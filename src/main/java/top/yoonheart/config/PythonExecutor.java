package top.yoonheart.config;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import top.yoonheart.util.LogSanitizer;

import java.io.BufferedReader;
import java.io.File;
import java.io.IOException;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.net.URI;
import java.net.URISyntaxException;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.nio.file.StandardCopyOption;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.TimeUnit;
import java.util.function.Consumer;

/**
 * Python 脚本执行器。
 *
 * <p>负责两件事：定位脚本真实路径（classpath / jar 内自动解压），以及启动 Python 进程并收集输出。</p>
 */
public final class PythonExecutor {

    private static final Logger log = LoggerFactory.getLogger(PythonExecutor.class);

    /** 时间控制模式下刷题可能长达 20 小时以上（1000 份 × 150 秒 ÷ 2 线程），设为 48 小时 */
    private static final long TIMEOUT_SECONDS = 172800L;
    /** 问卷解析（scan.py）的正常耗时在十几秒，给 2 分钟足够，避免脚本卡死时请求长期挂起 */
    public static final long ANALYSIS_TIMEOUT_SECONDS = 120L;
    /** 优雅终止的等待时间 */
    private static final long KILL_GRACE_SECONDS = 5L;

    /** 解析得到的 Python 解释器命令，首次使用时探测并缓存 */
    private static volatile String pythonCommand;
    /** 从 jar 内解压出来的脚本缓存：resourcePath -> 临时文件路径 */
    private static final Map<String, Path> EXTRACTED_SCRIPTS = new ConcurrentHashMap<>();

    private PythonExecutor() {
    }

    // ==================== 脚本定位 ====================

    /**
     * 把 classpath 下的脚本解析为可执行的真实文件路径。
     *
     * <p>开发态直接返回 target/classes 下的路径；当程序被打成 jar 时，
     * 脚本会被解压到临时目录再执行。自动处理中文、空格等特殊字符。</p>
     *
     * @param resourcePath classpath 相对路径，如 {@code scripts/wjx2.py}
     * @return 脚本的绝对路径
     */
    public static String resolveScriptPath(String resourcePath) {
        URL url = PythonExecutor.class.getClassLoader().getResource(resourcePath);
        if (url == null) {
            throw new IllegalStateException("未找到脚本文件，请检查路径：" + resourcePath);
        }
        try {
            URI uri = url.toURI();
            if ("file".equals(uri.getScheme())) {
                return Paths.get(uri).toString();
            }
        } catch (URISyntaxException | IllegalArgumentException e) {
            // 落在异常分支说明脚本被打进了 jar，走解压逻辑
            log.debug("脚本不在文件系统中，将解压后执行: {}", resourcePath);
        }
        return extractScript(resourcePath, url).toString();
    }

    /** 把 jar 内的脚本释放到临时目录（同名资源只解压一次） */
    private static Path extractScript(String resourcePath, URL url) {
        return EXTRACTED_SCRIPTS.computeIfAbsent(resourcePath, key -> {
            String fileName = key.substring(key.lastIndexOf('/') + 1);
            try (InputStream in = url.openStream()) {
                Path tempFile = File.createTempFile("wjx_", "_" + fileName).toPath();
                Files.copy(in, tempFile, StandardCopyOption.REPLACE_EXISTING);
                tempFile.toFile().deleteOnExit();
                return tempFile;
            } catch (IOException e) {
                throw new IllegalStateException("释放脚本失败：" + key, e);
            }
        });
    }

    // ==================== 解释器定位 ====================

