package top.yoonheart.service;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import jakarta.annotation.PreDestroy;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;
import top.yoonheart.config.PythonExecutor;
import top.yoonheart.model.BrushTask;

import java.io.File;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

@Service
public class BrushTaskManager {

    private static final Logger log = LoggerFactory.getLogger(BrushTaskManager.class);
    private static final ObjectMapper MAPPER = new ObjectMapper();
    /** 进度轮询间隔 */
    private static final long PROGRESS_POLL_INTERVAL_MS = 500L;
    /**
     * 已结束任务的最大保留条数。
     *
     * <p>任务记录只用于任务运行期间查询进度，跑完就没有再查的价值了。
     * 之前 tasks 只增不减，跑得越多占的内存越多，这里给一个上限，
     * 超出后按开始时间从最旧的已结束任务开始清理。</p>
     */
    private static final int MAX_RETAINED_TASKS = 100;

    /** 任务ID -> 任务状态 */
    private final Map<String, BrushTask> tasks = new ConcurrentHashMap<>();
    /** 任务ID -> SseEmitter列表 */
    private final Map<String, Set<SseEmitter>> emitters = new ConcurrentHashMap<>();
    /** 线程池用于执行Python脚本 */
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
            // 把进度文件、停止文件路径并入配置，交给 Python 脚本使用
            String updatedConfigJson = withRuntimePaths(task);

            // 执行Python脚本（结果通过进度文件回传，此处仅记录输出尾部便于排查）
            String result = PythonExecutor.executePythonScript(
                    PythonExecutor.resolveScriptPath("scripts/wjx2.py"), updatedConfigJson);
            logTail(task.getTaskId(), result);

