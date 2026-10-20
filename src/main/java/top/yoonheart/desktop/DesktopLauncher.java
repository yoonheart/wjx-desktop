package top.yoonheart.desktop;

import me.friwi.jcefmaven.CefAppBuilder;
import me.friwi.jcefmaven.CefInitializationException;
import me.friwi.jcefmaven.EnumProgress;
import me.friwi.jcefmaven.UnsupportedPlatformException;
import org.cef.CefApp;
import org.cef.CefClient;
import org.cef.CefSettings;
import org.cef.browser.CefBrowser;
import org.cef.browser.CefFrame;
import org.cef.handler.CefLifeSpanHandlerAdapter;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.SpringApplication;
import org.springframework.context.ConfigurableApplicationContext;
import top.yoonheart.WjxSpringbootApplication;
import top.yoonheart.service.BrushTaskManager;

import javax.swing.*;
import java.awt.*;
import java.awt.event.WindowAdapter;
import java.awt.event.WindowEvent;
import java.io.File;
import java.io.IOException;

/**
 * JCEF 桌面启动器（不侵入原有代码，独立入口）。
 *
 * <p>职责：先以 Web 模式启动 Spring Boot 内嵌服务器，等它监听 {@code localhost:8080}
 * 后，用 JCEF 打开一个桌面窗口，把首页 {@code http://localhost:8080} 渲染进窗口。
 * 用户得到的是一个「桌面软件」体验，无需手动开浏览器。</p>
 *
 * <p>关键实现点：JCEF 的 {@code CefAppBuilder.build()} 会从 classpath 提取原生库并初始化
 * CEF，这一步必须在主线程完成（不能放进 Swing EDT，否则 EDT 卡死）。build 完成后再用
 * EDT 创建窗口。</p>
 *
 * <p>原 Spring Boot 应用仍可独立运行（走 {@link WjxSpringbootApplication}），
 * 本类只在打包成桌面软件时作为入口使用。</p>
 */
public final class DesktopLauncher {

    private static final Logger log = LoggerFactory.getLogger(DesktopLauncher.class);

    /** 内嵌服务器端口，与 application.yml 保持一致 */
    private static final int SERVER_PORT = 8080;

    /** 关闭软件的动作：由 createWindow 注册，供 /api/desktop/close 触发 */
    private static volatile Runnable closeAction;

    private DesktopLauncher() {
    }

    /**
     * 请求关闭软件（由网页里的「关闭软件」确认后经 /api/desktop/close 调用）。
     * 真正的关闭流程在 Swing 事件线程执行。
     */
    public static void requestClose() {
        Runnable action = closeAction;
        if (action != null) {
            SwingUtilities.invokeLater(action);
        } else {
            System.exit(0);
        }
    }

    public static void main(String[] args) {
        System.setProperty("java.awt.headless", "false");

        // 1. 启动 Spring Boot 内嵌服务器（阻塞，直到服务器就绪）
        //    强制固定端口：桌面软件必须用固定端口，避免被外部环境变量（如 SERVER__PORT）
        //    干扰导致端口漂移，JCEF 窗口加载的地址也会随之错乱。
        //    命令行参数优先级最高，能压过环境变量。
        java.util.List<String> fixedArgs = new java.util.ArrayList<>(java.util.Arrays.asList(args));
        fixedArgs.add("--server.port=" + SERVER_PORT);
        ConfigurableApplicationContext context =
                SpringApplication.run(WjxSpringbootApplication.class, fixedArgs.toArray(new String[0]));

        // 2. JCEF 初始化必须在主线程（非 EDT）进行，避免 EDT 卡死
        CefApp cefApp = null;
        try {
            System.out.println("[JCEF] 开始初始化 CEF...");
            System.out.flush();
            cefApp = buildCefApp();
            System.out.println("[JCEF] CEF 初始化完成，准备创建窗口");
            System.out.flush();
        } catch (Throwable e) {
            System.out.println("[JCEF] 初始化失败: " + e);
            e.printStackTrace();
            System.out.flush();
            log.error("JCEF 初始化失败，回退到系统浏览器", e);
            openInSystemBrowser();
            return;
        }

        final CefApp app = cefApp;
        final ConfigurableApplicationContext ctx = context;

        // 3. 用 EDT 创建窗口（Swing 组件必须在 EDT 上操作）
        SwingUtilities.invokeLater(() -> createWindow(app, ctx));
    }

