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
        this.updateSwitchButton();   // 更新按钮文字
        this.notifyObservers();
        this.bindSwitchButton();     // 绑定按钮事件
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
        this.updateSwitchButton();   // 切换后更新按钮文字
        this.notifyObservers();
    }

    // 更新语言切换按钮的显示文字
    updateSwitchButton() {
        const btnTextSpan = document.getElementById('langSwitchText');
        if (!btnTextSpan) return;
        if (this.currentLang === 'zh') {
            btnTextSpan.textContent = '中|EN';   // 当前中文，按钮显示“中|EN”，点击将切换到英文
        } else {
            btnTextSpan.textContent = 'EN|中';   // 当前英文，按钮显示“EN|中”，点击将切换到中文
        }
    }

    // 绑定按钮点击事件
    bindSwitchButton() {
        const btn = document.getElementById('langSwitchBtn');
        if (!btn) return;
        // 移除旧监听避免重复绑定（简单处理：先移除再添加）
        const newBtn = btn.cloneNode(true);
        btn.parentNode.replaceChild(newBtn, btn);
        newBtn.addEventListener('click', async () => {
            const newLang = this.currentLang === 'zh' ? 'en' : 'zh';
            await this.setLanguage(newLang);
        });
    }

    // 翻译静态 DOM 元素
    applyTranslations() {
        // 处理 data-i18n 元素
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
        // 处理 data-i18n-placeholder
        document.querySelectorAll('[data-i18n-placeholder]').forEach(el => {
            const key = el.getAttribute('data-i18n-placeholder');
            if (this.translations[key]) el.placeholder = this.translations[key];
        });
        // 处理 data-i18n-title
        document.querySelectorAll('[data-i18n-title]').forEach(el => {
            const key = el.getAttribute('data-i18n-title');
            if (this.translations[key]) el.title = this.translations[key];
        });
        // 处理 data-i18n-alt
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