    /**
     * 解析可用的 Python 解释器命令。
     *
     * <p>优先读取系统属性 {@code wjx.python} 或环境变量 {@code WJX_PYTHON}；
     * 其次查找软件目录内打包的 Python（{@code ${wjx.app.dir}/python/python.exe}），
     * 保证打包后的软件在新机（无 Python 环境）也能运行脚本；
     * 最后才依次探测 {@code python} / {@code py} / {@code python3}。</p>
     */
    private static String resolvePythonCommand() {
        String cached = pythonCommand;
        if (cached != null) {
            return cached;
        }
        synchronized (PythonExecutor.class) {
            if (pythonCommand != null) {
                return pythonCommand;
            }
            String configured = System.getProperty("wjx.python");
            if (configured == null || configured.isBlank()) {
                configured = System.getenv("WJX_PYTHON");
            }
            if (configured != null && !configured.isBlank()) {
                pythonCommand = configured.trim();
            } else {
                // 软件目录内打包的 Python 优先
                String bundled = bundledPythonPath();
                pythonCommand = (bundled != null) ? bundled : probePythonCommand();
            }
            log.info("使用 Python 解释器: {}", pythonCommand);
            return pythonCommand;
        }
    }

    /** 软件目录内打包的 Python 解释器路径（不存在则返回 null） */
    private static String bundledPythonPath() {
        String appDir = System.getProperty("wjx.app.dir");
        if (appDir == null || appDir.isBlank()) {
            appDir = System.getenv("WJX_APP_DIR");
        }
        if (appDir == null || appDir.isBlank()) {
            return null;
        }
        String exeName = isWindows() ? "python.exe" : "bin/python";
        Path bundled = Paths.get(appDir, "python", exeName);
        if (Files.exists(bundled)) {
            return bundled.toString();
        }
        return null;
    }

    private static String probePythonCommand() {
        List<String> candidates = isWindows()
                ? List.of("python", "py", "python3")
                : List.of("python3", "python");
        for (String candidate : candidates) {
            try {
                Process process = new ProcessBuilder(candidate, "--version")
                        .redirectErrorStream(true)
                        .start();
                if (process.waitFor(5, TimeUnit.SECONDS) && process.exitValue() == 0) {
                    return candidate;
                }
            } catch (IOException | InterruptedException e) {
                if (e instanceof InterruptedException) {
                    Thread.currentThread().interrupt();
                }
                // 该候选命令不可用，继续试下一个
            }
        }
        log.warn("未探测到可用的 Python 解释器，回退到 \"python\"");
        return "python";
    }

    private static boolean isWindows() {
        return System.getProperty("os.name").toLowerCase().contains("win");
    }

    // ==================== 执行 ====================

    /**
     * 执行 Python 脚本并返回标准输出，超时时间沿用刷题用的 48 小时。
     *
     * @param scriptPath 脚本绝对路径
     * @param args       脚本参数
     * @return 脚本的标准输出
     */
    public static String executePythonScript(String scriptPath, String... args) {
        return executePythonScript(TIMEOUT_SECONDS, scriptPath, null, args);
    }

    /**
     * 执行 Python 脚本并返回标准输出，同时把进程句柄回调给调用方。
     *
     * <p>用于让任务管理器持有正在运行的 Python 进程，从而支持「强杀」——
     * 停止任务时若脚本卡死（写停止标志文件也无效），直接结束整棵进程树，
     * 顺带关掉它拉起的浏览器。</p>
     *
     * @param scriptPath      脚本绝对路径
     * @param processConsumer 进程启动后的回调（可为 null）
     * @param args            脚本参数
     * @return 脚本的标准输出
     */
    public static String executePythonScript(String scriptPath, Consumer<Process> processConsumer,
                                             String... args) {
        return executePythonScript(TIMEOUT_SECONDS, scriptPath, processConsumer, args);
    }

    /**
     * 执行 Python 脚本并返回标准输出，可指定超时。
     *
     * <p>当首个参数是 JSON（以 <code>{</code> 开头）时，会先写入临时文件再传递路径，
     * 以规避 Windows 命令行长度限制；其余情况直接作为命令行参数传入。</p>
     *
     * @param timeoutSeconds  超时秒数，超时后进程树会被强制结束
     * @param scriptPath      脚本绝对路径
     * @param processConsumer 进程启动后的回调（可为 null），用于外部持有进程以支持强杀
     * @param args            脚本参数
     * @return 脚本的标准输出
     */
    /** 执行 Python 脚本并返回标准输出，可指定超时（不关心进程句柄） */
    public static String executePythonScript(long timeoutSeconds, String scriptPath, String... args) {
        return executePythonScript(timeoutSeconds, scriptPath, null, args);
    }

