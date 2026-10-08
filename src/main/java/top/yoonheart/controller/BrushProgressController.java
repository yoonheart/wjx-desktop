package top.yoonheart.controller;

import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.http.MediaType;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;
import top.yoonheart.service.BrushTaskManager;

@RestController
@RequestMapping("/api/brush")
public class BrushProgressController {

    @Autowired
    private BrushTaskManager taskManager;

    @GetMapping(value = "/progress/{taskId}", produces = MediaType.TEXT_EVENT_STREAM_VALUE)
    public SseEmitter getProgress(@PathVariable String taskId) {
        SseEmitter emitter = new SseEmitter(0L); // 无超时限制

        taskManager.registerEmitter(taskId, emitter);

        return emitter;
    }

    @PostMapping("/stop/{taskId}")
    public String stopTask(@PathVariable String taskId) {
        taskManager.stopTask(taskId);
        return "任务已停止";
    }
}