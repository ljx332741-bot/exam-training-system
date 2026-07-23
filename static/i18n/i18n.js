// static/i18n/i18n.js - 完整修复版

class I18n {
    constructor() {
        this.currentLang = 'zh';
        this.translations = {};
        this.observers = [];
        this._initialized = false;
        this._initPromise = null;
        this._initCallbacks = [];
        this.init();
    }

    async init() {
        // 防止重复初始化
        if (this._initPromise) {
            return this._initPromise;
        }
        
        this._initPromise = (async () => {
            try {
                const savedLang = localStorage.getItem('app_lang');
                if (savedLang && ['zh', 'en'].includes(savedLang)) {
                    this.currentLang = savedLang;
                }
                await this.loadTranslations(this.currentLang);
                this._initialized = true;
                
                // ✅ 执行所有等待初始化的回调
                this._initCallbacks.forEach(cb => {
                    try { cb(); } catch(e) { console.warn('Init callback error:', e); }
                });
                this._initCallbacks = [];
                
                this.applyTranslations();
                this.translateAttributes();
                this.updateSwitchButton();
                this.notifyObservers();
                this.bindSwitchButton();
                
                // 触发就绪事件
                window.dispatchEvent(new CustomEvent('i18n:ready', { 
                    detail: { lang: this.currentLang } 
                }));
            } catch (e) {
                console.error('i18n 初始化失败:', e);
                // 即使失败也标记为已初始化，避免死锁
                this._initialized = true;
            }
        })();
        
        return this._initPromise;
    }

    // ✅ 新增：等待初始化完成的 Promise
    waitForInit() {
        if (this._initialized) {
            return Promise.resolve();
        }
        return new Promise((resolve) => {
            if (this._initialized) {
                resolve();
                return;
            }
            // 如果已经初始化完成，直接 resolve
            if (this._initPromise) {
                this._initPromise.then(resolve).catch(resolve);
                return;
            }
            // 否则加入回调队列
            this._initCallbacks.push(resolve);
        });
    }

    // 等待初始化完成（兼容旧接口）
    async waitForReady() {
        if (this._initialized) return;
        if (this._initPromise) {
            await this._initPromise;
        }
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
        await this.waitForInit();
        
        if (lang === this.currentLang) {
            // 语言相同，只重新应用翻译
            this.applyTranslations();
            this.translateAttributes();
            this.updateSwitchButton();
            this.notifyObservers();
            return;
        }
        
        this.currentLang = lang;
        localStorage.setItem('app_lang', lang);
        await this.loadTranslations(lang);
        this.applyTranslations();
        this.translateAttributes();
        this.updateSwitchButton();
        this.notifyObservers();
        window.dispatchEvent(new CustomEvent('app:languageChanged', { detail: { lang } }));
    }

    // 兼容旧接口
    setLang(lang) {
        return this.setLanguage(lang);
    }

    updateSwitchButton() {
        const btnTextSpan = document.getElementById('langSwitchText');
        if (!btnTextSpan) return;
        btnTextSpan.textContent = this.currentLang === 'zh' ? '中|EN' : 'EN|中';
    }

    bindSwitchButton() {
        const btn = document.getElementById('langSwitchBtn');
        if (!btn) return;
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
    // 统一的属性翻译方法
    // ============================================================
    translateAttributes() {
        // 1. 翻译 data-i18n-title
        document.querySelectorAll('[data-i18n-title]').forEach(el => {
            const key = el.getAttribute('data-i18n-title');
            if (this.translations[key]) {
                el.title = this.translations[key];
            }
        });
        
        // 2. 翻译 data-i18n-title-key（支持参数）
        document.querySelectorAll('[data-i18n-title-key]').forEach(el => {
            const key = el.getAttribute('data-i18n-title-key');
            if (!key) return;
            
            let params = {};
            const paramsAttr = el.getAttribute('data-i18n-title-params');
            if (paramsAttr) {
                try {
                    params = JSON.parse(paramsAttr);
                } catch(e) {
                    console.warn('解析 title params 失败:', e);
                }
            }
            
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
    // 应用翻译到页面
    // ============================================================
    applyTranslations() {
        document.querySelectorAll('[data-i18n]').forEach(el => {
            const key = el.getAttribute('data-i18n');
            if (key === undefined || key === null || key === '') return;
            
            let translation = this.translations[key];
            if (translation === undefined) {
                translation = key;
            }

            // 检查是否有参数
            const paramsAttr = el.getAttribute('data-i18n-params');
            if (paramsAttr) {
                try {
                    const params = JSON.parse(paramsAttr);
                    if (typeof params === 'object' && params !== null) {
                        Object.keys(params).forEach(k => {
                            const value = params[k];
                            if (value !== undefined && value !== null) {
                                translation = translation.replace(new RegExp(`\\{${k}\\}`, 'g'), String(value));
                            }
                        });
                    }
                } catch(e) {
                    console.warn('解析 data-i18n-params 失败:', e);
                }
            }
            
            // 处理 TITLE 标签
            if (el.tagName === 'TITLE') {
                document.title = translation;
                return;
            }
            
            // 检查是否需要 HTML 渲染
            const useHtml = el.hasAttribute('data-i18n-html') || 
                           el.closest('.help-section') !== null ||
                           el.closest('.help-item') !== null ||
                           el.classList.contains('help-html');
            
            if (useHtml) {
                el.innerHTML = translation;
            } else {
                if (el.children.length === 0) {
                    el.textContent = translation;
                } else {
                    let replaced = false;
                    for (let node of el.childNodes) {
                        if (node.nodeType === Node.TEXT_NODE) {
                            node.textContent = translation;
                            replaced = true;
                            break;
                        }
                    }
                    if (!replaced) {
                        el.appendChild(document.createTextNode(translation));
                    }
                }
            }
        });

        this.translateAttributes();
    }

    subscribe(fn) {
        this.observers.push(fn);
    }

    notifyObservers() {
        this.observers.forEach(fn => fn(this.currentLang, this.translations));
    }
}

// ============================================================
// 全局初始化 - 使用单例模式
// ============================================================
if (!window.i18n) {
    window.i18n = new I18n();
}

// 兼容全局 t() 函数
window.t = function(key, params = {}) {
    if (!window.i18n || !window.i18n._initialized) {
        return key;
    }
    return window.i18n.t(key, params);
};

// ============================================================
// 等待 i18n 就绪的工具函数
// ============================================================
window.waitForI18n = function() {
    return new Promise((resolve) => {
        if (window.i18n && window.i18n._initialized) {
            resolve();
            return;
        }
        // 使用 waitForInit 方法
        if (window.i18n && typeof window.i18n.waitForInit === 'function') {
            window.i18n.waitForInit().then(resolve);
            return;
        }
        // 降级方案：监听事件
        const handler = function(e) {
            document.removeEventListener('i18n:ready', handler);
            resolve();
        };
        document.addEventListener('i18n:ready', handler);
        
        // 超时保护
        setTimeout(() => {
            document.removeEventListener('i18n:ready', handler);
            console.warn('i18n 加载超时，强制继续');
            resolve();
        }, 3000);
    });
};