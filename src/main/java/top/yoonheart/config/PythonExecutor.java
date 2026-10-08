package top.yoonheart.config;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import lombok.Data;

import java.io.BufferedReader;
import java.io.File;
import java.io.IOException;
import java.io.InputStreamReader;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.*;
import java.util.concurrent.TimeUnit;


public class PythonExecutor {
    
    // JSON配置对象
    public static class WjxConfig {
        private String url;  // 问卷URL
        private int target_num = 3;  // 目标份数
        private int num_threads = 3;  // 线程数/窗口数
        private int max_question_check = 200;  // 最大检测题号
        private boolean use_ip = false;  // 是否使用代理IP
        private double fail_threshold;  // 失败阈值
        
        // 题目配置
        private Map<String, List<Integer>> single_prob = new HashMap<>();
        private Map<String, List<String>> single_other_texts = new HashMap<>();
        private Map<String, List<Integer>> single_other_texts_prob = new HashMap<>();
        private Map<String, List<Integer>> droplist_prob = new HashMap<>();
        private Map<String, List<Integer>> multiple_prob = new HashMap<>();
        private Map<String, List<String>> other_texts = new HashMap<>();
        private Map<String, List<Integer>> other_texts_prob = new HashMap<>();
        private Map<String, List<Object>> matrix_prob = new HashMap<>();
        private Map<String, List<Integer>> scale_prob = new HashMap<>();
        private Map<String, List<String>> texts = new HashMap<>();
        private Map<String, List<Integer>> texts_prob = new HashMap<>();

        // Getters and Setters
        public String getUrl() { return url; }
        public void setUrl(String url) { this.url = url; }
        public int getTarget_num() { return target_num; }
        public void setTarget_num(int target_num) { 
            this.target_num = target_num;
            this.fail_threshold = target_num / 4.0 + 1;  // 自动计算失败阈值
        }
        public int getNum_threads() { return num_threads; }
        public void setNum_threads(int num_threads) { this.num_threads = num_threads; }
        public int getMax_question_check() { return max_question_check; }
        public void setMax_question_check(int max_question_check) { this.max_question_check = max_question_check; }
        public boolean isUse_ip() { return use_ip; }
        public void setUse_ip(boolean use_ip) { this.use_ip = use_ip; }
        public double getFail_threshold() { return fail_threshold; }
        public void setFail_threshold(double fail_threshold) { this.fail_threshold = fail_threshold; }
        
        public Map<String, List<Integer>> getSingle_prob() { return single_prob; }
        public void setSingle_prob(Map<String, List<Integer>> single_prob) { this.single_prob = single_prob; }
        public Map<String, List<String>> getSingle_other_texts() { return single_other_texts; }
        public void setSingle_other_texts(Map<String, List<String>> single_other_texts) { this.single_other_texts = single_other_texts; }
        public Map<String, List<Integer>> getSingle_other_texts_prob() { return single_other_texts_prob; }
        public void setSingle_other_texts_prob(Map<String, List<Integer>> single_other_texts_prob) { this.single_other_texts_prob = single_other_texts_prob; }
        public Map<String, List<Integer>> getDroplist_prob() { return droplist_prob; }
        public void setDroplist_prob(Map<String, List<Integer>> droplist_prob) { this.droplist_prob = droplist_prob; }
        public Map<String, List<Integer>> getMultiple_prob() { return multiple_prob; }
        public void setMultiple_prob(Map<String, List<Integer>> multiple_prob) { this.multiple_prob = multiple_prob; }
        public Map<String, List<String>> getOther_texts() { return other_texts; }
        public void setOther_texts(Map<String, List<String>> other_texts) { this.other_texts = other_texts; }
        public Map<String, List<Integer>> getOther_texts_prob() { return other_texts_prob; }
        public void setOther_texts_prob(Map<String, List<Integer>> other_texts_prob) { this.other_texts_prob = other_texts_prob; }
        public Map<String, List<Object>> getMatrix_prob() { return matrix_prob; }
        public void setMatrix_prob(Map<String, List<Object>> matrix_prob) { this.matrix_prob = matrix_prob; }
        public Map<String, List<Integer>> getScale_prob() { return scale_prob; }
        public void setScale_prob(Map<String, List<Integer>> scale_prob) { this.scale_prob = scale_prob; }
        public Map<String, List<String>> getTexts() { return texts; }
        public void setTexts(Map<String, List<String>> texts) { this.texts = texts; }
        public Map<String, List<Integer>> getTexts_prob() { return texts_prob; }
        public void setTexts_prob(Map<String, List<Integer>> texts_prob) { this.texts_prob = texts_prob; }
    }