            // 标记任务完成（若用户已手动停止，保持STOPPED状态不被覆盖）
            if (task.getStatus() == BrushTask.TaskStatus.RUNNING) {
                task.setStatus(BrushTask.TaskStatus.COMPLETED);
            }
        } catch (Exception e) {
            log.error("任务 {} 执行失败", task.getTaskId(), e);
            // 用户手动停止不算失败
            if (task.getStatus() == BrushTask.TaskStatus.RUNNING) {
                task.setStatus(BrushTask.TaskStatus.FAILED);
            }
        } finally {
            if (watchdog != null) {
                watchdog.interrupt();
            }
            // 脚本已退出，最后再读一次进度文件，确保拿到完整结果
            // （Python 端进度写入有 1 秒节流，退出前会强制写一次）
            readProgressFile(task);
            // 通知所有监听者任务结束
            notifyTaskCompleted(task);
            // 任务已结束，回收该任务的 SSE 连接集合与过期任务记录，避免长时间运行后内存只涨不降
            emitters.remove(task.getTaskId());
            pruneFinishedTasks();
        }
    }

    /** 任务记录超出保留上限时，从最旧的已结束任务（非 RUNNING）开始清理 */
    private void pruneFinishedTasks() {
        int overflow = tasks.size() - MAX_RETAINED_TASKS;
        if (overflow <= 0) {
            return;
        }
        List<BrushTask> finished = tasks.values().stream()
                .filter(t -> t.getStatus() != BrushTask.TaskStatus.RUNNING)
                .sorted(Comparator.comparing(BrushTask::getStartTime))
                .toList();
        for (int i = 0; i < overflow && i < finished.size(); i++) {
            String taskId = finished.get(i).getTaskId();
            tasks.remove(taskId);
            emitters.remove(taskId);
        }
    }

    /** 应用关闭时释放执行 Python 脚本的线程池 */
    @PreDestroy
    public void shutdown() {
        executorService.shutdownNow();
    }

    /**
     * 在配置 JSON 中追加 progress_file 与 stop_file 两个运行时路径。
     * 通过 Jackson 解析后再序列化，避免手工拼接字符串导致的格式错误。
     */
    private String withRuntimePaths(BrushTask task) throws IOException {
        Map<String, Object> config = MAPPER.readValue(
                task.getConfigJson(), new TypeReference<Map<String, Object>>() {
                });
        config.put("progress_file", task.getProgressFilePath());
        config.put("stop_file", task.getStopFilePath());
        return MAPPER.writeValueAsString(config);
    }

    private void logTail(String taskId, String result) {
        if (result == null || result.isBlank()) {
            return;
        }
        String[] lines = result.strip().split("\\R");
        int from = Math.max(0, lines.length - 3);
        log.info("任务 {} 脚本输出尾部: {}", taskId, String.join(" | ", List.of(lines).subList(from, lines.length)));
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
                    readProgressFile(task);
                    // 推送进度更新
                    sendProgressUpdate(task.getTaskId(), task);

                    Thread.sleep(PROGRESS_POLL_INTERVAL_MS);
                } catch (InterruptedException e) {
                    Thread.currentThread().interrupt();
                    break;
                }
            }
        });
        watcher.setDaemon(true);
        watcher.start();
        return watcher;
    }

    /** 读取一次进度文件并更新任务计数（供 watchdog 与任务收尾复用） */
    private void readProgressFile(BrushTask task) {
        File progressFile = new File(task.getProgressFilePath());
        if (!progressFile.exists()) {
            return;
        }
        try {
            String content = Files.readString(progressFile.toPath(), StandardCharsets.UTF_8);
            parseProgressFile(task, content);
        } catch (Exception e) {
            // 文件可能正在被Python写入（读了一半），忽略本次读取
            log.trace("进度文件读取失败，跳过本次", e);
        }
    }

    /**
     * 解析进度文件内容，更新任务状态
     */
    private void parseProgressFile(BrushTask task, String content) {
        try {
            Map<String, Object> progress = MAPPER.readValue(
                    content, new TypeReference<Map<String, Object>>() {
                    });
            Object completed = progress.get("completedCount");
            if (completed instanceof Number number) {
                task.setCompletedCount(number.intValue());
            }
            Object failed = progress.get("failedCount");
            if (failed instanceof Number number) {
                task.setFailedCount(number.intValue());
            }
        } catch (Exception e) {
            // JSON 可能尚未写完，忽略本次解析
            log.trace("进度文件解析失败，跳过本次", e);
        }
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
        // 用户手动停止时，前端在调用停止接口前已主动 sse.close()，连接已断开。
        // 此时再推送必然触发连接中断异常（Spring 的异步错误处理会将其记录为 ERROR），
        // 而前端本就自行恢复界面、不依赖这次推送，因此直接跳过。
        if (task.getStatus() == BrushTask.TaskStatus.STOPPED) {
            return;
        }
        sendProgressUpdate(task.getTaskId(), task);
    }

    private void sendProgressUpdate(String taskId, BrushTask task) {
        Set<SseEmitter> emitterSet = emitters.get(taskId);
        if (emitterSet == null || emitterSet.isEmpty()) {
            return;
        }

        SseEmitter.SseEventBuilder event = SseEmitter.event()
                .name("progress")
                .data(task.getProgressJson());

        List<SseEmitter> toRemove = new ArrayList<>();
        for (SseEmitter emitter : emitterSet) {
            try {
                emitter.send(event);
            } catch (IOException e) {
                // 客户端已断开连接，静默移除即可
                toRemove.add(emitter);
            } catch (IllegalStateException e) {
                // emitter已完成或超时，静默移除
                toRemove.add(emitter);
            }
        }
        emitterSet.removeAll(toRemove);
    }

    // 停止任务
    public void stopTask(String taskId) {
        BrushTask task = tasks.get(taskId);
        if (task == null) {
            return;
        }
        task.setStatus(BrushTask.TaskStatus.STOPPED);

        // 创建停止标志文件
        try {
            File stopFile = new File(task.getStopFilePath());
            if (stopFile.createNewFile()) {
                log.info("已创建停止标志文件: {}", stopFile.getAbsolutePath());
            }
        } catch (IOException e) {
            log.error("创建停止标志文件失败: {}", task.getStopFilePath(), e);
        }
        // 前端在调用本接口前已主动 sse.close()，连接实际上已断开。
        // 这里直接丢弃该任务的 SSE 连接集合：watchdog 与任务收尾此后都取不到 emitter，
        // 不会再向已关闭的连接写入，从而避免 IOException（Spring 会将其记为 ERROR）。
        // 若前端之后重新订阅进度，registerEmitter 会重新创建集合。
        emitters.remove(taskId);
    }
}
