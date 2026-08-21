// static/js/timezone.js - 添加防抖和缓存检查

/**
 * 获取并设置用户时区（带防抖和缓存）
 */
let timezoneSetPending = false;
let lastSetTime = 0;

async function initUserTimezone() {
    // 获取浏览器时区
    const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
    
    if (!timezone) {
        console.warn('无法检测浏览器时区，使用 UTC');
        return;
    }
    
    // ✅ 防抖：5秒内不重复设置
    const now = Date.now();
    if (now - lastSetTime < 5000) {
        return;
    }
    
    // ✅ 检查本地缓存是否一致
    const savedTimezone = localStorage.getItem('user_timezone');
    if (savedTimezone === timezone) {
        // 时区没变，不发送请求
        return;
    }
    
    if (timezoneSetPending) {
        return;
    }
    
    timezoneSetPending = true;
    lastSetTime = now;
    
    try {
        localStorage.setItem('user_timezone', timezone);
        
        const response = await fetch('/api/user/timezone', {
            method: 'POST',
            headers: { 
                'Content-Type': 'application/json',
                'X-Timezone': timezone
            },
            body: JSON.stringify({ timezone: timezone })
        });
        
        if (response.ok) {
            sessionStorage.setItem('user_timezone', timezone);
        }
    } catch(e) {
        console.error('同步时区失败:', e);
    } finally {
        timezoneSetPending = false;
    }
}

// 页面加载时初始化
document.addEventListener('DOMContentLoaded', initUserTimezone);

// ✅ 语言切换时也检查，但复用防抖逻辑
window.addEventListener('app:languageChanged', () => {
    // 清空缓存强制重新检查（但防抖仍然有效）
    setTimeout(initUserTimezone, 100);
});

// 导出函数供其他模块使用
window.initUserTimezone = initUserTimezone;
window.getUserTimezone = () => Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';