    /**
     * 执行问卷星Python脚本
     * @param scriptPath Python脚本路径
     * @param wjxConfig 问卷配置对象l
     * @return 执行结果
     */
    public static String executeWjxScript(String scriptPath, WjxConfig wjxConfig) {
        try {
            // 将配置对象转换为JSON字符串
            ObjectMapper objectMapper = new ObjectMapper();
            String configJson = objectMapper.writeValueAsString(wjxConfig);
            
            // 调用Python脚本并传递JSON参数
            return executePythonScript(scriptPath, configJson);
            
        } catch (JsonProcessingException e) {
            throw new RuntimeException("JSON序列化失败", e);
        }
    }

    /**
     * 通用Python脚本执行方法
     */
    public static String executePythonScript(String scriptPath, String... args) {
        return executePythonScriptSync(scriptPath, args);
    }

    /**
     * 同步执行Python脚本
     */
    public static String executePythonScriptSync(String scriptPath, String... args) {
        File tempFile = null;
        try {
            List<String> command = new ArrayList<>();
            command.add("python");  // 或者 "python3"，取决于你的环境
            command.add(scriptPath);

            // 判断是否需要使用临时文件（基于脚本名称和参数内容）
            boolean useTempFile = false;
            String configJson = null;

            // 检查脚本名称是否包含"wjx2"（问卷星脚本），并且参数长度大于0
            if (scriptPath.contains("wjx2") && args.length > 0) {
                // 尝试判断第一个参数是否为JSON字符串（以{开头）
                String firstArg = args[0];
                if (firstArg.trim().startsWith("{")) {
                    useTempFile = true;
                    configJson = firstArg;
                }
            }

            if (useTempFile) {
                // 创建临时文件来存储JSON配置
                tempFile = File.createTempFile("config", ".json");

                // 将JSON配置写入临时文件
                Files.write(tempFile.toPath(), configJson.getBytes(StandardCharsets.UTF_8));

                // 传递临时文件路径作为参数
                command.add(tempFile.getAbsolutePath());
            } else {
                // 直接传递所有参数
                Collections.addAll(command, args);
            }


            ProcessBuilder processBuilder = new ProcessBuilder(command);

            // 处理Windows系统的中文路径问题
            if (System.getProperty("os.name").toLowerCase().contains("win")) {
                // 设置编码为UTF-8
                processBuilder.environment().put("PYTHONIOENCODING", "utf-8");
            }

            Process process = processBuilder.start();

            // 异步读取输出和错误流（避免阻塞）
            StringBuilder output = new StringBuilder();
            StringBuilder errorOutput = new StringBuilder();

            // ---------------------
            // 输出流处理模块
            // ---------------------
            Thread outputThread = new Thread(() -> {
                try (BufferedReader reader = new BufferedReader(
                        new InputStreamReader(process.getInputStream(), StandardCharsets.UTF_8))) {
                    String line;
                    while ((line = reader.readLine()) != null) {
                        output.append(line).append("\n");
                    }
                } catch (IOException e) {
                    e.printStackTrace();
                }
            });
            outputThread.setDaemon(true);

            // 错误流处理线程（设为守护线程）
            Thread errorThread = new Thread(() -> {
                try (BufferedReader reader = new BufferedReader(
                        new InputStreamReader(process.getErrorStream(), StandardCharsets.UTF_8))) {
                    String line;
                    while ((line = reader.readLine()) != null) {
                        errorOutput.append(line).append("\n");
                    }
                } catch (IOException e) {
                    e.printStackTrace();
                }
            });
            errorThread.setDaemon(true);

            outputThread.start();
            errorThread.start();

            // ---------------------
            // 进程超时处理模块
            // ---------------------
            // 时间控制模式下刷题可能长达20小时以上（1000份×150秒÷2线程），设为48小时
            if (!process.waitFor(172800, TimeUnit.SECONDS)) {
                // 分阶段销毁进程，优先优雅终止
                process.destroy();

                // 等待5秒让进程自行清理
                if (!process.waitFor(5, TimeUnit.SECONDS)) {
                    // 强制终止前使用系统特定的方式
                    if (System.getProperty("os.name").toLowerCase().contains("win")) {
                        // Windows下使用taskkill命令
                        try {
                            Process killProcess = Runtime.getRuntime().exec(
                                "taskkill /F /PID " + process.pid());
                            killProcess.waitFor();
                        } catch (Exception e) {
                            // 如果taskkill失败，回退到标准方法
                            process.destroyForcibly();
                        }
                    } else {
                        // Unix系统发送SIGTERM信号
                        process.destroyForcibly();
                    }
                }
                throw new RuntimeException("Python脚本执行超时");
            }

            outputThread.join();
            errorThread.join();

            int exitCode = process.exitValue();
            if (exitCode != 0) {
                String errorMessage = "Python脚本执行失败，退出码: " + exitCode
                        + ", 错误信息: " + errorOutput;
                System.err.println(errorMessage);
                throw new RuntimeException(errorMessage);
            }

            return output.toString();
        } catch (IOException | InterruptedException e) {
            throw new RuntimeException("执行Python脚本异常", e);
        } finally {
            // 清理临时文件
            if (tempFile != null && tempFile.exists()) {
                try {
                    tempFile.delete();
                } catch (Exception e) {
                    // 忽略删除失败的异常
                    System.err.println("删除临时文件失败: " + e.getMessage());
                }
            }
        }
    }

