// static/js/admin/dashboard/_training_list.js
// ============================================================
// 仪表盘培训列表操作模块（数据由服务端渲染，JS只负责操作交互）
// 修复版：采用与 list_trainings.html 一致的直接事件绑定方式
// ============================================================

const TrainingListModule = (function() {
    'use strict';
    
    let _isInitialized = false;
    let _currentEditingRow = null;
    let _countryTagInstances = new Map(); // 存储国家标签组件实例
    
    // ==================== 工具函数 ====================
    
    function escapeHtml(str) {
        if (!str) return '';
        return String(str).replace(/[&<>]/g, function(m) {
            if (m === '&') return '&amp;';
            if (m === '<') return '&lt;';
            if (m === '>') return '&gt;';
            return m;
        });
    }
    
    function getQuarterFromDate(dateStr) {
        if (!dateStr) return '-';
        try {
            const date = new Date(dateStr);
            if (isNaN(date.getTime())) return '-';
            const year = date.getFullYear();
            const month = date.getMonth() + 1;
            const quarter = Math.ceil(month / 3);
            return `${year}Q${quarter}`;
        } catch { return '-'; }
    }
    
    function parseTrainingCountries(training) {
        if (!training) return [];
        let countries = [];
        if (training.countries) {
            try {
                countries = typeof training.countries === 'string' ? 
                    JSON.parse(training.countries) : training.countries;
                if (!Array.isArray(countries)) countries = [countries];
            } catch {
                countries = [training.countries];
            }
        } else if (training.country) {
            countries = [training.country];
        }
        return countries.filter(c => c && c.trim());
    }
    
    // 本地时间转 UTC
    function localDateTimeToUTC(localDateTime) {
        if (!localDateTime) return '';
        try {
            const date = new Date(localDateTime);
            if (isNaN(date.getTime())) return '';
            return date.toISOString();
        } catch {
            return '';
        }
    }
    
    // 加载国家列表（带缓存）
    let _countryListCache = null;
    async function loadCountryList() {
        if (_countryListCache) return _countryListCache;
        try {
            const res = await fetch('/api/admin/export/recent_countries');
            _countryListCache = await res.json();
            return _countryListCache;
        } catch (err) {
            console.error('加载国家列表失败:', err);
            return [];
        }
    }

    // ==================== 多国家标签输入组件（内联版） ====================
    
    /**
     * 多国家标签输入组件 - 与 list_trainings.html 保持一致
     */
    class CountryTagInput {
        constructor(options = {}) {
            this.container = options.container;
            this.selectedCountries = options.selectedCountries || [];
            this.onChange = options.onChange || null;
            this.placeholder = options.placeholder || '输入国家名称或代码';
            this.maxTags = options.maxTags || 20;
            this._suggestions = [];
            this._currentIndex = -1;
            this._searchTimeout = null;
            this._allCountries = [];
            this._init();
        }
        
        async _init() {
            try {
                this._allCountries = await loadCountryList();
            } catch (err) {
                console.error('加载国家列表失败:', err);
                this._allCountries = [];
            }
            this._render();
            this._bindEvents();
            this._updateSelectedCount();
            if (this.selectedCountries.length > 0) {
                this._renderTags();
            }
        }
        
        _render() {
            const html = `
                <div class="country-tag-input-container">
                    <div class="tags-wrapper">
                        <input type="text" class="country-tag-input" 
                            placeholder="${this.placeholder}"
                            autocomplete="off"
                            autocorrect="off"
                            autocapitalize="off"
                            spellcheck="false">
                        <span class="selected-count-hint">已选 0 个国家</span>
                    </div>
                    <div class="country-suggestions-dropdown"></div>
                    <input type="hidden" class="country-codes-hidden" value="${this.selectedCountries.join(',')}">
                </div>
            `;
            
            this.container.innerHTML = html;
            
            this._input = this.container.querySelector('.country-tag-input');
            this._dropdown = this.container.querySelector('.country-suggestions-dropdown');
            this._tagsWrapper = this.container.querySelector('.tags-wrapper');
            this._hiddenField = this.container.querySelector('.country-codes-hidden');
            this._hint = this.container.querySelector('.selected-count-hint');
            this._container = this.container.querySelector('.country-tag-input-container');
        }
        
        _bindEvents() {
            if (!this._input) return;
            
            this._input.addEventListener('input', (e) => {
                const query = e.target.value.trim();
                clearTimeout(this._searchTimeout);
                if (query.length === 0) {
                    this._hideSuggestions();
                    return;
                }
                this._searchTimeout = setTimeout(() => {
                    this._searchCountries(query);
                }, 200);
            });
            
            this._input.addEventListener('keydown', (e) => {
                const suggestions = this._dropdown.querySelectorAll('.suggestion-item');
                
                switch (e.key) {
                    case 'Enter':
                        e.preventDefault();
                        if (this._currentIndex >= 0 && suggestions.length > 0) {
                            const selected = suggestions[this._currentIndex];
                            if (selected) {
                                this._addCountry(selected.dataset.code, selected.dataset.name);
                            }
                        } else if (this._input.value.trim().length > 0) {
                            this._tryAddCurrentInput();
                        }
                        break;
                    case 'ArrowDown':
                        e.preventDefault();
                        if (suggestions.length > 0) {
                            this._currentIndex = Math.min(this._currentIndex + 1, suggestions.length - 1);
                            this._highlightSuggestion(suggestions, this._currentIndex);
                        }
                        break;
                    case 'ArrowUp':
                        e.preventDefault();
                        if (suggestions.length > 0) {
                            this._currentIndex = Math.max(this._currentIndex - 1, 0);
                            this._highlightSuggestion(suggestions, this._currentIndex);
                        }
                        break;
                    case 'Backspace':
                        if (this._input.value === '' && this.selectedCountries.length > 0) {
                            const lastCode = this.selectedCountries[this.selectedCountries.length - 1];
                            this._removeCountry(lastCode);
                        }
                        break;
                    case 'Escape':
                        this._hideSuggestions();
                        this._input.blur();
                        break;
                }
            });
            
            document.addEventListener('click', (e) => {
                if (!this.container.contains(e.target)) {
                    this._hideSuggestions();
                }
            });
            
            this._input.addEventListener('focus', () => {
                if (this._input.value.trim().length > 0) {
                    this._searchCountries(this._input.value.trim());
                }
            });
        }
        
        _searchCountries(query) {
            if (!query || query.length === 0) {
                this._hideSuggestions();
                return;
            }
            
            const lowerQuery = query.toLowerCase();
            const results = [];
            const selectedCodes = new Set(this.selectedCountries);
            
            this._allCountries.forEach(country => {
                if (selectedCodes.has(country.code)) return;
                
                const nameZh = (country.name_zh || '').toLowerCase();
                const nameEn = (country.name_en || '').toLowerCase();
                const code = (country.code || '').toLowerCase();
                
                let matchType = null;
                let matchScore = 0;
                
                if (code === lowerQuery) {
                    matchType = 'code_exact';
                    matchScore = 100;
                } else if (code.startsWith(lowerQuery)) {
                    matchType = 'code_starts';
                    matchScore = 80;
                } else if (nameZh === lowerQuery) {
                    matchType = 'name_zh_exact';
                    matchScore = 90;
                } else if (nameZh.startsWith(lowerQuery)) {
                    matchType = 'name_zh_starts';
                    matchScore = 70;
                } else if (nameEn.startsWith(lowerQuery)) {
                    matchType = 'name_en_starts';
                    matchScore = 70;
                } else if (nameZh.includes(lowerQuery)) {
                    matchType = 'name_zh_includes';
                    matchScore = 40;
                } else if (nameEn.includes(lowerQuery)) {
                    matchType = 'name_en_includes';
                    matchScore = 40;
                } else if (code.includes(lowerQuery)) {
                    matchType = 'code_includes';
                    matchScore = 30;
                }
                
                if (matchType !== null) {
                    results.push({
                        ...country,
                        matchType,
                        matchScore,
                        displayName: window.i18n?.currentLang === 'en' ? country.name_en : country.name_zh
                    });
                }
            });
            
            results.sort((a, b) => b.matchScore - a.matchScore);
            this._renderSuggestions(results.slice(0, 15), query);
        }
        
        _renderSuggestions(results, query) {
            const dropdown = this._dropdown;
            if (!dropdown) return;
            
            if (results.length === 0) {
                dropdown.innerHTML = `
                    <div class="suggestion-empty">
                        <i class="bi bi-search"></i> 未找到匹配的国家
                    </div>
                `;
                dropdown.classList.add('show');
                return;
            }
            
            const highlightText = (text, query) => {
                if (!text || !query) return escapeHtml(text);
                const lowerText = text.toLowerCase();
                const lowerQuery = query.toLowerCase();
                const index = lowerText.indexOf(lowerQuery);
                if (index === -1) return escapeHtml(text);
                return escapeHtml(text.slice(0, index)) + 
                    `<span class="highlight">${escapeHtml(text.slice(index, index + query.length))}</span>` + 
                    escapeHtml(text.slice(index + query.length));
            };
            
            dropdown.innerHTML = results.map((country, index) => {
                const displayName = country.displayName || country.name_zh || country.name_en;
                const matchTypeMap = {
                    'code_exact': '精确匹配',
                    'code_starts': '代码匹配',
                    'name_zh_exact': '精确匹配',
                    'name_zh_starts': '名称匹配',
                    'name_en_starts': '名称匹配',
                    'name_zh_includes': '名称包含',
                    'name_en_includes': '名称包含',
                    'code_includes': '代码包含'
                };
                
                return `
                    <div class="suggestion-item" 
                        data-code="${escapeHtml(country.code)}" 
                        data-name="${escapeHtml(displayName)}"
                        data-index="${index}">
                        <span class="suggestion-code">${escapeHtml(country.code)}</span>
                        <span class="suggestion-name">${highlightText(displayName, query)}</span>
                        <span class="suggestion-match">${matchTypeMap[country.matchType] || '匹配'}</span>
                        <span class="suggestion-check"><i class="bi bi-plus-circle"></i></span>
                    </div>
                `;
            }).join('');
            
            dropdown.classList.add('show');
            this._currentIndex = -1;
            
            // 绑定点击事件
            dropdown.querySelectorAll('.suggestion-item').forEach(item => {
                item.addEventListener('click', () => {
                    this._addCountry(item.dataset.code, item.dataset.name);
                });
            });
        }
        
        _hideSuggestions() {
            if (this._dropdown) {
                this._dropdown.classList.remove('show');
            }
            this._currentIndex = -1;
        }
        
        _highlightSuggestion(items, index) {
            items.forEach((item, i) => {
                if (i === index) {
                    item.classList.add('active');
                    item.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
                } else {
                    item.classList.remove('active');
                }
            });
        }
        
        _addCountry(code, displayName) {
            if (this.selectedCountries.includes(code)) {
                this._container.classList.add('error');
                setTimeout(() => this._container.classList.remove('error'), 500);
                this._input.value = '';
                this._hideSuggestions();
                return;
            }
            
            if (this.selectedCountries.length >= this.maxTags) {
                if (typeof showToast === 'function') {
                    showToast(`最多只能选择 ${this.maxTags} 个国家`, 'warning');
                }
                return;
            }
            
            this.selectedCountries.push(code);
            this._updateHiddenField();
            this._renderTags();
            this._updateSelectedCount();
            this._input.value = '';
            this._hideSuggestions();
            
            if (typeof this.onChange === 'function') {
                this.onChange(this.selectedCountries);
            }
            
            setTimeout(() => this._input.focus(), 50);
        }
        
        _tryAddCurrentInput() {
            const query = this._input.value.trim();
            if (!query) return;
            
            const lowerQuery = query.toLowerCase();
            let matched = this._allCountries.find(c => 
                c.code.toLowerCase() === lowerQuery ||
                (c.name_zh || '').toLowerCase() === lowerQuery ||
                (c.name_en || '').toLowerCase() === lowerQuery
            );
            
            if (!matched) {
                matched = this._allCountries.find(c => 
                    c.code.toLowerCase().startsWith(lowerQuery) ||
                    (c.name_zh || '').toLowerCase().startsWith(lowerQuery) ||
                    (c.name_en || '').toLowerCase().startsWith(lowerQuery)
                );
            }
            
            if (matched) {
                const displayName = window.i18n?.currentLang === 'en' ? matched.name_en : matched.name_zh;
                this._addCountry(matched.code, displayName || matched.code);
            } else {
                this._container.classList.add('error');
                setTimeout(() => this._container.classList.remove('error'), 1000);
                this._input.select();
                if (typeof showToast === 'function') {
                    showToast(`未找到匹配的国家: ${query}`, 'warning');
                }
            }
        }
        
        _removeCountry(code) {
            this.selectedCountries = this.selectedCountries.filter(c => c !== code);
            this._updateHiddenField();
            this._renderTags();
            this._updateSelectedCount();
            
            if (typeof this.onChange === 'function') {
                this.onChange(this.selectedCountries);
            }
            
            setTimeout(() => this._input.focus(), 50);
        }
        
        _renderTags() {
            if (!this._tagsWrapper) return;
            
            const oldTags = this._tagsWrapper.querySelectorAll('.country-tag');
            oldTags.forEach(tag => tag.remove());
            
            const input = this._tagsWrapper.querySelector('.country-tag-input');
            const hint = this._tagsWrapper.querySelector('.selected-count-hint');
            
            this.selectedCountries.forEach(code => {
                const country = this._allCountries.find(c => c.code === code);
                const label = country ? (country.name_zh || country.name_en || code) : code;
                
                const tag = document.createElement('span');
                tag.className = 'country-tag';
                tag.innerHTML = `
                    <span class="tag-code">${escapeHtml(code)}</span>
                    <span>${escapeHtml(label)}</span>
                    <span class="tag-remove" data-code="${escapeHtml(code)}" title="移除">
                        <i class="bi bi-x"></i>
                    </span>
                `;
                
                const removeBtn = tag.querySelector('.tag-remove');
                removeBtn.addEventListener('click', (e) => {
                    e.stopPropagation();
                    this._removeCountry(code);
                });
                
                this._tagsWrapper.insertBefore(tag, input);
            });
        }
        
        _updateHiddenField() {
            if (this._hiddenField) {
                this._hiddenField.value = this.selectedCountries.join(',');
            }
        }
        
        _updateSelectedCount() {
            if (this._hint) {
                const count = this.selectedCountries.length;
                this._hint.textContent = `已选 ${count} 个国家`;
                this._hint.style.color = count > 0 ? '#198754' : '#6c757d';
            }
        }
        
        getSelected() {
            return this.selectedCountries;
        }
        
        setSelected(countries) {
            this.selectedCountries = Array.isArray(countries) ? countries : [];
            this._updateHiddenField();
            this._renderTags();
            this._updateSelectedCount();
            if (typeof this.onChange === 'function') {
                this.onChange(this.selectedCountries);
            }
        }
        
        clear() {
            this.selectedCountries = [];
            this._updateHiddenField();
            this._renderTags();
            this._updateSelectedCount();
            this._input.value = '';
            this._hideSuggestions();
            if (typeof this.onChange === 'function') {
                this.onChange(this.selectedCountries);
            }
        }
        
        destroy() {
            this.container.innerHTML = '';
        }
    }

    // ==================== 事件绑定（直接赋值方式） ====================
    
    function bindEvents() {
        // 推送按钮 - 使用直接事件绑定
        document.querySelectorAll('#dashboardTrainingTbody .push-training-btn, #dashboardTrainingTbody .push-btn').forEach(btn => {
            btn.onclick = handlePushClick;
        });
        
        // 拷贝按钮
        document.querySelectorAll('#dashboardTrainingTbody .copy-training-btn').forEach(btn => {
            btn.onclick = handleCopyClick;
        });
        
        // 删除按钮
        document.querySelectorAll('#dashboardTrainingTbody .delete-training-btn, #dashboardTrainingTbody .btn-outline-danger[data-id]').forEach(btn => {
            btn.onclick = handleDeleteClick;
        });
        
        // 编辑按钮
        document.querySelectorAll('#dashboardTrainingTbody .edit-training-btn, #dashboardTrainingTbody .edit-name-btn').forEach(btn => {
            btn.onclick = handleEditClick;
        });
        
        // 初始化 Tooltip
        initTooltips();
    }
    
    // ==================== 初始化 Tooltips ====================
    
    function initTooltips() {
        document.querySelectorAll('#dashboardTrainingTbody [data-bs-toggle="tooltip"]').forEach(el => {
            try {
                const oldTooltip = bootstrap.Tooltip.getInstance(el);
                if (oldTooltip) oldTooltip.dispose();
                new bootstrap.Tooltip(el, {
                    container: 'body',
                    trigger: 'hover focus',
                    placement: 'top'
                });
            } catch (e) {
                // 忽略 Bootstrap 未加载的情况
            }
        });
    }
    
    // ==================== 推送功能 ====================
    
    /**
     * 处理推送按钮点击 - 与培训管理页面保持一致
     */
    async function handlePushClick(e) {
        const btn = e.currentTarget;
        const trainingId = btn.dataset.id;
        
        // 获取培训名称
        const row = btn.closest('tr');
        const nameLink = row?.querySelector('.training-name-link');
        const trainingName = nameLink ? nameLink.textContent.trim() : `培训 #${trainingId}`;
        
        // 使用与 list_trainings.html 相同的推送对话框
        if (typeof showPushDialog === 'function') {
            showPushDialog(trainingId, trainingName);
        } else {
            // 降级方案：使用简单的确认对话框
            if (!confirm(`确定要推送培训「${trainingName}」吗？\n\n推送后，相关用户将收到通知。`)) {
                return;
            }
            
            // 显示加载状态
            const originalText = btn.innerHTML;
            btn.disabled = true;
            btn.innerHTML = `<span class="spinner-border spinner-border-sm me-1"></span> ${safeT('pushing') || '推送中...'}`;
            
            try {
                const response = await fetch(`/api/admin/training/${trainingId}/push`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({})
                });
                
                const result = await response.json();
                
                if (result.success) {
                    if (typeof showToast === 'function') {
                        showToast(result.message || '推送成功', 'success');
                    } else {
                        alert('推送成功');
                    }
                    // 刷新页面
                    setTimeout(() => location.reload(), 1000);
                } else {
                    throw new Error(result.message || '推送失败');
                }
            } catch (error) {
                console.error('推送失败:', error);
                if (typeof showToast === 'function') {
                    showToast('推送失败: ' + error.message, 'error');
                } else {
                    alert('推送失败: ' + error.message);
                }
            } finally {
                btn.disabled = false;
                btn.innerHTML = originalText;
            }
        }
    }
    
    // ==================== 拷贝功能 ====================
    
    async function handleCopyClick(e) {
        const btn = e.currentTarget;
        const trainingId = btn.dataset.id;
        
        if (!confirm('确定要拷贝此培训吗？')) {
            return;
        }
        
        const originalText = btn.innerHTML;
        btn.disabled = true;
        btn.innerHTML = `<span class="spinner-border spinner-border-sm me-1"></span> ${safeT('loading') || '加载中...'}`;
        
        try {
            const res = await fetch(`/api/admin/trainings/${trainingId}`);
            if (!res.ok) throw new Error('获取培训数据失败');
            const data = await res.json();
            const training = data.data || data;
            
            // 解析国家列表
            let countries = [];
            if (training.countries) {
                countries = typeof training.countries === 'string' ? 
                    JSON.parse(training.countries) : training.countries;
            } else if (training.country) {
                countries = [training.country];
            }
            if (!Array.isArray(countries)) countries = [countries];
            countries = countries.filter(c => c && c.trim());
            
            const copyData = {
                name: `${training.name} (拷贝)`,
                countries: countries,
                start_time: training.start_time,
                end_time: training.end_time,
                header_template: training.header_template || {}
            };
            
            const createRes = await fetch('/api/admin/trainings', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(copyData)
            });
            
            if (createRes.ok) {
                const result = await createRes.json();
                if (typeof showToast === 'function') {
                    showToast('培训拷贝成功', 'success');
                }
                setTimeout(() => {
                    location.reload();
                }, 1000);
            } else {
                const error = await createRes.json();
                throw new Error(error.message || '拷贝失败');
            }
        } catch (error) {
            console.error('拷贝培训失败:', error);
            if (typeof showToast === 'function') {
                showToast('拷贝失败: ' + error.message, 'error');
            } else {
                alert('拷贝失败: ' + error.message);
            }
        } finally {
            btn.disabled = false;
            btn.innerHTML = originalText;
        }
    }
    
    // ==================== 删除功能 ====================
    
    async function handleDeleteClick(e) {
        const btn = e.currentTarget;
        const trainingId = btn.dataset.id;
        const row = btn.closest('tr');
        const nameLink = row?.querySelector('.training-name-link');
        const trainingName = nameLink ? nameLink.textContent.trim() : '';
        
        if (!confirm(`确定要删除培训「${trainingName}」吗？此操作不可恢复！`)) {
            return;
        }
        
        const originalText = btn.innerHTML;
        btn.disabled = true;
        btn.innerHTML = `<span class="spinner-border spinner-border-sm me-1"></span> ${safeT('deleting') || '删除中...'}`;
        
        try {
            const res = await fetch(`/api/admin/trainings?id=${trainingId}`, {
                method: 'DELETE'
            });
            const result = await res.json();
            
            if (result.success) {
                if (typeof showToast === 'function') {
                    showToast('培训删除成功', 'success');
                }
                // 从DOM中移除行
                if (row) {
                    row.style.transition = 'all 0.3s ease';
                    row.style.opacity = '0';
                    row.style.transform = 'translateX(-20px)';
                    setTimeout(() => {
                        row.remove();
                        // 更新计数
                        const countBadge = document.getElementById('trainingCount');
                        if (countBadge) {
                            const current = parseInt(countBadge.textContent) || 0;
                            countBadge.textContent = Math.max(0, current - 1);
                        }
                    }, 300);
                }
            } else {
                throw new Error(result.message || '删除失败');
            }
        } catch (error) {
            console.error('删除培训失败:', error);
            if (typeof showToast === 'function') {
                showToast('删除失败: ' + error.message, 'error');
            } else {
                alert('删除失败: ' + error.message);
            }
            btn.disabled = false;
            btn.innerHTML = originalText;
        }
    }
    
    // ==================== 编辑功能（行内编辑） ====================
    
    /**
     * 处理编辑按钮点击 - 行内编辑
     */
    function handleEditClick(e) {
        const btn = e.currentTarget;
        const row = btn.closest('tr');
        if (!row) return;
        
        // 如果已经有编辑中的行，先取消
        if (_currentEditingRow && _currentEditingRow !== row) {
            cancelEdit(_currentEditingRow);
        }
        
        // 进入编辑模式
        startEdit(row);
    }
    
    /**
     * 开始行内编辑 - 从服务器获取最新数据
     */
    async function startEdit(row) {
        const trainingId = row.dataset.trainingId;
        if (!trainingId) {
            console.error('无法获取培训ID');
            return;
        }
        
        const nameCell = row.querySelector('td:first-child');
        const originalContent = nameCell.innerHTML;
        
        // 显示加载状态
        nameCell.innerHTML = `
            <div class="d-flex align-items-center gap-2">
                <span class="spinner-border spinner-border-sm"></span>
                <span>${safeT('loading') || '加载中...'}</span>
            </div>
        `;
        
        try {
            // 从服务器获取最新数据
            const res = await fetch(`/api/admin/trainings/${trainingId}`);
            if (!res.ok) throw new Error('获取培训数据失败');
            const result = await res.json();
            const training = result.data || result;
            
            // 解析国家列表
            let countries = parseTrainingCountries(training);
            
            // 构建编辑界面
            buildEditUI(row, training, countries);
        } catch (err) {
            console.error('加载培训数据失败:', err);
            nameCell.innerHTML = originalContent;
            if (typeof showToast === 'function') {
                showToast('加载培训数据失败: ' + err.message, 'error');
            }
        }
    }
    
    /**
     * 构建行内编辑界面
     */
    function buildEditUI(row, training, countries) {
        const nameCell = row.querySelector('td:first-child');
        const actionsCell = row.querySelector('td:last-child');
        const uniqueId = Date.now() + '_' + Math.random().toString(36).substr(2, 6);
        
        // 获取当前行中的绑定徽章（如果有）
        const bindingBadge = nameCell.querySelector('.binding-badge');
        const bindingHtml = bindingBadge ? bindingBadge.outerHTML : '';
        
        // 构建培训名称列
        nameCell.innerHTML = `
            <div class="training-name-cell editing-cell">
                <div class="d-flex align-items-center gap-2 flex-wrap mb-1">
                    <span class="fw-semibold" style="font-size: 0.8rem;">${safeT('name') || '名称'}:</span>
                    <input type="text" class="form-control form-control-sm edit-name-input" 
                           value="${escapeHtml(training.name)}" style="width: auto; min-width: 200px;">
                    ${bindingHtml}
                </div>
                <div class="training-meta mt-1">
                    <div class="d-flex align-items-center gap-2 flex-wrap mb-1">
                        <span class="fw-semibold" style="font-size: 0.7rem;">🌍 ${safeT('country') || '国家'}:</span>
                        <div id="editCountryTag_${uniqueId}" class="country-tag-input-wrapper" style="min-width: 200px;"></div>
                    </div>
                    <div class="d-flex align-items-center gap-2 flex-wrap">
                        <span class="fw-semibold" style="font-size: 0.7rem;">🕐 ${safeT('valid_period') || '有效期'}:</span>
                        <input type="datetime-local" class="form-control form-control-sm edit-start-input" 
                               value="${training.start_time ? training.start_time.slice(0, 16) : ''}" style="width: 160px;">
                        <span class="text-muted">→</span>
                        <input type="datetime-local" class="form-control form-control-sm edit-end-input" 
                               value="${training.end_time ? training.end_time.slice(0, 16) : ''}" style="width: 160px;">
                    </div>
                </div>
            </div>
        `;
        
        // 初始化国家标签组件
        const wrapper = document.getElementById(`editCountryTag_${uniqueId}`);
        let countryInstance = null;
        if (wrapper) {
            countryInstance = new CountryTagInput({
                container: wrapper,
                selectedCountries: countries,
                placeholder: safeT('placeholder_coutry_search') || '输入国家名称或代码',
                maxTags: 30,
                onChange: (selected) => {
                    console.log('已选国家:', selected);
                }
            });
            // 保存实例引用
            _countryTagInstances.set(uniqueId, countryInstance);
        }
        
        // 构建操作列
        actionsCell.innerHTML = `
            <div class="d-flex gap-1">
                <button class="btn btn-sm btn-success save-edit-btn" data-training-id="${training.id}">
                    <i class="bi bi-check"></i> ${safeT('save') || '保存'}
                </button>
                <button class="btn btn-sm btn-secondary cancel-edit-btn">
                    <i class="bi bi-x"></i> ${safeT('cancel') || '取消'}
                </button>
            </div>
        `;
        
        // 保存按钮事件
        const saveBtn = actionsCell.querySelector('.save-edit-btn');
        saveBtn.onclick = function() {
            saveEdit(row, countryInstance, uniqueId);
        };
        
        // 取消按钮事件
        const cancelBtn = actionsCell.querySelector('.cancel-edit-btn');
        cancelBtn.onclick = function() {
            cancelEdit(row);
        };
        
        // 键盘事件
        const inputs = nameCell.querySelectorAll('input');
        inputs.forEach(input => {
            input.addEventListener('keydown', function(e) {
                if (e.key === 'Enter') {
                    e.preventDefault();
                    saveBtn.click();
                } else if (e.key === 'Escape') {
                    e.preventDefault();
                    cancelBtn.click();
                }
            });
        });
        
        // 标记行进入编辑状态
        row.classList.add('editing-mode');
        _currentEditingRow = row;
        
        // 聚焦到名称输入框
        setTimeout(() => {
            const nameInput = nameCell.querySelector('.edit-name-input');
            if (nameInput) {
                nameInput.focus();
                nameInput.select();
            }
        }, 100);
    }
    
    /**
     * 保存编辑
     */
    async function saveEdit(row, countryInstance, instanceId) {
        const trainingId = row.dataset.trainingId;
        const nameInput = row.querySelector('.edit-name-input');
        const startInput = row.querySelector('.edit-start-input');
        const endInput = row.querySelector('.edit-end-input');
        
        if (!nameInput) return;
        
        const newName = nameInput.value.trim();
        const newStartTime = startInput ? startInput.value : '';
        const newEndTime = endInput ? endInput.value : '';
        
        // 从组件获取选中的国家列表
        let countries = countryInstance ? countryInstance.getSelected() : [];
        
        // 验证
        if (!newName) {
            if (typeof showToast === 'function') {
                showToast('培训名称不能为空', 'warning');
            }
            nameInput.focus();
            return;
        }
        
        if (countries.length === 0) {
            if (typeof showToast === 'function') {
                showToast('请至少选择一个国家', 'warning');
            }
            return;
        }
        
        // 显示保存状态
        const saveBtn = row.querySelector('.save-edit-btn');
        const originalText = saveBtn.innerHTML;
        saveBtn.disabled = true;
        saveBtn.innerHTML = `<span class="spinner-border spinner-border-sm me-1"></span> ${safeT('saving') || '保存中...'}`;
        
        try {
            // 构建更新数据
            const updateData = {
                id: trainingId,
                name: newName,
                countries: countries
            };
            
            if (newStartTime && newEndTime) {
                updateData.start_time = localDateTimeToUTC(newStartTime);
                updateData.end_time = localDateTimeToUTC(newEndTime);
            }
            
            const res = await fetch('/api/admin/trainings', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(updateData)
            });
            
            const result = await res.json();
            
            if (result.success) {
                if (typeof showToast === 'function') {
                    showToast('培训更新成功', 'success');
                }
                // 清理组件实例
                if (instanceId && _countryTagInstances.has(instanceId)) {
                    const instance = _countryTagInstances.get(instanceId);
                    if (instance && typeof instance.destroy === 'function') {
                        instance.destroy();
                    }
                    _countryTagInstances.delete(instanceId);
                }
                // 刷新页面显示最新数据
                setTimeout(() => {
                    location.reload();
                }, 500);
            } else {
                throw new Error(result.message || '更新失败');
            }
        } catch (error) {
            console.error('更新培训失败:', error);
            if (typeof showToast === 'function') {
                showToast('更新失败: ' + error.message, 'error');
            }
            saveBtn.disabled = false;
            saveBtn.innerHTML = originalText;
        }
    }
    
    /**
     * 取消编辑 - 刷新页面
     */
    function cancelEdit(row) {
        if (!row) return;
        
        // 清理所有国家标签组件实例
        _countryTagInstances.forEach((instance, key) => {
            if (instance && typeof instance.destroy === 'function') {
                instance.destroy();
            }
        });
        _countryTagInstances.clear();
        
        // 直接刷新页面以恢复原始数据
        location.reload();
    }
    
    /**
     * 重新绑定事件（用于页面动态更新后）
     */
    function reinit() {
        _currentEditingRow = null;
        _countryTagInstances.clear();
        bindEvents();
        initTooltips();
    }
    
    // ==================== 公共 API ====================
    
    return {
        /**
         * 初始化培训列表操作模块
         */
        init: function() {
            if (_isInitialized) return;
            _isInitialized = true;
            
            // 延迟绑定事件，确保 DOM 完全渲染
            setTimeout(function() {
                bindEvents();
                initTooltips();
                console.log('✅ 培训列表操作模块已初始化（修复版）');
            }, 300);
        },
        
        /**
         * 重新绑定事件
         */
        reinit: reinit,
        
        /**
         * 刷新列表
         */
        refresh: function() {
            location.reload();
        }
    };
})();

// 暴露到全局
window.TrainingListModule = TrainingListModule;