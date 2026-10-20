package top.yoonheart.config;

import org.springframework.context.annotation.Configuration;
import org.springframework.http.CacheControl;
import org.springframework.web.servlet.config.annotation.ResourceHandlerRegistry;
import org.springframework.web.servlet.config.annotation.WebMvcConfigurer;

@Configuration
public class WebConfig implements WebMvcConfigurer {
    
    @Override
    public void addResourceHandlers(ResourceHandlerRegistry registry) {
        // 配置静态资源访问路径。
        //
        // 关键：禁用浏览器缓存（no-store）。
        // 桌面版是 JCEF 内嵌 Chromium，它会按启发式规则把 /static/js/*.js 缓存到磁盘
        // （app/jcef/cache）。换了新 jar 后，浏览器仍可能直接拿旧 JS，导致「界面还是老样子」。
        // 这里统一返回 no-store，强制每次启动都从本地服务器重新拉取，避免陈旧的静态资源。
        registry.addResourceHandler("/static/**")
                .addResourceLocations("classpath:/static/")
                .setCacheControl(CacheControl.noStore());
    }
}
