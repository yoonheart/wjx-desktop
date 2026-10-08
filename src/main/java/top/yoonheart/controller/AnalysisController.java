package top.yoonheart.controller;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import top.yoonheart.config.PythonExecutor;
import top.yoonheart.po.Result;

import java.net.URI;
import java.util.Locale;
import java.util.Set;

@RestController
@RequestMapping("/api/analysis")
public class AnalysisController {

    private static final Logger log = LoggerFactory.getLogger(AnalysisController.class);

    /** 允许解析的问卷星主机名（与首页 isWjxUrl 保持一致） */
    private static final Set<String> ALLOWED_WJX_HOSTS = Set.of("v.wjx.cn", "www.wjx.cn");

    @GetMapping
    public Result analysis(String url) {
        // 1. 先校验前端传的url参数非空
        if (url == null || url.trim().isEmpty()) {
            return Result.error("问卷网址不能为空，请检查参数");
        }

        // 2. 服务端同样要校验域名：首页的 isWjxUrl 只是界面上的拦截，
        //    直接请求 /api/analysis?url=... 可以绕过它，让服务端起浏览器去访问任意地址。
        if (!isAllowedWjxUrl(url)) {
            return Result.error("仅支持 v.wjx.cn / www.wjx.cn 的问卷链接，请检查参数");
        }

        try {
            // 3. 定位脚本真实路径（由 PythonExecutor 统一处理中文/空格/打包进 jar 的情况）
            String scriptPath = PythonExecutor.resolveScriptPath("scripts/scan.py");

            // 4. 执行Python脚本并返回结果。解析单独用短超时：
            //    PythonExecutor 默认的 48 小时是给刷题任务准备的，用在解析上会让请求长期挂起。
            String result = PythonExecutor.executePythonScript(
                    PythonExecutor.ANALYSIS_TIMEOUT_SECONDS, scriptPath, url);
            return Result.success(result);
        } catch (Exception e) {
            // 不把完整问卷链接写进日志：路径里含问卷 id，属于用户数据。
            // 只保留主机名，足够定位问题。
            log.error("问卷解析失败 [host={}]: {}", safeHost(url), e.getMessage());
            return Result.error("问卷解析失败: " + e.getMessage());
        }
    }

    /** 日志脱敏用：仅返回主机名，避免把用户问卷链接的路径写进日志 */
    private static String safeHost(String raw) {
        if (raw == null) {
            return "unknown";
        }
        try {
            String host = URI.create(raw.trim()).getHost();
            return host != null ? host : "unknown";
        } catch (IllegalArgumentException e) {
            return "unknown";
        }
    }

    /** 必须是 https 且主机名为 v.wjx.cn / www.wjx.cn（严格比较主机名，不做前缀匹配） */
    private static boolean isAllowedWjxUrl(String raw) {
        try {
            URI uri = URI.create(raw.trim());
            if (!"https".equalsIgnoreCase(uri.getScheme())) {
                return false;
            }
            String host = uri.getHost();
            return host != null && ALLOWED_WJX_HOSTS.contains(host.toLowerCase(Locale.ROOT));
        } catch (IllegalArgumentException e) {
            return false;
        }
    }
}