    /** 执行 Python 脚本并返回标准输出（可指定超时并把进程句柄回调给调用方） */
    public static String executePythonScript(long timeoutSeconds, String scriptPath,
                                             Consumer<Process> processConsumer, String... args) {
        File tempConfigFile = null;
        try {
            List<String> command = new ArrayList<>();
            command.add(resolvePythonCommand());
            command.add(scriptPath);

            boolean useTempFile = args.length > 0
                    && args[0] != null
                    && args[0].trim().startsWith("{");

            if (useTempFile) {
                tempConfigFile = File.createTempFile("config", ".json");
                Files.write(tempConfigFile.toPath(), args[0].getBytes(StandardCharsets.UTF_8));
                command.add(tempConfigFile.getAbsolutePath());
            } else {
                Collections.addAll(command, args);
            }

            ProcessBuilder processBuilder = new ProcessBuilder(command);
            if (isWindows()) {
                // 处理 Windows 中文乱码
                processBuilder.environment().put("PYTHONIOENCODING", "utf-8");
            }

            // 注入软件目录内的 Edge 驱动路径：脚本优先用这个 driver，避免依赖 Selenium Manager 联网下载
            injectEdgeDriverPath(processBuilder);

            Process process = processBuilder.start();

            // 把进程句柄交给调用方（任务管理器），以便在停止/卡死时强杀整棵进程树
            if (processConsumer != null) {
                try {
                    processConsumer.accept(process);
                } catch (Exception e) {
                    log.warn("进程回调执行失败，忽略", e);
                }
            }

            StringBuilder output = new StringBuilder();
            StringBuilder errorOutput = new StringBuilder();

            Thread outputThread = drainAsync(process.getInputStream(), output);
            Thread errorThread = drainAsync(process.getErrorStream(), errorOutput);

            if (!process.waitFor(timeoutSeconds, TimeUnit.SECONDS)) {
                destroyProcessTree(process);
                throw new IllegalStateException("Python脚本执行超时（超过 " + timeoutSeconds + " 秒）");
            }

            outputThread.join();
            errorThread.join();

            int exitCode = process.exitValue();
            if (exitCode != 0) {
                // stderr 里可能带代理 IP，先打码再落日志 / 上抛：
                // 否则异常沿调用链传播时会被再写一次日志
                String errorMessage = "Python脚本执行失败，退出码: " + exitCode
                        + ", 错误信息: " + LogSanitizer.maskIp(errorOutput.toString());
                log.error(errorMessage);
                throw new IllegalStateException(errorMessage);
            }

            return output.toString();
        } catch (IOException e) {
            throw new IllegalStateException("执行Python脚本异常", e);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new IllegalStateException("执行Python脚本被中断", e);
        } finally {
            if (tempConfigFile != null && tempConfigFile.exists() && !tempConfigFile.delete()) {
                log.warn("临时配置文件删除失败: {}", tempConfigFile.getAbsolutePath());
            }
        }
    }

