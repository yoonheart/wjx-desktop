package top.yoonheart.controller;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import top.yoonheart.config.PythonExecutor;
import top.yoonheart.po.Result;

@RestController
@RequestMapping("/api/analysis")
public class AnalysisController {

    private static final Logger log = LoggerFactory.getLogger(AnalysisController.class);

    @GetMapping
    public Result analysis(String url) {
        // 1. 先校验前端传的url参数非空
        if (url == null || url.trim().isEmpty()) {
            return Result.error("问卷网址不能为空，请检查参数");
        }

        try {
            // 2. 定位脚本真实路径（由 PythonExecutor 统一处理中文/空格/打包进 jar 的情况）
            String scriptPath = PythonExecutor.resolveScriptPath("scripts/scan.py");

            // 3. 执行Python脚本并返回结果
            String result = PythonExecutor.executePythonScript(scriptPath, url);
            return Result.success(result);
        } catch (Exception e) {
            log.error("问卷解析失败: {}", url, e);
            return Result.error("问卷解析失败: " + e.getMessage());
        }
    }
}
