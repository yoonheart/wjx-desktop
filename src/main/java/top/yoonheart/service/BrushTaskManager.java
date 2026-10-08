package top.yoonheart.service;

import org.springframework.stereotype.Service;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;
import top.yoonheart.model.BrushTask;
import top.yoonheart.config.PythonExecutor;

import java.util.Map;
import java.util.Set;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.io.File;
import java.io.FileWriter;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Paths;

@Service
public class BrushTaskManager {
    // 任务ID -> 任务状态
    private final Map<String, BrushTask> tasks = new ConcurrentHashMap<>();
    // 任务ID -> SseEmitter列表
    private final Map<String, Set<SseEmitter>> emitters = new ConcurrentHashMap<>();
    // 线程池用于执行Python脚本
    private final ExecutorService executorService = Executors.newCachedThreadPool();

    public BrushTask createTask(String taskId, String configJson, String url, int targetCount) {
        BrushTask task = new BrushTask(taskId, configJson, url, targetCount);
        tasks.put(taskId, task);

        // 在单独的线程中启动Python脚本
        executorService.submit(() -> executePythonScript(task));

        return task;
    }

    public BrushTask getTask(String taskId) {
        return tasks.get(taskId);
    }

    private void executePythonScript(BrushTask task) {
        // 启动进度监控线程（watchdog），定期读取Python写的进度文件
        Thread watchdog = startProgressWatcher(task);

        try {
            // 更新配置JSON，添加进度文件路径和停止文件路径
            String configJson = task.getConfigJson();
            // 将进度文件路径和停止文件路径添加到配置中（注意Windows路径反斜杠转义）
            String progressPath = task.getProgressFilePath().replace("\\", "\\\\");
            String stopPath = task.getStopFilePath().replace("\\", "\\\\");
            String updatedConfigJson = configJson.substring(0, configJson.length() - 1) +
                ",\"progress_file\":\"" + progressPath + "\"" +
                ",\"stop_file\":\"" + stopPath + "\"}";

            // 执行Python脚本
            String result = PythonExecutor.executePythonScript("./src/main/resources/scripts/wjx2.py", updatedConfigJson);

            // 解析结果并更新任务状态
            parseAndApplyResult(task, result);

            // 标记任务完成（若用户已手动停止，保持STOPPED状态不被覆盖）
            if (task.getStatus() == BrushTask.TaskStatus.RUNNING) {
                task.setStatus(BrushTask.TaskStatus.COMPLETED);
            }
        } catch (Exception e) {
            e.printStackTrace();
            // 用户手动停止不算失败
            if (task.getStatus() == BrushTask.TaskStatus.RUNNING) {
                task.setStatus(BrushTask.TaskStatus.FAILED);
            }
        } finally {
            // 停止进度监控线程
            if (watchdog != null) {
                watchdog.interrupt();
            }
            // 通知所有监听者任务结束
            notifyTaskCompleted(task);
        }
    }

    /**
     * 启动进度监控线程，定期读取Python脚本写出的进度文件
     */
    private Thread startProgressWatcher(BrushTask task) {
        Thread watcher = new Thread(() -> {
            while (!Thread.currentThread().isInterrupted()) {
                try {
                    // 如果任务已结束，停止监控
                    if (task.getStatus() != BrushTask.TaskStatus.RUNNING) {
                        break;
                    }

                    // 读取进度文件
                    File progressFile = new File(task.getProgressFilePath());
                    if (progressFile.exists()) {
                        try {
                            String content = new String(Files.readAllBytes(progressFile.toPath()), java.nio.charset.StandardCharsets.UTF_8);
                            parseProgressFile(task, content);
                            // 推送进度更新
                            sendProgressUpdate(task.getTaskId(), task);
                        } catch (Exception e) {
                            // 文件可能正在被Python写入（读了一半），忽略本次读取
                        }
                    }

                    // 每500ms检查一次
                    Thread.sleep(500);
                } catch (InterruptedException e) {
                    break;
                }
            }
        });
        watcher.setDaemon(true);
        watcher.start();
        return watcher;
    }

    /**
     * 解析进度文件内容，更新任务状态
     */
    private void parseProgressFile(BrushTask task, String content) {
        try {
            // 提取completedCount和failedCount
            String completedStr = extractJsonValue(content, "completedCount");
            String failedStr = extractJsonValue(content, "failedCount");
            String targetStr = extractJsonValue(content, "targetCount");

            if (completedStr != null) {
                task.setCompletedCount(Integer.parseInt(completedStr));
            }
            if (failedStr != null) {
                task.setFailedCount(Integer.parseInt(failedStr));
            }
        } catch (Exception e) {
            // 解析失败，忽略
        }
    }

