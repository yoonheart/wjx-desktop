package top.yoonheart.model;

import java.time.LocalDateTime;
import java.util.concurrent.atomic.AtomicInteger;

public class BrushTask {
    public enum TaskStatus {RUNNING, COMPLETED, STOPPED, FAILED}

    private final String taskId;
    private final String configJson;
    private final String url;
    private final int targetCount;
    private volatile TaskStatus status = TaskStatus.RUNNING;
    private final LocalDateTime startTime = LocalDateTime.now();
    private final AtomicInteger completedCount = new AtomicInteger(0);
    private final AtomicInteger failedCount = new AtomicInteger(0);
    private final String progressFilePath;
    private final String stopFilePath;

    public BrushTask(String taskId, String configJson, String url, int targetCount) {
        this.taskId = taskId;
        this.configJson = configJson;
        this.url = url;
        this.targetCount = targetCount;
        this.progressFilePath = System.getProperty("java.io.tmpdir") + "/wjx_progress_" + taskId + ".json";
        this.stopFilePath = System.getProperty("java.io.tmpdir") + "/wjx_stop_" + taskId + ".flag";
    }

    // Getters
    public String getTaskId() {
        return taskId;
    }

    public String getConfigJson() {
        return configJson;
    }

    public String getUrl() {
        return url;
    }

    public int getTargetCount() {
        return targetCount;
    }

    public TaskStatus getStatus() {
        return status;
    }

    public void setStatus(TaskStatus status) {
        this.status = status;
    }

    public LocalDateTime getStartTime() {
        return startTime;
    }

    public int getCompletedCount() {
        return completedCount.get();
    }

    public void setCompletedCount(int count) {
        this.completedCount.set(count);
    }

    public int getFailedCount() {
        return failedCount.get();
    }

    public void setFailedCount(int count) {
        this.failedCount.set(count);
    }

    public int getTotalProcessed() {
        return completedCount.get() + failedCount.get();
    }

    public int getPercentComplete() {
        if (targetCount <= 0) return 0;
        // 进度只按成功填写份数计算，失败尝试不计入进度
        return Math.min(100, (int) ((double) completedCount.get() / targetCount * 100));
    }

    public String getProgressFilePath() {
        return progressFilePath;
    }

    public String getStopFilePath() {
        return stopFilePath;
    }

    // 获取进度JSON字符串
    public String getProgressJson() {
        StringBuilder sb = new StringBuilder();
        sb.append("{");
        sb.append("\"taskId\":\"").append(taskId).append("\",");
        sb.append("\"status\":\"").append(status.name()).append("\",");
        sb.append("\"completed\":").append(getCompletedCount()).append(",");
        sb.append("\"failed\":").append(getFailedCount()).append(",");
        sb.append("\"total\":").append(targetCount).append(",");
        sb.append("\"percent\":").append(getPercentComplete()).append(",");
        sb.append("\"elapsed\":\"").append(getElapsedTime()).append("\"");
        sb.append("}");
        return sb.toString();
    }

    private String getElapsedTime() {
        long elapsedSeconds = java.time.Duration.between(startTime, LocalDateTime.now()).getSeconds();
        long hours = elapsedSeconds / 3600;
        long minutes = (elapsedSeconds % 3600) / 60;
        long secs = elapsedSeconds % 60;
        return String.format("%02d:%02d:%02d", hours, minutes, secs);
    }
}