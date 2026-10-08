package top.yoonheart.controller;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import top.yoonheart.config.EdgeDriverManager;
import top.yoonheart.po.Result;

import java.util.LinkedHashMap;
import java.util.Map;

/**
 * Edge 驱动检测与下载接口。
 *
 * <p>前端在「开始解析」前先调用 {@code /api/driver/status} 检查驱动状态，
 * 若 {@code ready=false} 则调用 {@code /api/driver/download} 触发下载，
 * 再通过 {@code /api/driver/progress} 轮询下载进度。</p>
 */
@RestController
@RequestMapping("/api/driver")
public class EdgeDriverController {

    private static final Logger log = LoggerFactory.getLogger(EdgeDriverController.class);

    @Autowired
    private EdgeDriverManager driverManager;

    /** 查询驱动状态：edge 版本、driver 是否就绪、当前下载进度 */
    @GetMapping("/status")
    public Result status() {
        String edgeVersion = driverManager.detectEdgeVersion();
        boolean ready = driverManager.isDriverReady();
        Map<String, Object> data = new LinkedHashMap<>();
        data.put("edgeVersion", edgeVersion);
        data.put("driverVersion", driverManager.driverRecordedVersion());
        data.put("ready", ready);
        data.put("downloading", driverManager.isDownloading());
        data.put("progress", driverManager.getProgress());
        data.put("driverDir", driverManager.driverDir().toString());
        return Result.success(data);
    }

    /** 触发驱动下载（若已就绪则直接返回） */
    @PostMapping("/download")
    public Result download() {
        try {
            if (driverManager.isDriverReady()) {
                return Result.success(Map.of("ready", true, "skipped", true));
            }
            // 异步下载：立即返回，进度通过 /progress 轮询
            new Thread(() -> {
                try {
                    String path = driverManager.ensureDriver();
                    if (path == null) {
                        log.warn("驱动下载：未检测到 Edge 版本");
                    } else {
                        log.info("驱动下载完成：{}", path);
                    }
                } catch (Exception e) {
                    log.error("驱动下载失败", e);
                }
            }, "edge-driver-download").start();
            return Result.success(Map.of("ready", false, "started", true));
        } catch (Exception e) {
            log.error("触发驱动下载失败", e);
            return Result.error("触发驱动下载失败: " + e.getMessage());
        }
    }

    /** 查询下载进度（前端轮询） */
    @GetMapping("/progress")
    public Result progress() {
        boolean ready = driverManager.isDriverReady();
        Map<String, Object> data = new LinkedHashMap<>();
        data.put("progress", driverManager.getProgress());
        data.put("downloading", driverManager.isDownloading());
        data.put("ready", ready);
        return Result.success(data);
    }
}