    private void parseAndApplyResult(BrushTask task, String result) {
        try {
            // 解析Python脚本输出的JSON结果
            String[] lines = result.split("\\n");
            String jsonLine = null;

            for (int i = lines.length - 1; i >= 0; i--) {
                String line = lines[i].trim();
                if (line.startsWith("{") && line.endsWith("}")) {
                    jsonLine = line;
                    break;
                }
            }

            if (jsonLine != null) {
                // 简单解析JSON（避免引入额外的JSON库）
                String successCountStr = extractJsonValue(jsonLine, "successCount");
                String failureCountStr = extractJsonValue(jsonLine, "failureCount");

                if (successCountStr != null) {
                    task.setCompletedCount(Integer.parseInt(successCountStr));
                }
                if (failureCountStr != null) {
                    task.setFailedCount(Integer.parseInt(failureCountStr));
                }
            }
        } catch (Exception e) {
            e.printStackTrace();
        }
    }

    private String extractJsonValue(String json, String key) {
        String pattern = "\"" + key + "\":";
        int startIndex = json.indexOf(pattern);
        if (startIndex == -1) return null;

        startIndex += pattern.length();
        // 跳过空格
        while (startIndex < json.length() && json.charAt(startIndex) == ' ') {
            startIndex++;
        }

        // 查找值的结束位置
        int endIndex;
        if (json.charAt(startIndex) == '"') {
            // 字符串值
            startIndex++; // 跳过开始引号
            endIndex = json.indexOf('"', startIndex);
        } else {
            // 数字值
            endIndex = startIndex;
            while (endIndex < json.length() && (Character.isDigit(json.charAt(endIndex)) || json.charAt(endIndex) == '.')) {
                endIndex++;
            }
        }

        if (endIndex == -1) return null;
        return json.substring(startIndex, endIndex);
    }

    public void registerEmitter(String taskId, SseEmitter emitter) {
        emitters.computeIfAbsent(taskId, k -> ConcurrentHashMap.newKeySet()).add(emitter);

        // 发送初始状态
        BrushTask task = getTask(taskId);
        if (task != null) {
            try {
                emitter.send(SseEmitter.event()
                    .name("progress")
                    .data(task.getProgressJson()));
            } catch (IOException | IllegalStateException e) {
                // 发送失败说明连接已不可用，静默移除
                unregisterEmitter(taskId, emitter);
            }
        }

        // 设置清理回调
        emitter.onCompletion(() -> unregisterEmitter(taskId, emitter));
        emitter.onTimeout(() -> unregisterEmitter(taskId, emitter));
        emitter.onError(e -> unregisterEmitter(taskId, emitter));
    }

    public void unregisterEmitter(String taskId, SseEmitter emitter) {
        Set<SseEmitter> emitterSet = emitters.get(taskId);
        if (emitterSet != null) {
            emitterSet.remove(emitter);
        }
    }

    // 通知任务进度更新
    public void notifyTaskProgress(String taskId) {
        BrushTask task = tasks.get(taskId);
        if (task != null) {
            sendProgressUpdate(taskId, task);
        }
    }

    // 通知任务完成
    private void notifyTaskCompleted(BrushTask task) {
        sendProgressUpdate(task.getTaskId(), task);
    }

    private void sendProgressUpdate(String taskId, BrushTask task) {
        Set<SseEmitter> emitterSet = emitters.get(taskId);
        if (emitterSet != null) {
            SseEmitter.SseEventBuilder event = SseEmitter.event()
                .name("progress")
                .data(task.getProgressJson());

            // 创建要移除的发射器列表
            java.util.List<SseEmitter> toRemove = new java.util.ArrayList<>();

            for (SseEmitter emitter : emitterSet) {
                try {
                    emitter.send(event);
                } catch (IOException e) {
                    // 客户端已断开连接，静默移除即可（不打印堆栈）
                    toRemove.add(emitter);
                } catch (IllegalStateException e) {
                    // emitter已完成或超时，静默移除
                    toRemove.add(emitter);
                }
            }

            // 移除失效的发射器
            for (SseEmitter emitter : toRemove) {
                emitterSet.remove(emitter);
            }
        }
    }

    // 停止任务
    public void stopTask(String taskId) {
        BrushTask task = tasks.get(taskId);
        if (task != null) {
            task.setStatus(BrushTask.TaskStatus.STOPPED);

            // 创建停止标志文件
            try {
                File stopFile = new File(task.getStopFilePath());
                stopFile.createNewFile();
            } catch (IOException e) {
                e.printStackTrace();
            }
            // 注意：不向SSE推送停止事件——前端主动关闭连接并自行恢复界面，
            // 此时推送会因连接已断开而引发IOException报错
        }
    }
}