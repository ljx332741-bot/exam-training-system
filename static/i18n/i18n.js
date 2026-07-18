// static/i18n/i18n.js
class I18n {
    constructor() {
        this.currentLang = 'zh';
        this.translations = {};
        this.observers = [];
        this.init();
    }

    async init() {
        const savedLang = localStorage.getItem('app_lang');
        if (savedLang && ['zh', 'en'].includes(savedLang)) {
            this.currentLang = savedLang;
        }
        await this.loadTranslations(this.currentLang);
        this.applyTranslations();
        this.updateSwitchButton();
        this.notifyObservers();
        this.bindSwitchButton();
    }

    async loadTranslations(lang) {
        try {
            const response = await fetch(`/static/i18n/${lang}.json`);
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            this.translations = await response.json();
        } catch (e) {
            console.error('加载翻译文件失败', e);
            this.translations = {};
        }
    }

    async setLanguage(lang) {
        if (lang === this.currentLang) {
            this.applyTranslations();
            return;
        }
        this.currentLang = lang;
        localStorage.setItem('app_lang', lang);
        await this.loadTranslations(lang);
        this.applyTranslations();
        this.updateSwitchButton();
        this.notifyObservers();
        // 触发自定义事件，便于监听
        window.dispatchEvent(new CustomEvent('app:languageChanged', { detail: { lang } }));
    }

    updateSwitchButton() {
        const btnTextSpan = document.getElementById('langSwitchText');
        if (!btnTextSpan) return;
        btnTextSpan.textContent = this.currentLang === 'zh' ? '中|EN' : 'EN|中';
    }

    bindSwitchButton() {
        const btn = document.getElementById('langSwitchBtn');
        if (!btn) return;
        // 移除旧监听器
        if (btn._langHandler) btn.removeEventListener('click', btn._langHandler);
        const handler = async () => {
            const newLang = this.currentLang === 'zh' ? 'en' : 'zh';
            await this.setLanguage(newLang);
        };
        btn._langHandler = handler;
        btn.addEventListener('click', handler);
    }

    // ============================================================
    // 支持参数替换的翻译函数
    // ============================================================
    t(key, params = {}) {
        let text = this.translations[key] || key;
        if (params && typeof params === 'object') {
            Object.keys(params).forEach(k => {
                text = text.replace(new RegExp(`\\{${k}\\}`, 'g'), params[k]);
            });
        }
        return text;
    }

    // ============================================================
    // 统一的属性翻译方法（支持带参数标题）
    // ============================================================
    translateAttributes() {
        // 1. 翻译 data-i18n-title（简单文本，无参数）
        document.querySelectorAll('[data-i18n-title]').forEach(el => {
            const key = el.getAttribute('data-i18n-title');
            if (this.translations[key]) {
                el.title = this.translations[key];
            }
        });
        
        // 2. 翻译 data-i18n-title-key（支持参数）- ✅ 修复版
        document.querySelectorAll('[data-i18n-title-key]').forEach(el => {
            const key = el.getAttribute('data-i18n-title-key');
            if (!key) return;
            
            // 解析参数
            let params = {};
            const paramsAttr = el.getAttribute('data-i18n-title-params');
            if (paramsAttr) {
                try {
                    params = JSON.parse(paramsAttr);
                } catch(e) {
                    console.warn('解析 title params 失败:', e);
                }
            }
            
            // ✅ 使用 t() 方法统一处理翻译和参数替换
            const translated = this.t(key, params);
            if (translated && translated !== key) {
                el.title = translated;
            }
        });

        // 3. 翻译 data-i18n-placeholder
        document.querySelectorAll('[data-i18n-placeholder]').forEach(el => {
            const key = el.getAttribute('data-i18n-placeholder');
            if (this.translations[key]) {
                el.placeholder = this.translations[key];
            }
        });

        // 4. 翻译 data-i18n-alt
        document.querySelectorAll('[data-i18n-alt]').forEach(el => {
            const key = el.getAttribute('data-i18n-alt');
            if (this.translations[key]) {
                el.alt = this.translations[key];
            }
        });
    }

    // ============================================================
    // ✅ 扩展 applyTranslations，支持 data-i18n-html
    // ============================================================
    applyTranslations() {
        // ============================================================
        // 🔥 关键修改：处理 data-i18n 属性，支持 HTML 渲染
        // ============================================================
        document.querySelectorAll('[data-i18n]').forEach(el => {
            const key = el.getAttribute('data-i18n');
            if (key === undefined || key === null) return;
            
            let translation = this.translations[key];
            if (translation === undefined) return;

            // 🔥 检查是否有参数 - 优先检查 data-i18n-params
            const paramsAttr = el.getAttribute('data-i18n-params');
            if (paramsAttr) {
                try {
                    const params = JSON.parse(paramsAttr);
                    // 替换 {key} 格式的参数
                    if (typeof params === 'object' && params !== null) {
                        Object.keys(params).forEach(k => {
                            const value = params[k];
                            if (value !== undefined && value !== null) {
                                translation = translation.replace(new RegExp(`\\{${k}\\}`, 'g'), String(value));
                            }
                        });
                    }
                } catch(e) {
                    console.warn('解析 data-i18n-params 失败:', e, '原始值:', paramsAttr);
                }
            }
            
            // 处理 TITLE 标签
            if (el.tagName === 'TITLE') {
                document.title = translation;
                return;
            }
            
            // 🔥 核心逻辑：检查是否需要 HTML 渲染
            // 条件1：元素有 data-i18n-html 属性
            // 条件2：元素在帮助模态框内（.help-section 或 .help-item）
            const useHtml = el.hasAttribute('data-i18n-html') || 
                           el.closest('.help-section') !== null ||
                           el.closest('.help-item') !== null ||
                           el.classList.contains('help-html');
            
            if (useHtml) {
                // ✅ 使用 innerHTML 渲染 HTML 标签（如 <strong>）
                el.innerHTML = translation;
            } else {
                // ✅ 纯文本渲染
                // 如果元素有子节点，保留子节点，只替换文本节点
                if (el.children.length === 0) {
                    el.textContent = translation;
                } else {
                    // 有子节点时，只替换第一个文本节点
                    for (let node of el.childNodes) {
                        if (node.nodeType === Node.TEXT_NODE && node.textContent.trim() !== '') {
                            node.textContent = translation;
                            break;
                        }
                    }
                }
            }
        });

        // 调用统一的属性翻译方法
        this.translateAttributes();
    }

    subscribe(fn) {
        this.observers.push(fn);
    }

    notifyObservers() {
        this.observers.forEach(fn => fn(this.currentLang, this.translations));
    }
}

window.i18n = new I18n();

// 兼容全局 t() 函数（支持参数）
window.t = function(key, params = {}) {
    return window.i18n.t(key, params);
};