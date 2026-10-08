// API请求封装
// 依赖request.js提供的requestUtil工具

// 问卷解析相关API
const wjxApi = {
    // 解析问卷星链接
    analyzeUrl(url) {
        return requestUtil.get('/api/analysis', { url: url });
    },

    // 启动刷问卷任务（异步）
    brushStart(data) {
        // 添加标识表示这是一个启动任务的请求
        data.startTask = true;
        return requestUtil.post('/api/brush', data);
    }
};

// 其他API模块可以在这里继续添加
// const userApi = {
//     // 用户相关API
// };

// 将API模块挂载到window对象上，以便普通脚本访问
window.api = {
    wjx: wjxApi
    // 其他API模块可以在这里继续添加
    // user: userApi
};