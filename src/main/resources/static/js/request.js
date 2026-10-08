// RESTful风格请求工具

// 基础配置
const baseUrl = '';

// 通用请求函数
function request(url, method = 'GET', data = null, headers = {}, options = {}) {
    return new Promise((resolve, reject) => {
        let fullUrl = baseUrl + url;

        // options.timeout：超时毫秒数，到点主动 abort，避免请求永远挂着
        const controller = options.timeout ? new AbortController() : null;
        let timer = null;

        const fetchOptions = {
            method: method,
            headers: {
                'Content-Type': 'application/json',
                ...headers
            },
            credentials: 'include'
        };

        if (controller) {
            fetchOptions.signal = controller.signal;
            timer = setTimeout(() => controller.abort(), options.timeout);
        }

        // 根据请求方法处理数据
        if (data) {
            if (method === 'GET' || method === 'DELETE') {
                // GET/DELETE请求将数据转换为查询参数
                const queryParams = new URLSearchParams(data).toString();
                fullUrl = queryParams ? `${fullUrl}?${queryParams}` : fullUrl;
            } else {
                // POST/PUT/PATCH请求处理
                if (typeof data === 'string') {
                    // 如果是字符串，直接使用
                    fetchOptions.body = data;
                } else {
                    // 否则转换为JSON
                    fetchOptions.body = JSON.stringify(data);
                }
            }
        }

        // 发送请求
        fetch(fullUrl, fetchOptions)
            .then(response => {
                if (!response.ok) {
                    throw new Error(`HTTP error! status: ${response.status}`);
                }
                return response.json();
            })
            .then(data => {
                // 检查后端返回的业务错误码
                if (data.code !== undefined && data.code !== 1) {
                    throw new Error(data.msg || '请求失败');
                }
                resolve(data);
            })
            .catch(error => {
                if (error && error.name === 'AbortError') {
                    reject(new Error('请求超时，请稍后重试'));
                    return;
                }
                reject(error);
            })
            .finally(() => {
                if (timer) {
                    clearTimeout(timer);
                }
            });
    });
}

// 封装常用请求方法
const requestUtil = {
    // GET请求
    get(url, data = null, headers = {}, options = {}) {
        return request(url, 'GET', data, headers, options);
    },
    
    // POST请求
    post(url, data = null, headers = {}, options = {}) {
        return request(url, 'POST', data, headers, options);
    },
    
    // PUT请求
    put(url, data = null, headers = {}, options = {}) {
        return request(url, 'PUT', data, headers, options);
    },
    
    // DELETE请求
    delete(url, data = null, headers = {}, options = {}) {
        return request(url, 'DELETE', data, headers, options);
    },
    
    // PATCH请求
    patch(url, data = null, headers = {}, options = {}) {
        return request(url, 'PATCH', data, headers, options);
    }
};

// 将请求工具挂载到window对象上，以便普通脚本访问
window.requestUtil = requestUtil;
