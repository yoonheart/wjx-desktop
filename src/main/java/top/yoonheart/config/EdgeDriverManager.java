package top.yoonheart.config;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

import java.io.BufferedReader;
import java.io.File;
import java.io.IOException;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URI;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.nio.file.StandardCopyOption;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import java.util.zip.ZipEntry;
import java.util.zip.ZipInputStream;

/**
 * Edge 浏览器驱动的检测与下载。
 *
 * <p>负责三件事：</p>
 * <ol>
 *   <li>检测本机 Edge 浏览器的版本号（Windows 下读注册表或可执行文件属性）。</li>
 *   <li>检测软件目录中是否已有匹配的 msedgedriver。</li>
 *   <li>版本不符或缺失时，从国内镜像下载对应版本驱动到软件目录，并回传下载进度。</li>
 * </ol>
 *
 * <p>驱动统一放在「软件目录」下（通过系统属性 {@code wjx.app.dir} 指定，未指定时回退到
 * 用户主目录下的 .wjx 目录），以保证打包后仍能找到。目录结构：</p>
 * <pre>
 *   ${wjx.app.dir}/driver/msedgedriver.exe        （Windows）
 *   ${wjx.app.dir}/driver/version.txt             （记录下载时对应的 Edge 版本）
 * </pre>
 */
@Component
public class EdgeDriverManager {

    private static final Logger log = LoggerFactory.getLogger(EdgeDriverManager.class);

    /** 阿里云 npmmirror 提供的 Edge 驱动镜像（国内快，binary 名为 edgedriver） */
    private static final String MSEDGE_DRIVER_BASE_URL =
            "https://registry.npmmirror.com/-/binary/edgedriver/";

    /** 各下载任务的进度（版本号 -> 0~100） */
    private final Map<String, Integer> downloadProgress = new ConcurrentHashMap<>();

    /**
     * 解析软件目录：优先系统属性 {@code wjx.app.dir}，其次环境变量 {@code WJX_APP_DIR}，
     * 最后回退到 {@code ~/.wjx}。
     */
    public Path appDir() {
        String dir = System.getProperty("wjx.app.dir");
        if (dir == null || dir.isBlank()) {
            dir = System.getenv("WJX_APP_DIR");
        }
        if (dir == null || dir.isBlank()) {
            dir = System.getProperty("user.home") + File.separator + ".wjx";
        }
        return Paths.get(dir).toAbsolutePath().normalize();
    }

    /** 驱动所在目录 */
    public Path driverDir() {
        return appDir().resolve("driver");
    }

    /** 驱动文件路径（Windows 下为 msedgedriver.exe） */
    public Path driverExecutable() {
        String name = isWindows() ? "msedgedriver.exe" : "msedgedriver";
        return driverDir().resolve(name);
    }

    /** 记录驱动对应 Edge 版本的标记文件 */
    private Path versionMarker() {
        return driverDir().resolve("version.txt");
    }

    private boolean isWindows() {
        return System.getProperty("os.name").toLowerCase().contains("win");
    }

    // ==================== 版本检测 ====================

    /**
     * 检测本机 Edge 浏览器版本号。
     *
     * <p>优先通过文件系统探测：Edge 安装目录下有个以版本号命名的子目录
     * （如 {@code ...\Edge\Application\154.0.4258.62\msedge.exe}），直接读目录名即可，
     * 不依赖注册表、不依赖 reg.exe，最稳。</p>
     *
     * <p>读不到目录时依次退回注册表、msedge --version。返回形如
     * {@code 154.0.4258.62} 的字符串；检测失败返回 null。</p>
     */
    public String detectEdgeVersion() {
        if (isWindows()) {
            // 1. 文件系统探测：读 Application/<version>/ 目录名
            String v = detectEdgeVersionFromInstallDir();
            if (v != null) {
                return v;
            }
            // 2. 注册表
            v = readRegistryEdgeVersion();
            if (v != null) {
                return v;
            }
        }
        return probeEdgeVersionViaProcess();
    }