    /** 构建并初始化 CEF 应用（主线程执行） */
    private static CefApp buildCefApp()
            throws IOException, UnsupportedPlatformException, InterruptedException, CefInitializationException {
        CefAppBuilder builder = new CefAppBuilder();
        builder.setInstallDir(new File(resolveAppDir(), "jcef"));
        // 打印进度，便于定位首次初始化卡在哪一步
        builder.setProgressHandler((state, percent) -> {
            if (state == EnumProgress.INITIALIZED) {
                log.info("JCEF 初始化完成");
            } else if (percent == EnumProgress.NO_ESTIMATION) {
                log.info("JCEF 初始化阶段：{}", state);
            } else {
                log.info("JCEF 初始化：{} {:.0f}%", state, percent);
            }
        });

        CefSettings settings = builder.getCefSettings();
        settings.windowless_rendering_enabled = false;
        settings.cache_path = new File(resolveAppDir(), "jcef/cache").getAbsolutePath();
        settings.log_severity = CefSettings.LogSeverity.LOGSEVERITY_WARNING;

        // 彻底禁用 Chromium 的 HTTP 缓存。
        // 否则内嵌浏览器会把 /static/js/*.js 缓存到磁盘，换了新 jar 之后界面仍是旧版
        // （表现为「代码明明改了、软件却还是老样子」）。前端页面就在本机 8080，
        // 禁用缓存对性能没有实际影响，却能避免这类陈旧资源问题。
        // 注：jcefmaven 的 CefAppBuilder 没有 addSwitch()，附加命令行开关走 addJcefArgs()。
        builder.addJcefArgs("--disable-http-cache");

        return builder.build();
    }

    /** 创建并显示桌面窗口（EDT 执行） */
    private static void createWindow(CefApp cefApp, ConfigurableApplicationContext context) {
        CefClient client = cefApp.createClient();

        // 所有 target="_blank" 的外部链接（教程里的「51 代理」、首页的 GitHub 等）
        // 一律交给系统默认浏览器打开。JCEF 默认会尝试在软件内部再开一个窗口，
        // 结果是「点了没反应」或把软件窗口导航走；这里统一拦下来，
        // 软件窗口永远停在本地页面上。
        client.addLifeSpanHandler(new CefLifeSpanHandlerAdapter() {
            @Override
            public boolean onBeforePopup(CefBrowser browser, CefFrame frame,
                                        String targetUrl, String targetFrameName) {
                openExternal(targetUrl);
                return true; // 取消软件内部的弹窗
            }
        });

        CefBrowser browser = client.createBrowser("http://localhost:" + SERVER_PORT + "/", false, false);

        JFrame frame = new JFrame("问卷星助手");
        frame.setDefaultCloseOperation(JFrame.DO_NOTHING_ON_CLOSE);
        // 窗口图标：同时作用于标题栏与任务栏
        Image icon = loadAppIcon();
        if (icon != null) {
            frame.setIconImage(icon);
        }
        frame.getContentPane().add(browser.getUIComponent(), BorderLayout.CENTER);
        frame.setSize(1100, 760);
        frame.setLocationRelativeTo(null);
        // 注册关闭动作：供 /api/desktop/close 触发「强杀任务 + 退出软件」
        closeAction = () -> shutdown(cefApp, browser, frame, context);
        frame.addWindowListener(new WindowAdapter() {
            @Override
            public void windowClosing(WindowEvent e) {
                // 无运行中的任务：直接关闭
                if (!hasRunningTask(context)) {
                    shutdown(cefApp, browser, frame, context);
                    return;
                }
                // 正在刷问卷：交给网页里的软件自带确认框。
                // 用户点「关闭」后，页面会调用 /api/desktop/close → 强杀任务并退出软件。
                String pageUrl = browser.getURL();
                if (pageUrl == null || pageUrl.isBlank()) {
                    pageUrl = "http://localhost:" + SERVER_PORT + "/";
                }
                browser.executeJavaScript(
                        "(function(){"
                                + "if (typeof window.__wjxConfirmCloseSoftware === 'function') {"
                                + "  window.__wjxConfirmCloseSoftware();"
                                + "} else {"
                                + "  fetch('/api/desktop/close', {method:'POST'});"
                                + "}"
                                + "})();",
                        pageUrl, 0);
            }
        });
        frame.setVisible(true);
        browser.loadURL("http://localhost:" + SERVER_PORT + "/");

        log.info("JCEF 桌面窗口已启动，加载 http://localhost:{}", SERVER_PORT);
    }

