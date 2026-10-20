package top.yoonheart.controller;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import top.yoonheart.po.Result;

import java.io.File;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.Map;

import javax.swing.JFileChooser;
import javax.swing.SwingUtilities;
import javax.swing.UIManager;
import javax.swing.filechooser.FileNameExtensionFilter;

/**
 * 问卷配置导入 / 导出（桌面端专用）。
 *
 * <p>导出：前端把「每道题的配置 + 功能详细设置」打包成 JSON 传上来，
 * 这里弹出系统「另存为」对话框（文件名默认取问卷码，如 {@code YJc8nO1.json}），
 * 用户选好位置后写盘。</p>
 *
 * <p>导入：弹出「打开文件」对话框（只认 .json），读出内容原样返回给前端回填。</p>
 *
 * <p>文件对话框必须在 AWT 事件线程上显示（桌面模式 {@code java.awt.headless=false}），
 * Tomcat 工作线程通过 {@link SwingUtilities#invokeAndWait} 过去；网页模式（headless）
 * 下没有桌面环境，两个接口都直接返回可读错误。</p>
 */
@RestController
@RequestMapping("/api/config")
public class ConfigIOController {

    private static final Logger log = LoggerFactory.getLogger(ConfigIOController.class);
    private static final String FILE_EXTENSION = ".json";

    /** 导出：弹「另存为」并写盘。请求体 { suggestedName: "YJc8nO1.json", data: {...} } */
    @PostMapping("/export")
    public Result exportConfig(@RequestBody Map<String, Object> body) {
        if (java.awt.GraphicsEnvironment.isHeadless()) {
            return Result.error("当前是网页模式，无法弹出保存对话框，请改用桌面软件的导入导出功能");
        }
        Object data = body.get("data");
        if (data == null) {
            return Result.error("没有可导出的配置数据");
        }
        String suggestedName = sanitizeName(String.valueOf(
                body.getOrDefault("suggestedName", "wjx-config" + FILE_EXTENSION)));

        final String json;
        try {
            json = new ObjectMapper().writerWithDefaultPrettyPrinter().writeValueAsString(data);
        } catch (Exception e) {
            log.error("导出配置序列化失败", e);
            return Result.error("配置序列化失败: " + e.getMessage());
        }

        final File[] chosen = new File[1];
        try {
            SwingUtilities.invokeAndWait(() -> chosen[0] = showSaveDialog(suggestedName));
        } catch (Exception e) {
            log.error("显示保存对话框失败", e);
            return Result.error("无法打开保存对话框: " + e.getMessage());
        }
        File target = chosen[0];
        if (target == null) {
            return Result.error("已取消导出");
        }
        if (!target.getName().toLowerCase().endsWith(FILE_EXTENSION)) {
            target = new File(target.getParentFile(), target.getName() + FILE_EXTENSION);
        }
        try {
            Files.writeString(target.toPath(), json, StandardCharsets.UTF_8);
        } catch (Exception e) {
            log.error("写入配置文件失败: {}", target, e);
            return Result.error("写入文件失败: " + e.getMessage());
        }
        log.info("配置已导出到 {}", target.getAbsolutePath());
        return Result.success(Map.of("path", target.getAbsolutePath()));
    }

    /** 导入：弹「打开文件」，返回文件里的 JSON。 */
    @PostMapping("/import")
    public Result importConfig() {
        if (java.awt.GraphicsEnvironment.isHeadless()) {
            return Result.error("当前是网页模式，无法弹出打开对话框，请改用桌面软件的导入导出功能");
        }
        final File[] chosen = new File[1];
        try {
            SwingUtilities.invokeAndWait(() -> chosen[0] = showOpenDialog());
        } catch (Exception e) {
            log.error("显示打开对话框失败", e);
            return Result.error("无法打开文件对话框: " + e.getMessage());
        }
        File source = chosen[0];
        if (source == null) {
            return Result.error("已取消导入");
        }
        try {
            String content = Files.readString(source.toPath(), StandardCharsets.UTF_8);
            Object parsed = new ObjectMapper().readTree(content);
            return Result.success(parsed);
        } catch (Exception e) {
            log.error("读取配置文件失败: {}", source, e);
            return Result.error("读取文件失败，请确认选择的是本工具导出的 json 文件");
        }
    }