    /**
     * 异步执行Python脚本（用于长时间运行的任务）
     */
    public static void executePythonScriptAsync(String scriptPath, String configJson, String taskId, ProgressCallback callback) {
        // 在新线程中执行
        new Thread(() -> {
            try {
                String result = executePythonScriptSync(scriptPath, configJson);
                if (callback != null) {
                    callback.onComplete(taskId, result);
                }
            } catch (Exception e) {
                if (callback != null) {
                    callback.onError(taskId, e);
                }
            }
        }).start();
    }

    /**
     * 进度回调接口
     */
    public interface ProgressCallback {
        void onComplete(String taskId, String result);
        void onError(String taskId, Exception error);
    }

    // 测试示例
    public static void main(String[] args) {
        try {
            // 1. 创建配置对象
            WjxConfig config = new WjxConfig();
            
            // 2. 设置基础配置
            config.setUrl("https://v.wjx.cn/vm/Qu9AV5R.aspx");
            config.setTarget_num(3);  // 目标份数
            config.setNum_threads(3);  // 线程数
            config.setUse_ip(false);  // 不使用代理IP
            
            // 3. 设置题目配置
            // 单选题配置
            Map<String, List<Integer>> singleProb = new HashMap<>();
            singleProb.put("1", Arrays.asList(1, 1));  // 题号1，两个选项各50%概率
            singleProb.put("4", Arrays.asList(1, 1));  // 题号4，两个选项各50%概率
            config.setSingle_prob(singleProb);
            
            // 单选题其他选项文本配置
            Map<String, List<String>> singleOtherTexts = new HashMap<>();
            singleOtherTexts.put("1", Arrays.asList("其他选项文本1", "其他选项文本2"));
            config.setSingle_other_texts(singleOtherTexts);
            
            Map<String, List<Integer>> singleOtherTextsProb = new HashMap<>();
            singleOtherTextsProb.put("1", Arrays.asList(70, 30));
            config.setSingle_other_texts_prob(singleOtherTextsProb);
            
            // 下拉框配置
            Map<String, List<Integer>> dropListProb = new HashMap<>();
            dropListProb.put("1", Arrays.asList(2, 1, 1));
            config.setDroplist_prob(dropListProb);
            
            // 多选题配置
            Map<String, List<Integer>> multipleProb = new HashMap<>();
            multipleProb.put("2", Arrays.asList(100, 30, 23, 100));
            config.setMultiple_prob(multipleProb);
            
            // 多选题其他选项文本配置
            Map<String, List<String>> otherTexts = new HashMap<>();
            otherTexts.put("2", Arrays.asList("其他选项的文本1", "其他选项的文本2"));
            config.setOther_texts(otherTexts);
            
            Map<String, List<Integer>> otherTextsProb = new HashMap<>();
            otherTextsProb.put("2", Arrays.asList(10, 90));
            config.setOther_texts_prob(otherTextsProb);
            
            // 矩阵题配置
            Map<String, List<Object>> matrixProb = new HashMap<>();
            List<Object> matrix5 = new ArrayList<>();
            matrix5.add(Arrays.asList(1, 0, 0));  // 第1个子题
            matrix5.add(-1);  // 第2个子题
            matrixProb.put("5", matrix5);
            config.setMatrix_prob(matrixProb);
            
            // 量表题配置
            Map<String, List<Integer>> scaleProb = new HashMap<>();
            scaleProb.put("6", Arrays.asList(0,1,2,3,4,5,6,7,8,9,10));
            config.setScale_prob(scaleProb);
            
            // 填空题配置
            Map<String, List<String>> texts = new HashMap<>();
            texts.put("3", Arrays.asList("内容1", "内容2", "内容3"));
            config.setTexts(texts);
            
            Map<String, List<Integer>> textsProb = new HashMap<>();
            textsProb.put("3", Arrays.asList(1, 1, 1));
            config.setTexts_prob(textsProb);
            
            // 4. 执行Python脚本
            String result = executeWjxScript("path/to/your/wjx2.py", config);
            System.out.println("执行结果: " + result);
            
        } catch (Exception e) {
            e.printStackTrace();
        }
    }
}