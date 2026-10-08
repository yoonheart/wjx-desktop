package top.yoonheart.controller;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import top.yoonheart.po.Result;
import top.yoonheart.service.BrushTaskManager;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * 刷问卷任务接口。
 *
 * <p>注：原有的「单链接 1000 份上限 + 密钥校验」逻辑已按需求整体移除，
 * 请求参数中的 secretKey 不再被读取。全局累计份数上限（原 10000 份）同样已移除，
 * 现在只保留单个任务的份数范围校验（1~1000 份），总量不设上限。</p>
 */
@RestController
@RequestMapping("/api")
public class BrushController {

    private static final Logger log = LoggerFactory.getLogger(BrushController.class);

    @Autowired
    private BrushTaskManager taskManager;

    /** 单个任务允许的目标份数范围 */
    private static final int MIN_TARGET_COUNT = 1;
    private static final int MAX_TARGET_COUNT = 1000;
    /** 当前仅支持 2 个窗口 */
    private static final int SUPPORTED_THREADS = 2;

    /**
     * 创建刷问卷异步任务。
     *
     * <p>{@code /api/brush} 与 {@code /api/brush/start} 等价，均返回 taskId，
     * 由前端通过 SSE 订阅进度。</p>
     */
    @PostMapping({"/brush", "/brush/start"})
    public Result brush(@RequestBody Map<String, Object> requestData) {
        try {
            String url = (String) requestData.get("url");
            Object questions = requestData.get("questions");
            // 用宽松解析代替 (Integer) 强转：JSON 里出现浮点或数字字符串时不会抛 ClassCastException
            Integer targetCount = toInt(requestData.get("targetCount"));
            Integer speedMultiplier = toInt(requestData.get("speedMultiplier"));

            if (url == null || url.trim().isEmpty()) {
                return Result.error("问卷链接不能为空，请重新设置");
            }
            if (targetCount == null) {
                return Result.error("份数不能为空，请重新设置");
            }
            if (targetCount < MIN_TARGET_COUNT || targetCount > MAX_TARGET_COUNT) {
                return Result.error("份数必须在1-1000之间，请重新设置");
            }
            // 窗口数默认 2
            if (speedMultiplier == null) {
                speedMultiplier = SUPPORTED_THREADS;
            }
            if (speedMultiplier != SUPPORTED_THREADS) {
                return Result.error("窗口数必须为2，请重新设置");
            }

            String taskId = "task_" + System.currentTimeMillis();

            Map<String, Object> config = new LinkedHashMap<>();
            config.put("url", url);
            config.put("target_num", targetCount);
            config.put("num_threads", speedMultiplier);
            config.put("max_question_check", 200);
            config.put("fail_threshold", targetCount / 4.0 + 1);
            config.put("taskId", taskId);

            // 无头模式（默认开启）
            Boolean headless = (Boolean) requestData.get("headless");
            config.put("headless", headless != null ? headless : true);

            // 代理开关（默认开启）
            Boolean useProxy = (Boolean) requestData.get("useProxy");
            config.put("use_ip", useProxy != null ? useProxy : true);

            // 代理IP API链接（默认使用脚本内置的链接）
            String ipApiUrl = (String) requestData.get("ipApiUrl");
            if (ipApiUrl != null && !ipApiUrl.isEmpty()) {
                config.put("ip_api_url", ipApiUrl);
            }

            // 时间控制：开启后每份问卷耗时在区间内随机，且一个IP只填一份
            Boolean timeControl = (Boolean) requestData.get("timeControl");
            if (timeControl != null && timeControl) {
                int minTime = normalizeFillTime(toInt(requestData.get("minFillTime")), 30);
                int maxTime = normalizeFillTime(toInt(requestData.get("maxFillTime")), 150);
                if (maxTime < minTime) {
                    int tmp = maxTime;
                    maxTime = minTime;
                    minTime = tmp;
                }
                config.put("min_fill_time", minTime);
                config.put("max_fill_time", maxTime);
                config.put("ip_max_use", 1);  // 一个IP只填一份
            }

            // 各题型的概率配置
            applyQuestionConfig(config, questions);

            String configJson = new com.fasterxml.jackson.databind.ObjectMapper()
                    .writeValueAsString(config);

            // 启动异步任务
            taskManager.createTask(taskId, configJson, url, targetCount);

            return Result.success(Map.of("taskId", taskId));
        } catch (Exception e) {
            log.error("任务启动失败", e);
            return Result.error("任务启动失败: " + e.getMessage());
        }
    }

    /**
     * 宽松地把请求体里的数值转成 int。
     *
     * <p>兼容 JSON 反序列化出的 Integer / Long / Double（取整数部分）以及数字字符串；
     * 无法识别时返回 null，由调用方给出可读的错误提示，而不是抛 ClassCastException。</p>
     */
    private static Integer toInt(Object raw) {
        if (raw instanceof Number number) {
            return number.intValue();
        }
        if (raw instanceof String text) {
            try {
                return Integer.valueOf(text.trim());
            } catch (NumberFormatException e) {
                return null;
            }
        }
        return null;
    }

    /** 把填写时长限制在 30~150 秒内，非法值回退到默认值 */
    private static int normalizeFillTime(Integer value, int fallback) {
        if (value == null || value < 30 || value > 150) {
            return fallback;
        }
        return value;
    }