    /**
     * 把软件目录内的 Edge 驱动路径注入到子进程环境变量 {@code WJX_EDGE_DRIVER}。
     *
     * <p>驱动由 EdgeDriverManager 下载到软件目录，脚本通过该环境变量拿到路径，
     * 存在时才使用；不存在时置空，交给 Selenium Manager 兜底。</p>
     */
    private static void injectEdgeDriverPath(ProcessBuilder processBuilder) {
        try {
            String appDir = System.getProperty("wjx.app.dir");
            if (appDir == null || appDir.isBlank()) {
                appDir = System.getenv("WJX_APP_DIR");
            }
            if (appDir == null || appDir.isBlank()) {
                return;
            }
            String exeName = isWindows() ? "msedgedriver.exe" : "msedgedriver";
            Path driverPath = Paths.get(appDir, "driver", exeName);
            if (Files.exists(driverPath)) {
                processBuilder.environment().put("WJX_EDGE_DRIVER", driverPath.toString());
            }
        } catch (Exception e) {
            log.debug("注入 Edge 驱动路径失败，忽略", e);
        }
    }

    /** 异步读取某个流，避免子进程因缓冲区写满而阻塞 */
    private static Thread drainAsync(InputStream stream, StringBuilder sink) {
        Thread thread = new Thread(() -> {
            try (BufferedReader reader = new BufferedReader(
                    new InputStreamReader(stream, StandardCharsets.UTF_8))) {
                String line;
                while ((line = reader.readLine()) != null) {
                    sink.append(line).append('\n');
                }
            } catch (IOException e) {
                log.debug("读取子进程输出失败", e);
            }
        });
        thread.setDaemon(true);
        thread.start();
        return thread;
    }

    /**
     * 结束整棵进程树（Python 本体 + msedgedriver + 浏览器）。
     *
     * <p>跨平台实现：先记下所有子孙进程句柄（一旦父进程先退出，句柄就取不到了，
     * 所以必须在 destroy 之前收集），温柔终止父进程；若宽限期内没退干净，
     * 再逐个强杀子孙进程并强杀父进程。Windows 上额外用 {@code taskkill /F /T}
     * 兜底，确保 msedge/chrome 不会变成孤儿进程一直留在后台。</p>
     */
    public static void destroyProcessTree(Process process) {
        destroyProcessTree(process, KILL_GRACE_SECONDS);
    }

    /**
     * 结束整棵进程树，可指定「温柔终止」的宽限期。
     *
     * <p>宽限期传 0 表示立即强杀（用于关闭软件窗口这类「马上要退出、不等」的场景）。</p>
     *
     * @param graceSeconds 先 destroy 后等待的秒数，0 表示不等待直接强杀
     */
    public static void destroyProcessTree(Process process, long graceSeconds) {
        if (process == null) {
            return;
        }
        // 先收集子孙句柄（父进程退出后 descendants() 可能为空）
        List<ProcessHandle> descendants = new ArrayList<>();
        try {
            process.toHandle().descendants().forEach(descendants::add);
        } catch (Exception e) {
            log.debug("获取子进程句柄失败，忽略", e);
        }

        // 1. 温柔终止父进程
        process.destroy();
        boolean exited = false;
        try {
            exited = graceSeconds <= 0 ? !process.isAlive() : process.waitFor(graceSeconds, TimeUnit.SECONDS);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }

        // 2. 强杀所有子孙进程（浏览器 / 驱动），避免变成孤儿进程
        for (ProcessHandle handle : descendants) {
            try {
                if (handle.isAlive()) {
                    handle.destroyForcibly();
                }
            } catch (Exception e) {
                log.debug("强杀子进程失败，忽略", e);
            }
        }

        if (exited && descendants.isEmpty()) {
            return;
        }

        // 3. 父进程还在 / 有子孙残留：Windows 用 taskkill 兜底，其余平台 destroyForcibly
        if (isWindows()) {
            try {
                // 必须带 /T：msedgedriver 与 msedge 是 Python 的子进程，
                // 只杀 Python 本体的话浏览器会变成孤儿进程一直留在后台
                Process killProcess = Runtime.getRuntime().exec(
                        new String[]{"taskkill", "/F", "/T", "/PID", String.valueOf(process.pid())});
                killProcess.waitFor();
                return;
            } catch (Exception e) {
                log.debug("taskkill 失败，回退到 destroyForcibly", e);
            }
        }
        process.destroyForcibly();
    }
}
