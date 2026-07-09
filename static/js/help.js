// static/js/help.js
// 全局帮助系统 - 双语帮助中心

/**
 * 打开帮助模态框并滚动到指定章节
 * @param {string} sectionId - 章节ID（如 'help-dashboard'），不传则滚动到顶部
 */
function openHelp(sectionId) {
    const modal = document.getElementById('globalHelpModal');
    if (!modal) {
        console.warn('帮助模态框不存在');
        return;
    }

    // 获取或创建模态框实例
    let modalInstance = bootstrap.Modal.getInstance(modal);
    if (!modalInstance) {
        modalInstance = new bootstrap.Modal(modal, {
            backdrop: true,
            keyboard: true
        });
    }

    // 监听模态框显示完成事件
    modal.addEventListener('shown.bs.modal', function onShown() {
        modal.removeEventListener('shown.bs.modal', onShown);
        
        if (sectionId) {
            const targetElement = document.getElementById(sectionId);
            if (targetElement) {
                // 延迟一点点以确保渲染完成
                setTimeout(() => {
                    targetElement.scrollIntoView({
                        behavior: 'smooth',
                        block: 'start'
                    });
                    // 添加高亮效果
                    targetElement.style.transition = 'background-color 0.6s ease';
                    targetElement.style.backgroundColor = 'rgba(13, 110, 253, 0.08)';
                    setTimeout(() => {
                        targetElement.style.backgroundColor = 'transparent';
                    }, 1500);
                }, 300);
            }
        }
    });

    // 显示模态框
    modalInstance.show();
}

/**
 * 根据当前页面自动定位帮助章节
 * 用于页面级的帮助按钮
 */
function openPageHelp() {
    // 通过页面URL或特定元素ID判断当前页面
    const path = window.location.pathname;
    let sectionId = 'help-dashboard'; // 默认

    if (path.includes('/admin/trainings') || path.includes('/admin/training')) {
        sectionId = 'help-trainings';
    } else if (path.includes('/admin/exams') || path.includes('/admin/exam')) {
        sectionId = 'help-exams';
    } else if (path.includes('/admin/users') || path.includes('/admin/user')) {
        sectionId = 'help-users';
    } else if (path.includes('/admin/interviews') || path.includes('/admin/interview')) {
        sectionId = 'help-interviews';
    } else if (path.includes('/admin/wh') || path.includes('/admin/warehouse')) {
        sectionId = 'help-warehouse';
    } else if (path.includes('/admin/privacy')) {
        sectionId = 'help-privacy';
    } else if (path.includes('/admin/training/photos') || path.includes('/training/photos')) {
        sectionId = 'help-photos';
    }

    openHelp(sectionId);
}

/**
 * 初始化帮助系统
 * 为所有带有 data-help 属性的元素绑定点击事件
 */
function initHelpSystem() {
    // 为所有帮助按钮绑定事件
    document.querySelectorAll('[data-help]').forEach(element => {
        element.addEventListener('click', function(e) {
            e.preventDefault();
            const sectionId = this.dataset.help;
            if (sectionId) {
                openHelp(sectionId);
            } else {
                openPageHelp();
            }
        });
    });

    // 为目录链接添加平滑滚动
    document.querySelectorAll('.help-toc-link').forEach(link => {
        link.addEventListener('click', function(e) {
            e.preventDefault();
            const targetId = this.getAttribute('href');
            if (targetId && targetId.startsWith('#')) {
                const target = document.querySelector(targetId);
                if (target) {
                    target.scrollIntoView({
                        behavior: 'smooth',
                        block: 'start'
                    });
                }
            }
        });
    });
}

// DOM加载完成后初始化
document.addEventListener('DOMContentLoaded', function() {
    // 确保Bootstrap已加载
    if (typeof bootstrap !== 'undefined') {
        initHelpSystem();
    } else {
        // 如果Bootstrap尚未加载，等待
        const checkBootstrap = setInterval(() => {
            if (typeof bootstrap !== 'undefined') {
                clearInterval(checkBootstrap);
                initHelpSystem();
            }
        }, 100);
    }
});