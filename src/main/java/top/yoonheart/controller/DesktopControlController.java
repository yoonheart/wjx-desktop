package top.yoonheart.controller;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import top.yoonheart.desktop.DesktopLauncher;
import top.yoonheart.po.Result;
import top.yoonheart.service.BrushTaskManager;

/**
 * 桌面端控制接口。
 *
 * <p>网页里的「关闭软件」确认框中，用户点「关闭」后调用 {@code POST /api/desktop/close}：
 * 先强杀正在刷的问卷（含浏览器），再关闭窗口并退出软件。</p>
 *
 * <p>仅在桌面版（打包成软件、由 {@link DesktopLauncher} 启动）下有意义。</p>
 */
@RestController
@RequestMapping("/api/desktop")
public class DesktopControlController {

    private static final Logger log = LoggerFactory.getLogger(DesktopControlController.class);

    @Autowired
    private BrushTaskManager taskManager;

    /** 关闭软件：强杀正在刷的任务并退出 */
    @PostMapping("/close")
    public Result close() {
        // 异步执行：先让本次 HTTP 响应返回给页面，再强杀任务并退出，避免响应被中断
        Thread closer = new Thread(() -> {
            try {
                Thread.sleep(150L);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
            }
            try {
                taskManager.forceStopAllRunningTasks();
            } catch (Exception e) {
                log.warn("关闭软件前停止任务失败，忽略并继续退出", e);
            }
            DesktopLauncher.requestClose();
        }, "wjx-desktop-close");
        closer.setDaemon(true);
        closer.start();

        log.info("收到关闭软件请求，正在停止任务并退出");
        return Result.success("正在关闭");
    }
}
