// static/js/timezone.js
/**
 * 获取并设置用户时区
 * 完全依赖浏览器时区，无硬编码默认值
 */
async function initUserTimezone() {
    // 获取浏览器时区
    const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
    
    if (!timezone) {
        console.warn('无法检测浏览器时区，使用 UTC');
        return;
    }
    
    // 检查是否需要更新
    const savedTimezone = localStorage.getItem('user_timezone');
    const sessionTimezone = sessionStorage.getItem('user_timezone');
    
    if (savedTimezone !== timezone) {
        localStorage.setItem('user_timezone', timezone);
        
        // 发送到服务器
        try {
            const response = await fetch('/api/user/timezone', {
                method: 'POST',
                headers: { 
                    'Content-Type': 'application/json',
                    'X-Timezone': timezone  // 也可以通过自定义头传递
                },
                body: JSON.stringify({ timezone: timezone })
            });
            
            if (response.ok) {
                sessionStorage.setItem('user_timezone', timezone);
            }
        } catch(e) {
            console.error('同步时区失败:', e);
        }
    }
}

// 页面加载时初始化
document.addEventListener('DOMContentLoaded', initUserTimezone);

// 监听语言变化时重新同步（可选）
window.addEventListener('app:languageChanged', () => {
    initUserTimezone();
});

// 导出函数供其他模块使用
window.initUserTimezone = initUserTimezone;
window.getUserTimezone = () => Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';