    /** 从 Edge 安装目录的版本号子目录名探测版本 */
    private String detectEdgeVersionFromInstallDir() {
        String[] bases = {
                System.getenv("PROGRAMFILES(X86)") + "\\Microsoft\\Edge\\Application",
                System.getenv("PROGRAMFILES") + "\\Microsoft\\Edge\\Application",
                System.getenv("LOCALAPPDATA") + "\\Microsoft\\Edge\\Application"
        };
        Pattern versionDir = Pattern.compile("^(\\d+\\.\\d+\\.\\d+\\.\\d+)$");
        for (String base : bases) {
            if (base == null || base.startsWith("null")) {
                continue;
            }
            File dir = new File(base);
            if (!dir.isDirectory()) {
                continue;
            }
            File[] children = dir.listFiles(File::isDirectory);
            if (children == null) {
                continue;
            }
            for (File child : children) {
                if (versionDir.matcher(child.getName()).matches()
                        && new File(child, "msedge.exe").exists()) {
                    return child.getName();
                }
            }
        }
        return null;
    }

    /** 读注册表拿 Edge 版本（优先 HKCU，再 HKLM） */
    private String readRegistryEdgeVersion() {
        String[] queries = {
                "reg query \"HKCU\\Software\\Microsoft\\Edge\\BLBeacon\" /v version",
                "reg query \"HKLM\\SOFTWARE\\Microsoft\\Edge\\BLBeacon\" /v version",
                "reg query \"HKLM\\SOFTWARE\\WOW6432Node\\Microsoft\\Edge\\BLBeacon\" /v version"
        };
        Pattern versionPattern = Pattern.compile("version\\s+REG_SZ\\s+([\\d.]+)", Pattern.CASE_INSENSITIVE);
        for (String query : queries) {
            try {
                Process p = new ProcessBuilder("cmd", "/c", query).redirectErrorStream(true).start();
                String out = readAll(p.getInputStream());
                p.waitFor();
                Matcher m = versionPattern.matcher(out);
                if (m.find()) {
                    return m.group(1).trim();
                }
            } catch (Exception e) {
                log.trace("读注册表失败，尝试下一条：{}", query, e);
            }
        }
        return null;
    }

    /** 兜底：运行 msedge --version 读取版本 */
    private String probeEdgeVersionViaProcess() {
        String[] commands = isWindows()
                ? new String[]{"cmd", "/c", "msedge", "--version"}
                : new String[]{"microsoft-edge", "--version"};
        Pattern versionPattern = Pattern.compile("([\\d.]+)");
        try {
            Process p = new ProcessBuilder(commands).redirectErrorStream(true).start();
            String out = readAll(p.getInputStream());
            p.waitFor();
            Matcher m = versionPattern.matcher(out);
            if (m.find()) {
                return m.group(1).trim();
            }
        } catch (Exception e) {
            log.debug("通过进程探测 Edge 版本失败", e);
        }
        return null;
    }

    /** 读取驱动版本标记文件，判断当前 driver 对应哪个 Edge 版本 */
    public String driverRecordedVersion() {
        try {
            Path marker = versionMarker();
            if (Files.exists(marker)) {
                return Files.readString(marker, StandardCharsets.UTF_8).trim();
            }
        } catch (IOException e) {
            log.trace("读取驱动版本标记失败", e);
        }
        return null;
    }

    /**
     * 判断当前驱动是否可用：driver 文件存在，且记录版本与 Edge 版本一致。
     */
    public boolean isDriverReady() {
        String edgeVersion = detectEdgeVersion();
        if (edgeVersion == null) {
            return false;
        }
        if (!Files.exists(driverExecutable())) {
            return false;
        }
        String recorded = driverRecordedVersion();
        return edgeVersion.equals(recorded);
    }

    // ==================== 下载 ====================

    /**
     * 下载与当前 Edge 版本匹配的驱动（若已就绪则跳过）。
     *
     * <p>下载源为阿里云 npmmirror 镜像，文件为 zip，下载后解压出 msedgedriver.exe
     * 并写入版本标记。下载期间通过 {@link #getProgress()} 回传百分比。</p>
     *
     * @return 驱动可执行文件的绝对路径；Edge 版本检测失败返回 null
     */
    public String ensureDriver() throws IOException {
        String edgeVersion = detectEdgeVersion();
        if (edgeVersion == null) {
            log.warn("未检测到 Edge 浏览器版本，无法匹配驱动");
            return null;
        }

        // 已就绪直接返回
        if (Files.exists(driverExecutable()) && edgeVersion.equals(driverRecordedVersion())) {
            return driverExecutable().toString();
        }

        downloadProgress.put(edgeVersion, 0);
        try {
            Files.createDirectories(driverDir());

            String downloadUrl = MSEDGE_DRIVER_BASE_URL + edgeVersion + "/edgedriver_win64.zip";
            if (!isWindows()) {
                downloadUrl = MSEDGE_DRIVER_BASE_URL + edgeVersion + "/edgedriver_linux64.zip";
            }

            Path zipPath = driverDir().resolve("msedgedriver.zip");
            downloadFile(downloadUrl, zipPath, edgeVersion);

            // 解压并替换驱动
            extractDriver(zipPath, driverExecutable());

            // 记录版本
            Files.writeString(versionMarker(), edgeVersion, StandardCharsets.UTF_8);

            // 删除临时 zip
            Files.deleteIfExists(zipPath);

            return driverExecutable().toString();
        } finally {
            downloadProgress.remove(edgeVersion);
        }
    }