    /**
     * 把前端提交的题目配置，转换成 Python 脚本所需的各题型概率表，写入 config。
     */
    private static void applyQuestionConfig(Map<String, Object> config, Object questions) {
        Map<String, Object> singleProb = new LinkedHashMap<>();
        Map<String, Object> singleOtherTexts = new LinkedHashMap<>();
        Map<String, Object> singleOtherTextsProb = new LinkedHashMap<>();
        Map<String, Object> multipleProb = new LinkedHashMap<>();
        Map<String, Object> otherTexts = new LinkedHashMap<>();
        Map<String, Object> otherTextsProb = new LinkedHashMap<>();
        Map<String, Object> droplistProb = new LinkedHashMap<>();
        Map<String, Object> texts = new LinkedHashMap<>();
        Map<String, Object> textsProb = new LinkedHashMap<>();
        Map<String, Object> scaleProb = new LinkedHashMap<>();
        Map<String, Object> matrixProb = new LinkedHashMap<>();

        if (questions instanceof List<?> questionsList) {
            for (Object questionObj : questionsList) {
                if (!(questionObj instanceof Map<?, ?> questionMap)) {
                    continue;
                }
                String qid = (String) questionMap.get("id");
                String qtype = (String) questionMap.get("type");
                if (qid == null || qtype == null) {
                    continue;
                }

                switch (qtype) {
                    case "单选题" -> {
                        putOptionProbs(singleProb, qid, questionMap.get("options"));
                        putOptionTextAnswers(singleOtherTexts, singleOtherTextsProb, qid,
                                questionMap.get("optionTextAnswers"));
                    }
                    case "多选题" -> {
                        putOptionProbs(multipleProb, qid, questionMap.get("options"));
                        putOptionTextAnswers(otherTexts, otherTextsProb, qid,
                                questionMap.get("optionTextAnswers"));
                    }
                    case "填空题" -> {
                        // 优先使用 textAnswers，为空时回退到 options（兼容旧格式）
                        Object textAnswers = questionMap.get("textAnswers");
                        if (!(textAnswers instanceof List<?> list) || list.isEmpty()) {
                            textAnswers = questionMap.get("options");
                        }
                        putTextAnswers(texts, textsProb, qid, textAnswers);
                    }
                    case "量表题" -> putOptionProbs(scaleProb, qid, questionMap.get("options"));
                    case "矩阵题" -> putMatrixProbs(matrixProb, qid, questionMap.get("options"));
                    default -> {
                        // 其他题型暂不处理
                    }
                }
            }
        }

        config.put("single_prob", singleProb);
        config.put("single_other_texts", singleOtherTexts);
        config.put("single_other_texts_prob", singleOtherTextsProb);
        config.put("multiple_prob", multipleProb);
        config.put("other_texts", otherTexts);
        config.put("other_texts_prob", otherTextsProb);
        config.put("droplist_prob", droplistProb);
        config.put("texts", texts);
        config.put("texts_prob", textsProb);
        config.put("scale_prob", scaleProb);
        config.put("matrix_prob", matrixProb);
    }

    /** 单选/多选/量表题：选项概率列表 */
    private static void putOptionProbs(Map<String, Object> target, String qid, Object options) {
        if (!(options instanceof List<?> optionList)) {
            return;
        }
        List<Integer> probs = new ArrayList<>();
        for (Object option : optionList) {
            if (option instanceof Map<?, ?> optionMap) {
                probs.add((Integer) optionMap.get("probability"));
            }
        }
        if (!probs.isEmpty()) {
            target.put(qid, probs);
        }
    }

    /** 单选/多选题：带文本输入框的选项，其候选文本与概率 */
    private static void putOptionTextAnswers(Map<String, Object> texts, Map<String, Object> probs,
                                             String qid, Object optionTextAnswers) {
        if (!(optionTextAnswers instanceof Map<?, ?> answerMap)) {
            return;
        }
        for (Object optionIndex : answerMap.keySet()) {
            Object answers = answerMap.get(optionIndex);
            if (!(answers instanceof List<?> answerList) || answerList.isEmpty()) {
                continue;
            }
            List<String> textList = new ArrayList<>();
            List<Integer> probList = new ArrayList<>();
            for (Object answer : answerList) {
                if (answer instanceof Map<?, ?> entry) {
                    textList.add((String) entry.get("text"));
                    probList.add((Integer) entry.get("probability"));
                }
            }
            if (!textList.isEmpty()) {
                texts.put(qid, textList);
                probs.put(qid, probList);
            }
        }
    }

    /** 填空题：候选文本与概率 */
    private static void putTextAnswers(Map<String, Object> texts, Map<String, Object> probs,
                                       String qid, Object textAnswers) {
        if (!(textAnswers instanceof List<?> answerList) || answerList.isEmpty()) {
            return;
        }
        List<String> textList = new ArrayList<>();
        List<Integer> probList = new ArrayList<>();
        for (Object answer : answerList) {
            if (answer instanceof Map<?, ?> entry) {
                textList.add((String) entry.get("text"));
                probList.add((Integer) entry.get("probability"));
            }
        }
        if (!textList.isEmpty()) {
            texts.put(qid, textList);
            probs.put(qid, probList);
        }
    }

    /** 矩阵题：每个子题（行）各自的选项概率列表 */
    private static void putMatrixProbs(Map<String, Object> target, String qid, Object rows) {
        if (!(rows instanceof List<?> rowList)) {
            return;
        }
        List<Object> matrixRows = new ArrayList<>();
        for (Object rowObj : rowList) {
            if (!(rowObj instanceof Map<?, ?> rowMap)) {
                continue;
            }
            Object rowOptions = rowMap.get("options");
            if (!(rowOptions instanceof List<?> optionList)) {
                continue;
            }
            List<Integer> rowProbs = new ArrayList<>();
            for (Object option : optionList) {
                if (option instanceof Map<?, ?> optionMap) {
                    rowProbs.add((Integer) optionMap.get("probability"));
                }
            }
            matrixRows.add(rowProbs);
        }
        if (!matrixRows.isEmpty()) {
            target.put(qid, matrixRows);
        }
    }
}
