package top.yoonheart.controller;

import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.web.bind.annotation.*;
import top.yoonheart.po.Result;
import top.yoonheart.config.PythonExecutor;
import top.yoonheart.service.BrushTaskManager;
import top.yoonheart.model.BrushTask;

import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

@RestController
@RequestMapping("/api")
public class BrushController {

    @Autowired
    private BrushTaskManager taskManager;

    // 存储每个链接的刷问卷次数
    private static final Map<String, Integer> URL_COUNTS = new ConcurrentHashMap<>();
    // 全局总刷问卷次数
    private static int TOTAL_COUNT = 0;
    // 全局最大总份数
    private static final int MAX_TOTAL_COUNT = 10000;
    // 密钥
    private static final String SECRET_KEY = "yoonheart";
    // 普通用户最大份数
    private static final int MAX_NORMAL_COUNT = 1000;
    // 密钥用户最大份数
    private static final int MAX_SECRET_COUNT = 1000;

    @PostMapping("/brush")
    public Result brush(@RequestBody Map<String, Object> requestData) {
        try {
            // 检查是否是启动异步任务的请求（前端带 startTask 标识）
            if (requestData.get("startTask") != null) {
                return startBrushTask(requestData);
            }

            // 获取请求参数
            String url = (String) requestData.get("url");
            Integer targetCount = (Integer) requestData.get("targetCount");
            Integer speedMultiplier = (Integer) requestData.get("speedMultiplier");
            // 默认使用2线程
            if (speedMultiplier == null) {
                speedMultiplier = 2;
            }
            Object questions = requestData.get("questions");
            String secretKey = (String) requestData.get("secretKey");
            
            // 检查全局总份数是否达到上限
            if (TOTAL_COUNT + targetCount > MAX_TOTAL_COUNT) {
                return Result.error("系统测试份数已达上限，无法继续刷问卷");
            }
            
            // 获取当前链接的刷问卷次数
            int currentCount = URL_COUNTS.getOrDefault(url, 0);
            
            // 检查是否需要密钥
            if (currentCount + targetCount > MAX_NORMAL_COUNT) {
                // 需要密钥验证
                if (secretKey == null || !secretKey.equals(SECRET_KEY)) {
                    return Result.error("密钥验证失败，请输入正确的密钥");
                }
                // 密钥用户检查总次数
                if (currentCount + targetCount > MAX_SECRET_COUNT) {
                    return Result.error("每个链接最多只能刷1000份问卷");
                }
            } else {
                // 普通用户检查份数范围
                if (targetCount < 1 || targetCount > MAX_NORMAL_COUNT) {
                    return Result.error("份数必须在1-1000之间，请重新设置");
                }
            }
            
            // 校验窗口数参数
            if (speedMultiplier != 2) {
                return Result.error("窗口数必须为2，请重新设置");
            }

            // 使用Jackson库构建完整的JSON对象
            com.fasterxml.jackson.databind.ObjectMapper mapper = new com.fasterxml.jackson.databind.ObjectMapper();
            
            // 创建配置对象
            java.util.Map<String, Object> config = new java.util.HashMap<>();
            
            // 基本配置
            config.put("url", url);
            config.put("target_num", targetCount);
            config.put("num_threads", speedMultiplier);
            config.put("max_question_check", 200);
            config.put("use_ip", false);
            config.put("fail_threshold", targetCount / 4.0 + 1);
            
            // 初始化各种题型的配置
            java.util.Map<String, Object> singleProb = new java.util.HashMap<>();
            java.util.Map<String, Object> singleOtherTexts = new java.util.HashMap<>();
            java.util.Map<String, Object> singleOtherTextsProb = new java.util.HashMap<>();
            java.util.Map<String, Object> multipleProb = new java.util.HashMap<>();
            java.util.Map<String, Object> otherTexts = new java.util.HashMap<>();
            java.util.Map<String, Object> otherTextsProb = new java.util.HashMap<>();
            java.util.Map<String, Object> droplistProb = new java.util.HashMap<>();
            java.util.Map<String, Object> texts = new java.util.HashMap<>();
            java.util.Map<String, Object> textsProb = new java.util.HashMap<>();
            java.util.Map<String, Object> scaleProb = new java.util.HashMap<>();
            java.util.Map<String, Object> matrixProb = new java.util.HashMap<>();
            
            // 处理questions数据
            if (questions instanceof java.util.List) {
                java.util.List<?> questionsList = (java.util.List<?>) questions;
                for (Object questionObj : questionsList) {
                    if (questionObj instanceof java.util.Map) {
                        java.util.Map<?, ?> questionMap = (java.util.Map<?, ?>) questionObj;
                        String qid = (String) questionMap.get("id");
                        String qtype = (String) questionMap.get("type");
                        
                        if ("单选题".equals(qtype)) {
                            // 处理单选题选项概率
                            java.util.List<Object> options = (java.util.List<Object>) questionMap.get("options");
                            if (options != null) {
                                java.util.List<Integer> probs = new java.util.ArrayList<>();
                                for (Object option : options) {
                                    if (option instanceof java.util.Map) {
                                        Integer prob = (Integer) ((java.util.Map<?, ?>) option).get("probability");
                                        probs.add(prob);
                                    }
                                }
                                if (!probs.isEmpty()) {
                                    singleProb.put(qid, probs);
                                }
                            }
                            
                            // 处理单选题其他选项文本
                            java.util.Map<?, ?> optionTextAnswers = (java.util.Map<?, ?>) questionMap.get("optionTextAnswers");
                            if (optionTextAnswers != null) {
                                for (Object optionIndexObj : optionTextAnswers.keySet()) {
                                    java.util.List<Object> textAnswers = (java.util.List<Object>) optionTextAnswers.get(optionIndexObj);
                                    if (textAnswers != null && !textAnswers.isEmpty()) {
                                        java.util.List<String> textList = new java.util.ArrayList<>();
                                        java.util.List<Integer> probList = new java.util.ArrayList<>();
                                        for (Object textAnswer : textAnswers) {
                                            if (textAnswer instanceof java.util.Map) {
                                                String text = (String) ((java.util.Map<?, ?>) textAnswer).get("text");
                                                Integer prob = (Integer) ((java.util.Map<?, ?>) textAnswer).get("probability");
                                                textList.add(text);
                                                probList.add(prob);
                                            }
                                        }
                                        if (!textList.isEmpty()) {
                                            singleOtherTexts.put(qid, textList);
                                            singleOtherTextsProb.put(qid, probList);
                                        }
                                    }
                                }
                            }
                        } else if ("多选题".equals(qtype)) {
                            // 处理多选题选项概率
                            java.util.List<Object> options = (java.util.List<Object>) questionMap.get("options");
                            if (options != null) {
                                java.util.List<Integer> probs = new java.util.ArrayList<>();
                                for (Object option : options) {
                                    if (option instanceof java.util.Map) {
                                        Integer prob = (Integer) ((java.util.Map<?, ?>) option).get("probability");
                                        probs.add(prob);
                                    }
                                }
                                if (!probs.isEmpty()) {
                                    multipleProb.put(qid, probs);
                                }
                            }
                            
                            // 处理多选题其他选项文本
                            java.util.Map<?, ?> optionTextAnswers = (java.util.Map<?, ?>) questionMap.get("optionTextAnswers");
                            if (optionTextAnswers != null) {
                                for (Object optionIndexObj : optionTextAnswers.keySet()) {
                                    java.util.List<Object> textAnswers = (java.util.List<Object>) optionTextAnswers.get(optionIndexObj);
                                    if (textAnswers != null && !textAnswers.isEmpty()) {
                                        java.util.List<String> textList = new java.util.ArrayList<>();
                                        java.util.List<Integer> probList = new java.util.ArrayList<>();
                                        for (Object textAnswer : textAnswers) {
                                            if (textAnswer instanceof java.util.Map) {
                                                String text = (String) ((java.util.Map<?, ?>) textAnswer).get("text");
                                                Integer prob = (Integer) ((java.util.Map<?, ?>) textAnswer).get("probability");
                                                textList.add(text);
                                                probList.add(prob);
                                            }
                                        }
                                        if (!textList.isEmpty()) {
                                            otherTexts.put(qid, textList);
                                            otherTextsProb.put(qid, probList);
                                        }
                                    }
                                }
                            }
                        } else if ("填空题".equals(qtype)) {
                            // 处理填空题文本答案
                            java.util.List<Object> textAnswers = (java.util.List<Object>) questionMap.get("textAnswers");
                            if (textAnswers != null && !textAnswers.isEmpty()) {
                                java.util.List<String> textList = new java.util.ArrayList<>();
                                java.util.List<Integer> probList = new java.util.ArrayList<>();
                                for (Object textAnswer : textAnswers) {
                                    if (textAnswer instanceof java.util.Map) {
                                        String text = (String) ((java.util.Map<?, ?>) textAnswer).get("text");
                                        Integer prob = (Integer) ((java.util.Map<?, ?>) textAnswer).get("probability");
                                        textList.add(text);
                                        probList.add(prob);
                                    }
                                }
                                if (!textList.isEmpty()) {
                                    texts.put(qid, textList);
                                    textsProb.put(qid, probList);
                                }
                            } else {
                                // 尝试从options字段获取（兼容旧格式）
                                java.util.List<Object> options = (java.util.List<Object>) questionMap.get("options");
                                if (options != null && !options.isEmpty()) {
                                    java.util.List<String> textList = new java.util.ArrayList<>();
                                    java.util.List<Integer> probList = new java.util.ArrayList<>();
                                    for (Object option : options) {
                                        if (option instanceof java.util.Map) {
                                            String text = (String) ((java.util.Map<?, ?>) option).get("text");
                                            Integer prob = (Integer) ((java.util.Map<?, ?>) option).get("probability");
                                            textList.add(text);
                                            probList.add(prob);
                                        }
                                    }
                                    if (!textList.isEmpty()) {
                                        texts.put(qid, textList);
                                        textsProb.put(qid, probList);
                                    }
                                }
                            }
                        } else if ("量表题".equals(qtype)) {
                            // 处理量表题选项概率
                            java.util.List<Object> options = (java.util.List<Object>) questionMap.get("options");
                            if (options != null) {
                                java.util.List<Integer> probs = new java.util.ArrayList<>();
                                for (Object option : options) {
                                    if (option instanceof java.util.Map) {
                                        Integer prob = (Integer) ((java.util.Map<?, ?>) option).get("probability");
                                        probs.add(prob);
                                    }
                                }
                                if (!probs.isEmpty()) {
                                    scaleProb.put(qid, probs);
                                }
                            }
                        } else if ("矩阵题".equals(qtype)) {
                            // 处理矩阵题选项概率
                            java.util.List<Object> rows = (java.util.List<Object>) questionMap.get("options");
                            if (rows != null) {
                                java.util.List<Object> matrixRows = new java.util.ArrayList<>();
                                for (Object rowObj : rows) {
                                    if (rowObj instanceof java.util.Map) {
                                        java.util.List<Object> rowOptions = (java.util.List<Object>) ((java.util.Map<?, ?>) rowObj).get("options");
                                        if (rowOptions != null) {
                                            java.util.List<Integer> rowProbs = new java.util.ArrayList<>();
                                            for (Object option : rowOptions) {
                                                if (option instanceof java.util.Map) {
                                                    Integer prob = (Integer) ((java.util.Map<?, ?>) option).get("probability");
                                                    rowProbs.add(prob);
                                                }
                                            }
                                            matrixRows.add(rowProbs);
                                        }
                                    }
                                }
                                if (!matrixRows.isEmpty()) {
                                    matrixProb.put(qid, matrixRows);
                                }
                            }
                        }
                    }
                }
            }
            
            // 将所有配置添加到主配置对象
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
            
            // 转换为JSON字符串
            String configJson = mapper.writeValueAsString(config);
            
            // 构建Python脚本路径
            String scriptPath = "./src/main/resources/scripts/wjx2.py";

            // 执行Python脚本，传递完整的配置JSON
            String result = PythonExecutor.executePythonScript(scriptPath, configJson);
            
            // 更新链接的刷问卷次数
            URL_COUNTS.put(url, currentCount + targetCount);
            
            // 更新全局总刷问卷次数
            TOTAL_COUNT += targetCount;

            // 解析执行结果
            // 这里需要根据Python脚本的实际输出格式进行解析
            // 假设脚本返回JSON格式的结果
            Map<String, Object> resultMap = parseScriptResult(result);

            return Result.success(resultMap);
        } catch (Exception e) {
            e.printStackTrace();
            return Result.error("刷问卷失败: " + e.getMessage());
        }
    }