    /**
     * 「另存为」对话框：文件名预填问卷码，文件类型默认 json。
     *
     * <p>为什么不用 {@code java.awt.FileDialog}：它在 Windows 上是系统原生对话框，
     * 会**忽略** {@code setFilenameFilter}，「对象类型」下拉里只有「所有文件 (*.*)」，
     * 看起来像程序不认 json。{@link JFileChooser} 才能真正给出扩展名过滤，
     * 代价是自绘外观，所以切到系统 LAF 尽量贴近（见 {@link #useSystemLookAndFeel()}）。</p>
     */
    private File showSaveDialog(String suggestedName) {
        useSystemLookAndFeel();
        JFileChooser chooser = new JFileChooser();
        chooser.setDialogTitle("导出配置");
        chooser.setFileFilter(new FileNameExtensionFilter("问卷配置 (*.json)", "json"));
        chooser.setAcceptAllFileFilterUsed(true);
        File dir = lastDirectory();
        if (dir != null) {
            chooser.setCurrentDirectory(dir);
        }
        chooser.setSelectedFile(new File(suggestedName));
        if (chooser.showSaveDialog(null) != JFileChooser.APPROVE_OPTION) {
            return null;
        }
        File picked = chooser.getSelectedFile();
        rememberDirectory(picked);
        return picked;
    }

    /** 「打开文件」对话框：默认只显示 *.json */
    private File showOpenDialog() {
        useSystemLookAndFeel();
        JFileChooser chooser = new JFileChooser();
        chooser.setDialogTitle("导入配置");
        // 设成 fileFilter 之后它就是默认选中的那一项，下拉里第一眼看到的是 json
        chooser.setFileFilter(new FileNameExtensionFilter("问卷配置 (*.json)", "json"));
        chooser.setAcceptAllFileFilterUsed(true);
        File dir = lastDirectory();
        if (dir != null) {
            chooser.setCurrentDirectory(dir);
        }
        if (chooser.showOpenDialog(null) != JFileChooser.APPROVE_OPTION) {
            return null;
        }
        File picked = chooser.getSelectedFile();
        rememberDirectory(picked);
        return picked;
    }

    /** 首次弹框前把 Swing 外观切成系统风格；只做一次，避免反复触发全局刷新。 */
    private static volatile boolean lafApplied = false;

    private static void useSystemLookAndFeel() {
        if (lafApplied) {
            return;
        }
        try {
            UIManager.setLookAndFeel(UIManager.getSystemLookAndFeelClassName());
        } catch (Exception e) {
            // 切不过去不影响功能，最多是外观朴素一点
            log.warn("切换系统外观失败，沿用默认外观: {}", e.getMessage());
        }
        lafApplied = true;
    }

    /** 记住上次选文件的目录，下次打开直接落在那儿（导出到桌面后，导入也在桌面） */
    private static volatile File lastDirectory = null;

    private static File lastDirectory() {
        File dir = lastDirectory;
        if (dir != null && dir.isDirectory()) {
            return dir;
        }
        File desktop = new File(System.getProperty("user.home", "."), "Desktop");
        return desktop.isDirectory() ? desktop : null;
    }

    private static void rememberDirectory(File picked) {
        File parent = picked == null ? null : picked.getParentFile();
        if (parent != null && parent.isDirectory()) {
            lastDirectory = parent;
        }
    }

    /** 去掉文件名里的非法字符，避免问卷码带斜杠之类导致写盘失败 */
    private String sanitizeName(String name) {
        String cleaned = name == null ? "" : name.trim();
        if (cleaned.isEmpty()) {
            return "wjx-config" + FILE_EXTENSION;
        }
        cleaned = cleaned.replaceAll("[\\\\/:*?\"<>|]", "_");
        if (!cleaned.toLowerCase().endsWith(FILE_EXTENSION)) {
            cleaned = cleaned + FILE_EXTENSION;
        }
        return cleaned;
    }
}
