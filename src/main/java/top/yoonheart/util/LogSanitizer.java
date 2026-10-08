package top.yoonheart.util;

import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * 日志脱敏工具。
 *
 * <p>本工具会通过代理 IP 访问问卷星，脚本运行过程中会打印所用的代理地址
 * （例如「页面加载超时（IP: 1.2.3.4:8080），更换IP重试」）。代理 IP 是用户
 * 自行购买的资源、可关联到具体出口网络，不应明文落进应用日志，因此在写日志前统一打码。</p>
 */
public final class LogSanitizer {

    /** IPv4 地址，可选带端口 */
    private static final Pattern IPV4 = Pattern.compile(
            "\\b(\\d{1,3})\\.(\\d{1,3})\\.(\\d{1,3})\\.(\\d{1,3})(:\\d+)?");

    private LogSanitizer() {
    }

    /**
     * 把文本中的 IPv4 地址打码。
     *
     * <p>保留前两段、打码后两段：既能判断「有没有换 IP」，又不暴露完整出口地址。</p>
     *
     * @param text 原始文本，可为 null
     * @return 打码后的文本；入参为 null 或空串时原样返回
     */
    public static String maskIp(String text) {
        if (text == null || text.isEmpty()) {
            return text;
        }
        Matcher matcher = IPV4.matcher(text);
        return matcher.replaceAll("$1.$2.*.*");
    }
}
