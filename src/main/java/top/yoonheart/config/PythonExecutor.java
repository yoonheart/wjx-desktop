package top.yoonheart.config;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

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

/**
 * Python 脚本执行器。
 *
 * <p>负责两件事：定位脚本真实路径（classpath / jar 内自动解压），以及启动 Python 进程并收集输出。</p>
 */
public final class PythonExecutor {

    private static final Logger log = LoggerFactory.getLogger(PythonExecutor.class);

    /** 时间控制模式下刷题可能长达 20 小时以上（1000 份 × 150 秒 ÷ 2 线程），设为 48 小时 */
    private static final long TIMEOUT_SECONDS = 172800L;
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
     * 未配置时依次探测 {@code python} / {@code py} / {@code python3}。</p>
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
            pythonCommand = (configured != null && !configured.isBlank())
                    ? configured.trim()
                    : probePythonCommand();
            log.info("使用 Python 解释器: {}", pythonCommand);
            return pythonCommand;
        }
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
     * 执行 Python 脚本并返回标准输出。
     *
     * <p>当首个参数是 JSON（以 <code>{</code> 开头）时，会先写入临时文件再传递路径，
     * 以规避 Windows 命令行长度限制；其余情况直接作为命令行参数传入。</p>
     *
     * @param scriptPath 脚本绝对路径
     * @param args       脚本参数
     * @return 脚本的标准输出
     */
    public static String executePythonScript(String scriptPath, String... args) {
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

            Process process = processBuilder.start();

            StringBuilder output = new StringBuilder();
            StringBuilder errorOutput = new StringBuilder();

            Thread outputThread = drainAsync(process.getInputStream(), output);
            Thread errorThread = drainAsync(process.getErrorStream(), errorOutput);

            if (!process.waitFor(TIMEOUT_SECONDS, TimeUnit.SECONDS)) {
                destroyProcess(process);
                throw new IllegalStateException("Python脚本执行超时");
            }

            outputThread.join();
            errorThread.join();

            int exitCode = process.exitValue();
            if (exitCode != 0) {
                String errorMessage = "Python脚本执行失败，退出码: " + exitCode
                        + ", 错误信息: " + errorOutput;
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

    /** 分阶段销毁进程：先优雅终止，超时后强制结束 */
    private static void destroyProcess(Process process) {
        process.destroy();
        try {
            if (process.waitFor(KILL_GRACE_SECONDS, TimeUnit.SECONDS)) {
                return;
            }
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
        if (isWindows()) {
            try {
                Process killProcess = Runtime.getRuntime().exec("taskkill /F /PID " + process.pid());
                killProcess.waitFor();
                return;
            } catch (Exception e) {
                log.debug("taskkill 失败，回退到 destroyForcibly", e);
            }
        }
        process.destroyForcibly();
    }
}