    private Map<String, Object> parseScriptResult(String result) {
        try {
            // 解析Python脚本输出的JSON结果
            // 找到最后一行的JSON字符串
            String[] lines = result.split("\\n");
            String jsonLine = null;
            
            for (int i = lines.length - 1; i >= 0; i--) {
                String line = lines[i].trim();
                if (line.startsWith("{") && line.endsWith("}")) {
                    jsonLine = line;
                    break;
                }
            }
            
            if (jsonLine != null) {
                // 使用Jackson库解析JSON
                // 这里假设项目中已经引入了Jackson库
                com.fasterxml.jackson.databind.ObjectMapper mapper = new com.fasterxml.jackson.databind.ObjectMapper();
                return mapper.readValue(jsonLine, Map.class);
            } else {
                // 如果没有找到JSON结果，直接返回原始输出
                return Map.of(
                        "successCount", 0,
                        "failureCount", 0,
                        "runTime", "未知",
                        "message", result
                );
            }
        } catch (Exception e) {
            e.printStackTrace();
            // 解析失败，直接返回原始输出
            return Map.of(
                    "successCount", 0,
                    "failureCount", 0,
                    "runTime", "未知",
                    "message", result
            );
        }
    }

    // 新增方法：启动异步任务
    @PostMapping("/brush/start")
    public Result startBrushTask(@RequestBody Map<String, Object> requestData) {
        try {
            // 检查是否是启动任务的请求
            if (!requestData.containsKey("startTask")) {
                return Result.error("非法请求：缺少startTask标识");
            }

            // 生成任务ID
            String taskId = "task_" + System.currentTimeMillis();

            // 从请求中提取必要的参数
            String url = (String) requestData.get("url");
            Integer targetCount = (Integer) requestData.get("targetCount");
            Integer speedMultiplier = (Integer) requestData.get("speedMultiplier");
            Object questions = requestData.get("questions");
            String secretKey = (String) requestData.get("secretKey");
            // 首页传来的配置（无头模式默认开启，代理默认开启，API链接默认使用脚本内置的）
            Boolean headless = (Boolean) requestData.get("headless");
            Boolean useProxy = (Boolean) requestData.get("useProxy");
            String ipApiUrl = (String) requestData.get("ipApiUrl");
            // 时间控制（默认关闭；开启后一个IP只填一份）
            Boolean timeControl = (Boolean) requestData.get("timeControl");
            Integer minFillTime = (Integer) requestData.get("minFillTime");
            Integer maxFillTime = (Integer) requestData.get("maxFillTime");

            // 检查全局总份数是否达到上限
            if (TOTAL_COUNT + targetCount > MAX_TOTAL_COUNT) {
                return Result.error("系统测试份数已达上限，无法继续刷问卷");
            }

            // 获取当前链接的刷问卷次数
            int currentCount = URL_COUNTS.getOrDefault(url, 0);

            // 检查是否需要密钥
            if (currentCount + targetCount > MAX_NORMAL_COUNT) {
                // 需要密钥验证
                if (secretKey == null || !secretKey.equals(SECRET_KEY)) {
                    return Result.error("密钥验证失败，请输入正确的密钥");
                }
                // 密钥用户检查总次数
                if (currentCount + targetCount > MAX_SECRET_COUNT) {
                    return Result.error("每个链接最多只能刷1000份问卷");
                }
            } else {
                // 普通用户检查份数范围
                if (targetCount < 1 || targetCount > MAX_NORMAL_COUNT) {
                    return Result.error("份数必须在1-1000之间，请重新设置");
                }
            }

            // 校验窗口数参数
            if (speedMultiplier != 2) {
                return Result.error("窗口数必须为2，请重新设置");
            }

            // 构建配置对象（这部分代码来自原brush方法）
            com.fasterxml.jackson.databind.ObjectMapper mapper = new com.fasterxml.jackson.databind.ObjectMapper();
            java.util.Map<String, Object> config = new java.util.HashMap<>();

            // 基本配置
            config.put("url", url);
            config.put("target_num", targetCount);
            config.put("num_threads", speedMultiplier);
            config.put("max_question_check", 200);
            // 代理开关（默认开启）
            config.put("use_ip", useProxy != null ? useProxy : true);
            config.put("fail_threshold", targetCount / 4.0 + 1);
            config.put("taskId", taskId); // 添加任务ID
            // 无头模式（默认开启）
            config.put("headless", headless != null ? headless : true);
            // 代理IP API链接（默认使用脚本内置的链接）
            if (ipApiUrl != null && !ipApiUrl.isEmpty()) {
                config.put("ip_api_url", ipApiUrl);
            }
            // 时间控制：开启后每份问卷耗时在区间内随机，且一个IP只填一份
            if (timeControl != null && timeControl) {
                int minTime = (minFillTime != null && minFillTime >= 30 && minFillTime <= 150) ? minFillTime : 30;
                int maxTime = (maxFillTime != null && maxFillTime >= 30 && maxFillTime <= 150) ? maxFillTime : 150;
                if (maxTime < minTime) {
                    int tmp = maxTime;
                    maxTime = minTime;
                    minTime = tmp;
                }
                config.put("min_fill_time", minTime);
                config.put("max_fill_time", maxTime);
                config.put("ip_max_use", 1);  // 一个IP只填一份
            }

            // 初始化各种题型的配置
            java.util.Map<String, Object> singleProb = new java.util.HashMap<>();
            java.util.Map<String, Object> singleOtherTexts = new java.util.HashMap<>();
            java.util.Map<String, Object> singleOtherTextsProb = new java.util.HashMap<>();
            java.util.Map<String, Object> multipleProb = new java.util.HashMap<>();
            java.util.Map<String, Object> otherTexts = new java.util.HashMap<>();
            java.util.Map<String, Object> otherTextsProb = new java.util.HashMap<>();
            java.util.Map<String, Object> droplistProb = new java.util.HashMap<>();
            java.util.Map<String, Object> texts = new java.util.HashMap<>();
            java.util.Map<String, Object> textsProb = new java.util.HashMap<>();
            java.util.Map<String, Object> scaleProb = new java.util.HashMap<>();
            java.util.Map<String, Object> matrixProb = new java.util.HashMap<>();

            // 处理questions数据（这部分代码来自原brush方法）
            if (questions instanceof java.util.List) {
                java.util.List<?> questionsList = (java.util.List<?>) questions;
                for (Object questionObj : questionsList) {
                    if (questionObj instanceof java.util.Map) {
                        java.util.Map<?, ?> questionMap = (java.util.Map<?, ?>) questionObj;
                        String qid = (String) questionMap.get("id");
                        String qtype = (String) questionMap.get("type");

                        if ("单选题".equals(qtype)) {
                            // 处理单选题选项概率
                            java.util.List<Object> options = (java.util.List<Object>) questionMap.get("options");
                            if (options != null) {
                                java.util.List<Integer> probs = new java.util.ArrayList<>();
                                for (Object option : options) {
                                    if (option instanceof java.util.Map) {
                                        Integer prob = (Integer) ((java.util.Map<?, ?>) option).get("probability");
                                        probs.add(prob);
                                    }
                                }
                                if (!probs.isEmpty()) {
                                    singleProb.put(qid, probs);
                                }
                            }

                            // 处理单选题其他选项文本
                            java.util.Map<?, ?> optionTextAnswers = (java.util.Map<?, ?>) questionMap.get("optionTextAnswers");
                            if (optionTextAnswers != null) {
                                for (Object optionIndexObj : optionTextAnswers.keySet()) {
                                    java.util.List<Object> textAnswers = (java.util.List<Object>) optionTextAnswers.get(optionIndexObj);
                                    if (textAnswers != null && !textAnswers.isEmpty()) {
                                        java.util.List<String> textList = new java.util.ArrayList<>();
                                        java.util.List<Integer> probList = new java.util.ArrayList<>();
                                        for (Object textAnswer : textAnswers) {
                                            if (textAnswer instanceof java.util.Map) {
                                                String text = (String) ((java.util.Map<?, ?>) textAnswer).get("text");
                                                Integer prob = (Integer) ((java.util.Map<?, ?>) textAnswer).get("probability");
                                                textList.add(text);
                                                probList.add(prob);
                                            }
                                        }
                                        if (!textList.isEmpty()) {
                                            singleOtherTexts.put(qid, textList);
                                            singleOtherTextsProb.put(qid, probList);
                                        }
                                    }
                                }
                            }
                        } else if ("多选题".equals(qtype)) {
                            // 处理多选题选项概率
                            java.util.List<Object> options = (java.util.List<Object>) questionMap.get("options");
                            if (options != null) {
                                java.util.List<Integer> probs = new java.util.ArrayList<>();
                                for (Object option : options) {
                                    if (option instanceof java.util.Map) {
                                        Integer prob = (Integer) ((java.util.Map<?, ?>) option).get("probability");
                                        probs.add(prob);
                                    }
                                }
                                if (!probs.isEmpty()) {
                                    multipleProb.put(qid, probs);
                                }
                            }

                            // 处理多选题其他选项文本
                            java.util.Map<?, ?> optionTextAnswers = (java.util.Map<?, ?>) questionMap.get("optionTextAnswers");
                            if (optionTextAnswers != null) {
                                for (Object optionIndexObj : optionTextAnswers.keySet()) {
                                    java.util.List<Object> textAnswers = (java.util.List<Object>) optionTextAnswers.get(optionIndexObj);
                                    if (textAnswers != null && !textAnswers.isEmpty()) {
                                        java.util.List<String> textList = new java.util.ArrayList<>();
                                        java.util.List<Integer> probList = new java.util.ArrayList<>();
                                        for (Object textAnswer : textAnswers) {
                                            if (textAnswer instanceof java.util.Map) {
                                                String text = (String) ((java.util.Map<?, ?>) textAnswer).get("text");
                                                Integer prob = (Integer) ((java.util.Map<?, ?>) textAnswer).get("probability");
                                                textList.add(text);
                                                probList.add(prob);
                                            }
                                        }
                                        if (!textList.isEmpty()) {
                                            otherTexts.put(qid, textList);
                                            otherTextsProb.put(qid, probList);
                                        }
                                    }
                                }
                            }
                        } else if ("填空题".equals(qtype)) {
                            // 处理填空题文本答案
                            java.util.List<Object> textAnswers = (java.util.List<Object>) questionMap.get("textAnswers");
                            if (textAnswers != null && !textAnswers.isEmpty()) {
                                java.util.List<String> textList = new java.util.ArrayList<>();
                                java.util.List<Integer> probList = new java.util.ArrayList<>();
                                for (Object textAnswer : textAnswers) {
                                    if (textAnswer instanceof java.util.Map) {
                                        String text = (String) ((java.util.Map<?, ?>) textAnswer).get("text");
                                        Integer prob = (Integer) ((java.util.Map<?, ?>) textAnswer).get("probability");
                                        textList.add(text);
                                        probList.add(prob);
                                    }
                                }
                                if (!textList.isEmpty()) {
                                    texts.put(qid, textList);
                                    textsProb.put(qid, probList);
                                }
                            } else {
                                // 尝试从options字段获取（兼容旧格式）
                                java.util.List<Object> options = (java.util.List<Object>) questionMap.get("options");
                                if (options != null && !options.isEmpty()) {
                                    java.util.List<String> textList = new java.util.ArrayList<>();
                                    java.util.List<Integer> probList = new java.util.ArrayList<>();
                                    for (Object option : options) {
                                        if (option instanceof java.util.Map) {
                                            String text = (String) ((java.util.Map<?, ?>) option).get("text");
                                            Integer prob = (Integer) ((java.util.Map<?, ?>) option).get("probability");
                                            textList.add(text);
                                            probList.add(prob);
                                        }
                                    }
                                    if (!textList.isEmpty()) {
                                        texts.put(qid, textList);
                                        textsProb.put(qid, probList);
                                    }
                                }
                            }
                        } else if ("量表题".equals(qtype)) {
                            // 处理量表题选项概率
                            java.util.List<Object> options = (java.util.List<Object>) questionMap.get("options");
                            if (options != null) {
                                java.util.List<Integer> probs = new java.util.ArrayList<>();
                                for (Object option : options) {
                                    if (option instanceof java.util.Map) {
                                        Integer prob = (Integer) ((java.util.Map<?, ?>) option).get("probability");
                                        probs.add(prob);
                                    }
                                }
                                if (!probs.isEmpty()) {
                                    scaleProb.put(qid, probs);
                                }
                            }
                        } else if ("矩阵题".equals(qtype)) {
                            // 处理矩阵题选项概率
                            java.util.List<Object> rows = (java.util.List<Object>) questionMap.get("options");
                            if (rows != null) {
                                java.util.List<Object> matrixRows = new java.util.ArrayList<>();
                                for (Object rowObj : rows) {
                                    if (rowObj instanceof java.util.Map) {
                                        java.util.List<Object> rowOptions = (java.util.List<Object>) ((java.util.Map<?, ?>) rowObj).get("options");
                                        if (rowOptions != null) {
                                            java.util.List<Integer> rowProbs = new java.util.ArrayList<>();
                                            for (Object option : rowOptions) {
                                                if (option instanceof java.util.Map) {
                                                    Integer prob = (Integer) ((java.util.Map<?, ?>) option).get("probability");
                                                    rowProbs.add(prob);
                                                }
                                            }
                                            matrixRows.add(rowProbs);
                                        }
                                    }
                                }
                                if (!matrixRows.isEmpty()) {
                                    matrixProb.put(qid, matrixRows);
                                }
                            }
                        }
                    }
                }
            }

            // 将所有配置添加到主配置对象
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

            // 转换为JSON字符串
            String configJson = mapper.writeValueAsString(config);

            // 更新链接的刷问卷次数
            URL_COUNTS.put(url, currentCount + targetCount);
            // 更新全局总刷问卷次数
            TOTAL_COUNT += targetCount;

            // 启动异步任务
            taskManager.createTask(taskId, configJson, url, targetCount);

            // 返回任务ID
            return Result.success(java.util.Map.of("taskId", taskId));
        } catch (Exception e) {
            e.printStackTrace();
            return Result.error("任务启动失败: " + e.getMessage());
        }
    }
}