    /**
     * 下载文件并更新进度。支持 HTTP 重定向（镜像站常会 302 到真实 CDN）。
     */
    private void downloadFile(String urlStr, Path dest, String version) throws IOException {
        HttpURLConnection conn = openWithRedirects(urlStr);
        try (InputStream in = conn.getInputStream()) {
            long total = conn.getContentLengthLong();
            byte[] buf = new byte[64 * 1024];
            long downloaded = 0;
            int n;
            try (OutputStream out = Files.newOutputStream(dest)) {
                while ((n = in.read(buf)) != -1) {
                    out.write(buf, 0, n);
                    downloaded += n;
                    if (total > 0) {
                        int pct = (int) (downloaded * 100 / total);
                        downloadProgress.put(version, Math.min(100, pct));
                    }
                }
            }
        } finally {
            conn.disconnect();
        }
        downloadProgress.put(version, 100);
    }

    /** 打开连接并跟随 302/301 重定向 */
    private HttpURLConnection openWithRedirects(String urlStr) throws IOException {
        String current = urlStr;
        for (int i = 0; i < 10; i++) {
            HttpURLConnection conn = (HttpURLConnection) URI.create(current).toURL().openConnection();
            conn.setInstanceFollowRedirects(false);
            conn.setConnectTimeout(15000);
            conn.setReadTimeout(60000);
            conn.setRequestProperty("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64)");
            int code = conn.getResponseCode();
            if (code == HttpURLConnection.HTTP_MOVED_PERM
                    || code == HttpURLConnection.HTTP_MOVED_TEMP
                    || code == HttpURLConnection.HTTP_SEE_OTHER
                    || code == 307 || code == 308) {
                String location = conn.getHeaderField("Location");
                conn.disconnect();
                if (location == null) {
                    throw new IOException("重定向缺少 Location 头");
                }
                // 处理相对路径
                current = URI.create(current).resolve(location).toString();
                continue;
            }
            if (code >= 400) {
                conn.disconnect();
                throw new IOException("下载失败，HTTP " + code + "：" + current);
            }
            return conn;
        }
        throw new IOException("重定向次数过多：" + urlStr);
    }

    /** 从 zip 中解压出驱动可执行文件 */
    private void extractDriver(Path zipPath, Path target) throws IOException {
        try (ZipInputStream zis = new ZipInputStream(Files.newInputStream(zipPath))) {
            ZipEntry entry;
            boolean found = false;
            while ((entry = zis.getNextEntry()) != null) {
                String name = entry.getName();
                if (name.endsWith("msedgedriver.exe") || name.endsWith("msedgedriver")) {
                    try (OutputStream out = Files.newOutputStream(target)) {
                        zis.transferTo(out);
                    }
                    found = true;
                    break;
                }
            }
            if (!found) {
                throw new IOException("压缩包中未找到 msedgedriver");
            }
        }
    }

    /** 当前下载进度百分比（0~100），无任务时返回 -1 */
    public int getProgress() {
        if (downloadProgress.isEmpty()) {
            return -1;
        }
        int sum = 0;
        for (int v : downloadProgress.values()) {
            sum = Math.max(sum, v);
        }
        return sum;
    }

    /** 是否有下载进行中 */
    public boolean isDownloading() {
        return !downloadProgress.isEmpty();
    }

    private String readAll(InputStream in) throws IOException {
        StringBuilder sb = new StringBuilder();
        try (BufferedReader reader = new BufferedReader(
                new InputStreamReader(in, StandardCharsets.UTF_8))) {
            String line;
            while ((line = reader.readLine()) != null) {
                sb.append(line).append('\n');
            }
        }
        return sb.toString();
    }
}
