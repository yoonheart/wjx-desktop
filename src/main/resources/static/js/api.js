// API请求封装
// 依赖request.js提供的requestUtil工具

// 问卷解析相关API
const wjxApi = {
    // 解析问卷星链接
    // 后端解析脚本的超时是 120 秒，客户端放到 150 秒：
    // 这样超时能先由服务端返回可读错误，客户端超时只作为兜底。
    analyzeUrl(url) {
        return requestUtil.get('/api/analysis', { url: url }, {}, { timeout: 150000 });
    },

    // 启动刷问卷任务（异步）
    brushStart(data) {
        // 添加标识表示这是一个启动任务的请求
        data.startTask = true;
        return requestUtil.post('/api/brush', data);
    },

    // 查询 Edge 驱动状态
    driverStatus() {
        return requestUtil.get('/api/driver/status');
    },

    // 触发驱动下载
    driverDownload() {
        return requestUtil.post('/api/driver/download');
    },

    // 查询驱动下载进度
    driverProgress() {
        return requestUtil.get('/api/driver/progress');
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