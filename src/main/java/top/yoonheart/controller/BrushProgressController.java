package top.yoonheart.controller;

import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;
import top.yoonheart.po.Result;
import top.yoonheart.service.BrushTaskManager;

@RestController
@RequestMapping("/api/brush")
public class BrushProgressController {

    @Autowired
    private BrushTaskManager taskManager;

    /**
     * 订阅任务进度（SSE 长连接）。
     *
     * <p>任务不存在时返回 404，而不是先建立连接再说。EventSource 遇到 404 或非
     * {@code text/event-stream} 响应会直接「fail the connection」——置为 CLOSED
     * 且不会自动重连，正好避免为无效 taskId 挂上一条永不超时的服务端连接。</p>
     */
    @GetMapping(value = "/progress/{taskId}", produces = MediaType.TEXT_EVENT_STREAM_VALUE)
    public ResponseEntity<SseEmitter> getProgress(@PathVariable String taskId) {
        if (!taskManager.hasTask(taskId)) {
            return ResponseEntity.notFound().build();
        }
        SseEmitter emitter = new SseEmitter(0L);
        taskManager.registerEmitter(taskId, emitter);
        return ResponseEntity.ok(emitter);
    }

    /** 停止任务：写入停止标志文件，由 Python 脚本优雅退出 */
    @PostMapping("/stop/{taskId}")
    public Result stopTask(@PathVariable String taskId) {
        taskManager.stopTask(taskId);
        return Result.success("任务已停止");
    }
}
