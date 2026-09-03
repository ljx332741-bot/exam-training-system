// static/js/admin/dashboard/_training_list.js
// ============================================================
// 仪表盘培训列表操作模块（数据由服务端渲染，JS只负责操作交互）
// 修复版：采用与 list_trainings.html 一致的直接事件绑定方式
// 完整移植推送功能
// ============================================================

const TrainingListModule = (function() {
    'use strict';
    
    let _isInitialized = false;
    let _currentEditingRow = null;
    let _countryTagInstances = new Map(); // 存储国家标签组件实例
    let _currentPushTrainingId = null;    // 当前推送的培训ID
    let _pushUserListCache = [];          // 推送用户列表缓存
    
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
    
    // 解析国家参数（将中文/英文名转换为国家代码）
    function resolveCountryParam(text) {
        if (!text) return '';
        const upperText = text.toUpperCase();
        if (upperText.length === 2) {
            return upperText;
        }
        return '';
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
    
    // ==================== 推送功能（完整移植自 list_trainings.html） ====================
    
    /**
     * 处理推送按钮点击 - 打开推送模态框
     */
    async function handlePushClick(e) {
        const btn = e.currentTarget;
        const trainingId = btn.dataset.id;
        
        // 获取培训名称
        const row = btn.closest('tr');
        const nameLink = row?.querySelector('.training-name-link');
        const trainingName = nameLink ? nameLink.textContent.trim() : `培训 #${trainingId}`;
        
        // 获取培训状态
        const statusBadge = row?.querySelector('.badge');
        const isDraft = statusBadge && statusBadge.textContent.trim() === '草稿';
        
        // 获取时间
        let startTime = btn.dataset.start;
        let endTime = btn.dataset.end;
        
        // 如果是草稿或无有效时间，使用默认时间
        if (isDraft || !startTime || !endTime || startTime.startsWith('1970-01-01')) {
            const now = new Date();
            const thirtyDaysLater = new Date(now.getTime() + 30 * 24 * 60 * 60 * 1000);
            startTime = formatDateTimeLocal(now);
            endTime = formatDateTimeLocal(thirtyDaysLater);
        } else {
            startTime = startTime ? utcToLocalDatetimeLocal(startTime) : '';
            endTime = endTime ? utcToLocalDatetimeLocal(endTime) : '';
        }
        
        // 设置当前培训ID
        _currentPushTrainingId = trainingId;
        
        // 填充时间
        document.getElementById('start_time').value = startTime;
        document.getElementById('end_time').value = endTime;
        
        // 设置培训名称
        const nameSpan = document.getElementById('pushModalTrainingName');
        if (nameSpan) {
            nameSpan.textContent = trainingName ? `- ${escapeHtml(trainingName)}` : '';
        }
        
        // 清空用户列表并显示加载状态
        const tbody = document.getElementById('pushUserListBody');
        if (tbody) {
            tbody.innerHTML = `<tr><td colspan="5" class="text-center">${safeT('loading') || '加载中...'}</td></tr>`;
        }
        
        // 显示模态框
        const modalElement = document.getElementById('pushModal');
        if (modalElement) {
            const modal = new bootstrap.Modal(modalElement);
            modal.show();
        }
        
        // 加载用户列表
        await loadPushUserList(trainingId);
    }
    
    /**
     * 加载推送用户列表 - 完全移植自 list_trainings.html
     */
    async function loadPushUserList(trainingId) {
        const search = document.getElementById('pushUserSearch')?.value || '';
        const whFilter = document.getElementById('pushWhFilter')?.value.toLowerCase() || '';
        const isPartner = document.getElementById('pushIsPartner')?.value || '';
        const countryFilterInput = document.getElementById('pushCountryFilter');
        const countryFilter = countryFilterInput ? countryFilterInput.value : '';
        
        const tid = trainingId || _currentPushTrainingId;
        if (!tid) {
            console.warn('没有选中培训');
            const tbody = document.getElementById('pushUserListBody');
            if (tbody) {
                tbody.innerHTML = '<tr><td colspan="5" class="text-center text-warning">请先选择培训</td></tr>';
            }
            return;
        }
        
        try {
            // ========== 1. 获取培训详情 ==========
            const trainingRes = await fetch(`/api/admin/trainings/${tid}`);
            if (!trainingRes.ok) {
                throw new Error(`HTTP ${trainingRes.status}`);
            }
            const trainingData = await trainingRes.json();
            const training = trainingData.data || trainingData;
            
            if (!training) {
                const tbody = document.getElementById('pushUserListBody');
                if (tbody) {
                    tbody.innerHTML = '<tr><td colspan="5" class="text-center text-danger">培训不存在</td></tr>';
                }
                return;
            }
            
            // ========== 2. 解析培训国家列表 ==========
            let trainingCountries = parseTrainingCountries(training);
            
            if (trainingCountries.length === 0) {
                const tbody = document.getElementById('pushUserListBody');
                if (tbody) {
                    tbody.innerHTML = `
                        <tr>
                            <td colspan="5" class="text-center text-warning">
                                <i class="bi bi-exclamation-triangle me-1"></i>
                                该培训未指定国家，无法推送
                            </td>
                        </tr>
                    `;
                }
                return;
            }
            
            // ========== 3. 构建请求参数 ==========
            const params = new URLSearchParams();
            if (search) params.append('search', search);
            if (whFilter) params.append('wh', whFilter);
            if (isPartner) params.append('is_partner', isPartner === 'Y' ? 'true' : 'false');
            
            // 传递培训国家列表
            trainingCountries.forEach(c => {
                params.append('countries', c);
            });
            
            // 用户手动筛选的国家
            if (countryFilter) {
                const resolved = resolveCountryParam(countryFilter);
                if (resolved) {
                    params.append('country', resolved);
                }
            }
            
            console.log('📤 请求推送用户列表:', params.toString());
            
            // ========== 4. 调用推送用户列表 API ==========
            const res = await fetch(`/api/admin/users/push_list?${params.toString()}`);
            const data = await res.json();
            let users = data.data || [];
            const actualCountries = data.countries || trainingCountries;
            
            console.log(`📥 返回用户数: ${users.length} (有效国家: ${actualCountries.join(', ')})`);
            
            // ========== 5. 签到状态过滤 ==========
            // 获取该培训的所有签到记录
            const attRes = await fetch(`/api/training/attendance/${tid}`);
            const attData = await attRes.json();
            const attendances = attData.attendances || [];
            
            // 构建签到映射
            const attendanceMap = {};
            attendances.forEach(att => {
                attendanceMap[att.user_id] = {
                    has_signature: !!(att.signature_url && att.signature_url !== '' && att.signature_url !== 'null'),
                    sign_time: att.sign_time,
                    signed_name: att.signed_name
                };
            });
            
            // 过滤用户：只保留未签到 或 待重新签字的用户
            const filteredUsers = users.filter(user => {
                const userId = user.id;
                const attendance = attendanceMap[userId];
                
                if (!attendance) return true;  // 未签到
                if (!attendance.has_signature) return true;  // 待重新签字
                return false;  // 已签到
            });
            
            console.log(`签到状态过滤: 原始 ${users.length} 人，过滤后 ${filteredUsers.length} 人`);
            
            // ========== 6. 渲染用户列表 ==========
            const tbody = document.getElementById('pushUserListBody');
            if (!tbody) return;
            
            if (filteredUsers.length === 0) {
                let message = '';
                if (users.length === 0) {
                    message = `该培训国家(${actualCountries.join(', ')})下暂无用户`;
                } else {
                    message = `该培训国家(${actualCountries.join(', ')})下所有用户已签到`;
                }
                tbody.innerHTML = `
                    <tr>
                        <td colspan="5" class="text-center text-muted">
                            <i class="bi bi-info-circle me-1"></i>
                            ${message}
                        </td>
                    </tr>
                `;
            } else {
                tbody.innerHTML = filteredUsers.map(u => {
                    const attendance = attendanceMap[u.id];
                    let statusBadge = '';
                    if (!attendance) {
                        statusBadge = `<span class="badge bg-warning text-dark ms-1">${safeT('pending_push') || '待推送'}</span>`;
                    } else if (!attendance.has_signature) {
                        statusBadge = `<span class="badge bg-danger ms-1">${safeT('need_re-sign') || '待重新签字'}</span>`;
                    }
                    
                    const whDisplay = u.wh_id ? `${u.wh_id}${u.wh_name_en ? ` (${u.wh_name_en})` : ''}` : '-';
                    
                    return `
                        <tr>
                            <td><input type="checkbox" class="push-user-checkbox" value="${u.id}"></td>
                            <td>
                                <div style="display: flex; flex-direction: column; align-items: flex-start; white-space: nowrap;">
                                    <span>${escapeHtml(u.name_en || u.name_cn || '')}</span>
                                    <span>${statusBadge}</span>
                                </div>
                            </td>
                            <td>${escapeHtml(u.email)}</td>
                            <td class="country-cell">${escapeHtml(u.country || '-')}</td>
                            <td>${escapeHtml(whDisplay)}</td>
                        </tr>
                    `;
                }).join('');
            }
            
            // ========== 7. 绑定全选事件 ==========
            const selectAllCheckbox = document.getElementById('pushSelectAll');
            if (selectAllCheckbox) {
                const newSelectAll = selectAllCheckbox.cloneNode(true);
                selectAllCheckbox.parentNode.replaceChild(newSelectAll, selectAllCheckbox);
                newSelectAll.onclick = (e) => {
                    document.querySelectorAll('.push-user-checkbox').forEach(cb => cb.checked = e.target.checked);
                };
            }
            
        } catch (err) {
            console.error('加载用户列表失败', err);
            const tbody = document.getElementById('pushUserListBody');
            if (tbody) {
                tbody.innerHTML = '<tr><td colspan="5" class="text-center text-danger">加载失败: ' + escapeHtml(err.message) + '</td></tr>';
            }
        }
    }
    
    /**
     * 格式化本地时间（用于 datetime-local 输入框）
     */
    function formatDateTimeLocal(date) {
        const year = date.getFullYear();
        const month = String(date.getMonth() + 1).padStart(2, '0');
        const day = String(date.getDate()).padStart(2, '0');
        const hours = String(date.getHours()).padStart(2, '0');
        const minutes = String(date.getMinutes()).padStart(2, '0');
        return `${year}-${month}-${day}T${hours}:${minutes}`;
    }
    
    /**
     * UTC 时间转本地 datetime-local 格式
     */
    function utcToLocalDatetimeLocal(utcStr) {
        if (!utcStr) return '';
        try {
            const date = new Date(utcStr);
            if (isNaN(date.getTime())) return '';
            return formatDateTimeLocal(date);
        } catch {
            return '';
        }
    }
    
    // ==================== 绑定推送模态框事件 ====================
    
    /**
     * 绑定推送模态框的确认按钮事件
     */
    function bindPushModalEvents() {
        const confirmBtn = document.getElementById('confirmPushBtn');
        if (confirmBtn) {
            // 移除旧事件避免重复绑定
            const newBtn = confirmBtn.cloneNode(true);
            confirmBtn.parentNode.replaceChild(newBtn, confirmBtn);
            
            newBtn.addEventListener('click', confirmPushHandler);
        }
        
        // 刷新用户列表按钮
        const loadUsersBtn = document.getElementById('pushLoadUsersBtn');
        if (loadUsersBtn) {
            const newBtn = loadUsersBtn.cloneNode(true);
            loadUsersBtn.parentNode.replaceChild(newBtn, loadUsersBtn);
            
            newBtn.addEventListener('click', async function() {
                const btn = this;
                const originalHTML = btn.innerHTML;
                btn.disabled = true;
                btn.innerHTML = `<span class="spinner-border spinner-border-sm me-1"></span> ${safeT('loading') || '加载中...'}`;
                
                try {
                    await loadPushUserList(_currentPushTrainingId);
                    if (typeof showToast === 'function') {
                        showToast(safeT('t_list_refreshed') || '列表已刷新', 'success');
                    }
                } catch (err) {
                    console.error('加载失败:', err);
                    if (typeof showToast === 'function') {
                        showToast(safeT('load_failed') || '加载失败', 'error');
                    }
                } finally {
                    btn.disabled = false;
                    btn.innerHTML = originalHTML;
                }
            });
        }
        
        // 库房模糊搜索
        const whFilterInput = document.getElementById('pushWhFilter');
        if (whFilterInput) {
            whFilterInput.addEventListener('input', async function() {
                const q = this.value.trim();
                const datalist = document.getElementById('whDatalist');
                if (q.length === 0) {
                    datalist.innerHTML = '';
                    return;
                }
                try {
                    const res = await fetch(`/api/search/warehouses?q=${encodeURIComponent(q)}`);
                    const items = await res.json();
                    datalist.innerHTML = items.map(name => `<option value="${escapeHtml(name)}">`).join('');
                } catch (e) {
                    console.error('搜索库房失败', e);
                }
            });
        }
        
        // 国家自动补全初始化
        setupCountryAutocompleteForPush();
    }
    
    /**
     * 推送确认处理函数
     */
    async function confirmPushHandler() {
        const startLocal = document.getElementById('start_time').value;
        const endLocal = document.getElementById('end_time').value;
        
        if (!startLocal || !endLocal) {
            alert(safeT('t_fill_start_end_time') || '请填写开始和结束时间');
            return;
        }
        
        // 转换为 UTC
        const startISO = localDateTimeToUTC(startLocal);
        const endISO = localDateTimeToUTC(endLocal);
        
        // 收集选中的用户ID
        const selectedUserIds = Array.from(document.querySelectorAll('.push-user-checkbox:checked')).map(cb => cb.value);
        
        const btn = document.getElementById('confirmPushBtn');
        const originalText = btn.innerHTML;
        btn.disabled = true;
        btn.innerHTML = `<span class="spinner-border spinner-border-sm me-1"></span> ${safeT('pushing') || '推送中...'}`;
        
        try {
            const res = await fetch('/api/admin/trainings', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    id: _currentPushTrainingId,
                    start_time: startISO,
                    end_time: endISO,
                    is_active: true,
                    user_ids: selectedUserIds.length > 0 ? selectedUserIds : null
                })
            });
            
            if (res.ok) {
                const modal = bootstrap.Modal.getInstance(document.getElementById('pushModal'));
                if (modal) modal.hide();
                
                if (typeof showToast === 'function') {
                    const count = selectedUserIds.length;
                    const msg = count > 0 
                        ? `已推送给 ${count} 名选中学员`
                        : '已推送给所有国家用户';
                    showToast(msg, 'success');
                }
                
                // 刷新页面
                setTimeout(() => {
                    location.reload();
                }, 1000);
            } else {
                const err = await res.json();
                alert(safeT('t_push_failed') + (err.message || safeT('t_network_err')));
            }
        } catch (err) {
            console.error('推送失败:', err);
            alert(safeT('t_push_failed') + err.message);
        } finally {
            btn.disabled = false;
            btn.innerHTML = originalText;
        }
    }
    
    /**
     * 为推送模态框设置国家自动补全
     */
    function setupCountryAutocompleteForPush() {
        const input = document.getElementById('pushCountryFilter');
        if (!input) return;
        
        // 确保有隐藏字段
        if (!document.getElementById('pushCountryCode')) {
            const hidden = document.createElement('input');
            hidden.type = 'hidden';
            hidden.id = 'pushCountryCode';
            input.after(hidden);
        }
        
        // 如果已经初始化，跳过
        if (window._pushCountryAutocompleteInited) return;
        window._pushCountryAutocompleteInited = true;
        
        // 使用与 list_trainings.html 相同的逻辑
        // 简单实现：输入时尝试匹配国家代码
        input.addEventListener('blur', function() {
            const val = this.value.trim();
            if (!val) {
                document.getElementById('pushCountryCode').value = '';
                return;
            }
            const code = resolveCountryParam(val);
            if (code) {
                document.getElementById('pushCountryCode').value = code;
            }
        });
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
                if (row) {
                    row.style.transition = 'all 0.3s ease';
                    row.style.opacity = '0';
                    row.style.transform = 'translateX(-20px)';
                    setTimeout(() => {
                        row.remove();
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
    
    function handleEditClick(e) {
        const btn = e.currentTarget;
        const row = btn.closest('tr');
        if (!row) return;
        
        if (_currentEditingRow && _currentEditingRow !== row) {
            cancelEdit(_currentEditingRow);
        }
        
        startEdit(row);
    }
    
    async function startEdit(row) {
        const trainingId = row.dataset.trainingId;
        if (!trainingId) {
            console.error('无法获取培训ID');
            return;
        }
        
        const nameCell = row.querySelector('td:first-child');
        const originalContent = nameCell.innerHTML;
        
        nameCell.innerHTML = `
            <div class="d-flex align-items-center gap-2">
                <span class="spinner-border spinner-border-sm"></span>
                <span>${safeT('loading') || '加载中...'}</span>
            </div>
        `;
        
        try {
            const res = await fetch(`/api/admin/trainings/${trainingId}`);
            if (!res.ok) throw new Error('获取培训数据失败');
            const result = await res.json();
            const training = result.data || result;
            
            let countries = parseTrainingCountries(training);
            buildEditUI(row, training, countries);
        } catch (err) {
            console.error('加载培训数据失败:', err);
            nameCell.innerHTML = originalContent;
            if (typeof showToast === 'function') {
                showToast('加载培训数据失败: ' + err.message, 'error');
            }
        }
    }
    
    function buildEditUI(row, training, countries) {
        const nameCell = row.querySelector('td:first-child');
        const actionsCell = row.querySelector('td:last-child');
        const uniqueId = Date.now() + '_' + Math.random().toString(36).substr(2, 6);
        
        const bindingBadge = nameCell.querySelector('.binding-badge');
        const bindingHtml = bindingBadge ? bindingBadge.outerHTML : '';
        
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
            _countryTagInstances.set(uniqueId, countryInstance);
        }
        
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
        
        const saveBtn = actionsCell.querySelector('.save-edit-btn');
        saveBtn.onclick = function() {
            saveEdit(row, countryInstance, uniqueId);
        };
        
        const cancelBtn = actionsCell.querySelector('.cancel-edit-btn');
        cancelBtn.onclick = function() {
            cancelEdit(row);
        };
        
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
        
        row.classList.add('editing-mode');
        _currentEditingRow = row;
        
        setTimeout(() => {
            const nameInput = nameCell.querySelector('.edit-name-input');
            if (nameInput) {
                nameInput.focus();
                nameInput.select();
            }
        }, 100);
    }
    
    async function saveEdit(row, countryInstance, instanceId) {
        const trainingId = row.dataset.trainingId;
        const nameInput = row.querySelector('.edit-name-input');
        const startInput = row.querySelector('.edit-start-input');
        const endInput = row.querySelector('.edit-end-input');
        
        if (!nameInput) return;
        
        const newName = nameInput.value.trim();
        const newStartTime = startInput ? startInput.value : '';
        const newEndTime = endInput ? endInput.value : '';
        
        let countries = countryInstance ? countryInstance.getSelected() : [];
        
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
        
        const saveBtn = row.querySelector('.save-edit-btn');
        const originalText = saveBtn.innerHTML;
        saveBtn.disabled = true;
        saveBtn.innerHTML = `<span class="spinner-border spinner-border-sm me-1"></span> ${safeT('saving') || '保存中...'}`;
        
        try {
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
                if (instanceId && _countryTagInstances.has(instanceId)) {
                    const instance = _countryTagInstances.get(instanceId);
                    if (instance && typeof instance.destroy === 'function') {
                        instance.destroy();
                    }
                    _countryTagInstances.delete(instanceId);
                }
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
    
    function cancelEdit(row) {
        if (!row) return;
        
        _countryTagInstances.forEach((instance, key) => {
            if (instance && typeof instance.destroy === 'function') {
                instance.destroy();
            }
        });
        _countryTagInstances.clear();
        
        location.reload();
    }
    
    // ==================== 初始化推送模态框事件 ====================
    
    function initPushModal() {
        // 监听模态框显示事件，初始化国家自动补全
        const modalElement = document.getElementById('pushModal');
        if (modalElement) {
            modalElement.addEventListener('shown.bs.modal', function() {
                setupCountryAutocompleteForPush();
                // 如果有当前培训ID，加载用户列表
                if (_currentPushTrainingId) {
                    loadPushUserList(_currentPushTrainingId);
                }
            });
        }
        
        // 绑定确认按钮事件
        bindPushModalEvents();
    }
    
    // ==================== 重新绑定事件 ====================
    
    function reinit() {
        _currentEditingRow = null;
        _countryTagInstances.clear();
        bindEvents();
        initTooltips();
        initPushModal();
    }
    
    // ==================== 公共 API ====================
    
    return {
        init: function() {
            if (_isInitialized) return;
            _isInitialized = true;
            
            setTimeout(function() {
                bindEvents();
                initTooltips();
                initPushModal();
                console.log('✅ 培训列表操作模块已初始化（含完整推送功能）');
            }, 300);
        },
        
        reinit: reinit,
        
        refresh: function() {
            location.reload();
        }
    };
})();

// 暴露到全局
window.TrainingListModule = TrainingListModule;