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
        if (lang === this.currentLang) return;
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

    applyTranslations() {
        // 翻译带 data-i18n 属性的元素
        document.querySelectorAll('[data-i18n]').forEach(el => {
            const key = el.getAttribute('data-i18n');
            const translation = this.translations[key];
            if (translation === undefined) return;
            if (el.tagName === 'TITLE') {
                document.title = translation;
                return;
            }
            if (el.children.length === 0) {
                el.textContent = translation;
            } else {
                for (let node of el.childNodes) {
                    if (node.nodeType === Node.TEXT_NODE && node.textContent.trim() !== '') {
                        node.textContent = translation;
                        break;
                    }
                }
            }
        });
        // 翻译 placeholder、title、alt
        document.querySelectorAll('[data-i18n-placeholder]').forEach(el => {
            const key = el.getAttribute('data-i18n-placeholder');
            if (this.translations[key]) el.placeholder = this.translations[key];
        });
        document.querySelectorAll('[data-i18n-title]').forEach(el => {
            const key = el.getAttribute('data-i18n-title');
            if (this.translations[key]) el.title = this.translations[key];
        });
        document.querySelectorAll('[data-i18n-alt]').forEach(el => {
            const key = el.getAttribute('data-i18n-alt');
            if (this.translations[key]) el.alt = this.translations[key];
        });
    }

    subscribe(fn) {
        this.observers.push(fn);
    }

    notifyObservers() {
        this.observers.forEach(fn => fn(this.currentLang, this.translations));
    }

    t(key) {
        return this.translations[key] || key;
    }
}

window.i18n = new I18n();
