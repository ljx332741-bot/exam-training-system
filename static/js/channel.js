// static/js/channel.js - 全局跨页面通信工具

/**
 * 全局跨页面通信工具类
 * 支持：考试更新、培训更新、用户权限变更等
 */
class PageChannel {
    constructor() {
        this.channels = {};
        this._listeners = {};
        this._useLocalStorage = false;
        this._init();
    }

    _init() {
        // 检查 BroadcastChannel 是否可用
        try {
            const testChannel = new BroadcastChannel('_test_channel');
            testChannel.close();
            this._useLocalStorage = false;
        } catch (e) {
            console.warn('📡 BroadcastChannel 不可用，使用 localStorage 降级方案');
            this._useLocalStorage = true;
            this._initLocalStorageListener();
        }
    }

    // ==================== 获取或创建频道 ====================

    /**
     * 获取或创建频道
     * @param {string} channelName - 频道名称
     * @returns {BroadcastChannel|null}
     */
    _getChannel(channelName) {
        if (this._useLocalStorage) {
            return null;
        }
        
        if (!this.channels[channelName]) {
            try {
                this.channels[channelName] = new BroadcastChannel(channelName);
                // 设置消息处理器
                this.channels[channelName].onmessage = (event) => {
                    this._handleMessage(channelName, event.data);
                };
            } catch (e) {
                console.warn(`📡 创建频道 ${channelName} 失败:`, e);
                return null;
            }
        }
        return this.channels[channelName];
    }

    // ==================== 消息处理 ====================

    /**
     * 处理接收到的消息
     */
    _handleMessage(channelName, data) {
        if (!data || !data.type) return;
        
        console.log(`📡 收到消息 [${channelName}]:`, data);
        
        // 触发特定频道的事件
        const channelListeners = this._listeners[channelName] || [];
        channelListeners.forEach(callback => {
            try {
                callback(data);
            } catch (e) {
                console.error('消息回调执行失败:', e);
            }
        });
        
        // 触发全局事件
        const globalListeners = this._listeners['*'] || [];
        globalListeners.forEach(callback => {
            try {
                callback(channelName, data);
            } catch (e) {
                console.error('全局回调执行失败:', e);
            }
        });
        
        // 也触发 DOM 事件
        const event = new CustomEvent('page:message', {
            detail: { channel: channelName, data: data }
        });
        document.dispatchEvent(event);
    }

    // ==================== localStorage 降级方案 ====================

    _initLocalStorageListener() {
        // 监听 storage 事件
        window.addEventListener('storage', (e) => {
            if (e.key && e.key.startsWith('page_channel_')) {
                try {
                    const data = JSON.parse(e.newValue);
                    const channelName = e.key.replace('page_channel_', '');
                    // 检查是否是自己发送的（避免循环）
                    if (data._sender === this._getSenderId()) {
                        return;
                    }
                    this._handleMessage(channelName, data);
                } catch (err) {
                    // 忽略解析错误
                }
            }
        });
    }

    _getSenderId() {
        if (!window._pageChannelSenderId) {
            window._pageChannelSenderId = 'page_' + Date.now() + '_' + Math.random().toString(36).substr(2, 6);
        }
        return window._pageChannelSenderId;
    }

    // ==================== 公共 API ====================

    /**
     * 发送消息到指定频道
     * @param {string} channelName - 频道名称
     * @param {string} type - 消息类型
     * @param {Object} data - 消息数据
     */
    send(channelName, type, data = {}) {
        const message = {
            type: type,
            timestamp: Date.now(),
            ...data
        };
        
        console.log(`📡 发送消息 [${channelName}]:`, message);
        
        // 方式1: BroadcastChannel
        const channel = this._getChannel(channelName);
        if (channel) {
            try {
                channel.postMessage(message);
                return true;
            } catch (e) {
                console.warn('📡 BroadcastChannel 发送失败:', e);
            }
        }
        
        // 方式2: localStorage 降级
        if (this._useLocalStorage) {
            try {
                const key = 'page_channel_' + channelName;
                const payload = { ...message, _sender: this._getSenderId() };
                localStorage.setItem(key, JSON.stringify(payload));
                // 立即清除，只作为触发信号
                setTimeout(() => {
                    localStorage.removeItem(key);
                }, 50);
                return true;
            } catch (e) {
                console.warn('📡 localStorage 发送失败:', e);
            }
        }
        
        return false;
    }

    /**
     * 监听频道消息
     * @param {string} channelName - 频道名称，'*' 表示所有频道
     * @param {Function} callback - 回调函数
     * @returns {Function} 取消监听的函数
     */
    on(channelName, callback) {
        if (!this._listeners[channelName]) {
            this._listeners[channelName] = [];
        }
        this._listeners[channelName].push(callback);
        
        // 如果频道不存在，预创建
        if (channelName !== '*') {
            this._getChannel(channelName);
        }
        
        // 返回取消监听函数
        return () => {
            const idx = this._listeners[channelName].indexOf(callback);
            if (idx !== -1) {
                this._listeners[channelName].splice(idx, 1);
            }
        };
    }

    /**
     * 移除所有监听器
     * @param {string} channelName - 频道名称，不传则移除所有
     */
    off(channelName) {
        if (channelName) {
            delete this._listeners[channelName];
        } else {
            this._listeners = {};
        }
    }

    /**
     * 关闭所有频道连接
     */
    close() {
        Object.keys(this.channels).forEach(key => {
            try {
                this.channels[key].close();
            } catch (e) {
                // 忽略
            }
        });
        this.channels = {};
        this._listeners = {};
    }
}

// ============================================================
// 创建全局单例
// ============================================================
if (!window.PageChannel) {
    window.PageChannel = new PageChannel();
}

// ============================================================
// 预定义频道名称（常量）
// ============================================================
window.CHANNELS = {
    EXAM: 'exam_update_channel',
    TRAINING: 'training_update_channel',
    USER: 'user_update_channel',
    SYSTEM: 'system_notification_channel'
};

// ============================================================
// 便捷发送函数
// ============================================================

/**
 * 发送考试更新通知
 * @param {number} examId - 更新的考试ID
 * @param {string} action - 操作类型: 'update', 'delete', 'create', 'reimport'
 */
window.notifyExamUpdate = function(examId, action = 'update') {
    window.PageChannel.send(window.CHANNELS.EXAM, 'exam_updated', {
        exam_id: examId,
        action: action
    });
};

/**
 * 发送培训更新通知
 * @param {number} trainingId - 更新的培训ID
 * @param {string} action - 操作类型: 'update', 'delete', 'create', 'push'
 */
window.notifyTrainingUpdate = function(trainingId, action = 'update') {
    window.PageChannel.send(window.CHANNELS.TRAINING, 'training_updated', {
        training_id: trainingId,
        action: action
    });
};

/**
 * 监听考试更新
 * @param {Function} callback - 回调函数，接收 { exam_id, action }
 * @returns {Function} 取消监听的函数
 */
window.onExamUpdate = function(callback) {
    return window.PageChannel.on(window.CHANNELS.EXAM, (data) => {
        if (data.type === 'exam_updated') {
            callback(data);
        }
    });
};

/**
 * 监听培训更新
 * @param {Function} callback - 回调函数，接收 { training_id, action }
 * @returns {Function} 取消监听的函数
 */
window.onTrainingUpdate = function(callback) {
    return window.PageChannel.on(window.CHANNELS.TRAINING, (data) => {
        if (data.type === 'training_updated') {
            callback(data);
        }
    });
};