package top.yoonheart.controller;

import org.springframework.stereotype.Controller;
import org.springframework.web.bind.annotation.GetMapping;

/**
 * 页面路由。
 *
 * <p>页面已从 JSP 迁移为静态 HTML，存放于 {@code src/main/resources/static/}。
 * 这里只把原来的地址转发到对应静态文件，从而保持对外 URL（{@code /} 与 {@code /analysis}）不变。
 * 转发由 Servlet 容器的 RequestDispatcher 完成，不涉及任何模板引擎。</p>
 */
@Controller
public class HiController {

    @GetMapping("/")
    public String home() {
        return "forward:/index.html";
    }

    @GetMapping("/analysis")
    public String analysis() {
        return "forward:/analysis.html";
    }
}