    /** 是否有正在刷写的任务（取不到任务管理器时按「没有」处理，直接关闭窗口） */
    private static boolean hasRunningTask(ConfigurableApplicationContext context) {
        try {
            BrushTaskManager taskManager = context.getBean(BrushTaskManager.class);
            return taskManager != null && taskManager.hasRunningTask();
        } catch (Exception e) {
            log.warn("获取任务管理器失败，按「无运行任务」处理", e);
            return false;
        }
    }

    /**
     * 从 classpath 加载应用图标（icons/app.ico）。
     *
     * <p>Java 的 ImageIO 不支持 ICO 格式，而本项目的 ico 内嵌的是标准 PNG 图像
     * （多尺寸中最大为 256x256），因此解析 ICO 目录结构、取出最大的那张 PNG
     * 交给 ImageIO 解码。解析失败返回 null，窗口回落为默认图标，不影响启动。</p>
     */
    private static Image loadAppIcon() {
        try (java.io.InputStream in = DesktopLauncher.class
                .getClassLoader().getResourceAsStream("icons/app.ico")) {
            if (in == null) {
                log.warn("未找到 icons/app.ico，窗口使用默认图标");
                return null;
            }
            byte[] ico = in.readAllBytes();
            if (ico.length < 6) {
                return null;
            }
            int count = (ico[4] & 0xFF) | ((ico[5] & 0xFF) << 8);
            int bestSize = -1;
            int bestOffset = -1;
            int bestLength = 0;
            for (int i = 0; i < count; i++) {
                int off = 6 + i * 16;
                if (off + 16 > ico.length) {
                    break;
                }
                // width/height 为 0 表示 256
                int w = ico[off] & 0xFF;
                int h = ico[off + 1] & 0xFF;
                int length = (ico[off + 8] & 0xFF) | ((ico[off + 9] & 0xFF) << 8)
                        | ((ico[off + 10] & 0xFF) << 16) | ((ico[off + 11] & 0xFF) << 24);
                int offset = (ico[off + 12] & 0xFF) | ((ico[off + 13] & 0xFF) << 8)
                        | ((ico[off + 14] & 0xFF) << 16) | ((ico[off + 15] & 0xFF) << 24);
                int size = Math.max(w, h);
                if (size > bestSize && offset + length <= ico.length) {
                    bestSize = size;
                    bestOffset = offset;
                    bestLength = length;
                }
            }
            if (bestOffset < 0) {
                return null;
            }
            byte[] image = java.util.Arrays.copyOfRange(ico, bestOffset, bestOffset + bestLength);
            return new javax.swing.ImageIcon(
                    java.awt.Toolkit.getDefaultToolkit().createImage(image)).getImage();
        } catch (Exception e) {
            log.warn("加载应用图标失败，窗口使用默认图标", e);
            return null;
        }
    }

    /** 解析软件目录（与 EdgeDriverManager 保持一致） */
    private static File resolveAppDir() {
        String dir = System.getProperty("wjx.app.dir");
        if (dir == null || dir.isBlank()) {
            dir = System.getenv("WJX_APP_DIR");
        }
        if (dir == null || dir.isBlank()) {
            dir = System.getProperty("user.home") + File.separator + ".wjx";
        }
        return new File(dir).getAbsoluteFile();
    }

    /** 用系统默认浏览器打开一个外部链接；仅接受 http/https，成功返回 true */
    private static boolean openExternal(String url) {
        if (url == null || url.isBlank()) {
            return false;
        }
        String target = url.trim();
        String lower = target.toLowerCase();
        if (!lower.startsWith("http://") && !lower.startsWith("https://")) {
            log.warn("拒绝用系统浏览器打开非 http(s) 链接：{}", target);
            return false;
        }
        try {
            Desktop.getDesktop().browse(java.net.URI.create(target));
            return true;
        } catch (Exception e) {
            log.warn("调用系统浏览器打开链接失败：{}", target, e);
            return false;
        }
    }

    /** JCEF 不可用时，退回用系统浏览器打开（保证可用性） */
    private static void openInSystemBrowser() {
        String home = "http://localhost:" + SERVER_PORT + "/";
        if (!openExternal(home)) {
            log.warn("打开系统浏览器失败，请手动访问 {}", home);
        }
    }

    private static void shutdown(CefApp cefApp, CefBrowser browser, JFrame frame,
                                 ConfigurableApplicationContext context) {
        frame.dispose();
        if (browser != null) {
            browser.close(true);
        }
        if (cefApp != null) {
            cefApp.dispose();
        }
        context.close();
        System.exit(0);
    }
}
