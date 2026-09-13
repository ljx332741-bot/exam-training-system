// static/js/admin/dashboard/_training_exam_list.js
// ============================================================
// 仪表盘培训列表操作模块（数据由服务端渲染，JS只负责操作交互）
// 完整版：包含培训名称点击、表头录入、国家分组等功能
// ============================================================

const TrainingListModule = (function() {
    'use strict';
    
    let _isInitialized = false;
    let _currentEditingRow = null;
    let _countryTagInstances = new Map();
    let _currentPushTrainingId = null;
    let _pushUserListCache = [];
    let _headerModalLoadingSet = new Set();
    
    // ============================================================
    // 工具函数
    // ============================================================
    
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
    
    function resolveCountryParam(text) {
        if (!text) return '';
        const upperText = text.toUpperCase();
        if (upperText.length === 2) {
            return upperText;
        }
        return '';
    }
    
    function formatDateTimeLocal(date) {
        const year = date.getFullYear();
        const month = String(date.getMonth() + 1).padStart(2, '0');
        const day = String(date.getDate()).padStart(2, '0');
        const hours = String(date.getHours()).padStart(2, '0');
        const minutes = String(date.getMinutes()).padStart(2, '0');
        return `${year}-${month}-${day}T${hours}:${minutes}`;
    }
    
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
    
    // ============================================================
    // 多国家标签输入组件
    // ============================================================
    
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
            // 考试名称点击 -> 跳转考生考试详情页
            document.querySelectorAll('.exam-name-link').forEach(link => {
                link.removeEventListener('click', handleExamNameClick);
                link.addEventListener('click', handleExamNameClick);
            });

            // 考试ID点击 -> 跳转考生考试状态管理页
            document.querySelectorAll('.exam-id-link').forEach(link => {
                link.removeEventListener('click', handleExamIdClick);
                link.addEventListener('click', handleExamIdClick);
            });
            function handleExamNameClick(e) {
                // 如果链接已经有 href 属性，不拦截（让默认行为生效）
                const link = e.currentTarget;
                if (link.getAttribute('href') && link.getAttribute('href') !== '#') {
                    return;
                }
                e.preventDefault();
                const row = link.closest('tr');
                const examId = row?.dataset?.examId || link.dataset?.examId;
                if (examId) {
                    window.open(`/admin/exam/${examId}/scores`, '_blank');
                }
            }
            function handleExamIdClick(e) {
                e.preventDefault();
                const link = e.currentTarget;
                const examId = link.textContent.trim();
                if (examId) {
                    window.open(`/admin/exam/${examId}/candidate_status`, '_blank');
                }
            }
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
                this._hint.textContent = `${t('chose_count_country', count)}`;
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
    
    // ============================================================
    // 培训名称点击处理
    // ============================================================
    
    async function handleTrainingNameClick(e) {
        const link = e.currentTarget;
        const trainingId = link.dataset.id;
        
        if (!trainingId) {
            console.warn('培训ID缺失');
            return;
        }
        
        // 防止重复点击
        if (link.dataset.loading === 'true') {
            return;
        }
        
        link.dataset.loading = 'true';
        link.style.opacity = '0.6';
        link.style.cursor = 'wait';
        
        try {
            // 获取培训详情
            const res = await fetch(`/api/admin/trainings/${trainingId}`);
            if (!res.ok) throw new Error('获取培训信息失败');
            const result = await res.json();
            const training = result.data || result;
            
            // 解析国家列表
            const countries = parseTrainingCountries(training);
            
            if (countries.length === 1) {
                // 单国家：直接跳转签到详情
                const url = `/admin/training/${trainingId}/attendance?from=dashboard`;
                window.open(url, '_blank');
            } else if (countries.length > 1) {
                // 多国家：弹出国家分组模态框
                await showCountryGroupModal(trainingId, training.name, countries);
            } else {
                // 没有国家：提示
                if (typeof showToast === 'function') {
                    showToast('该培训未指定国家，无法查看签到详情', 'warning');
                }
            }
        } catch (err) {
            console.error('获取培训信息失败:', err);
            if (typeof showToast === 'function') {
                showToast('加载培训信息失败: ' + err.message, 'error');
            }
        } finally {
            link.dataset.loading = 'false';
            link.style.opacity = '';
            link.style.cursor = '';
        }
    }
    
    // ============================================================
    // 国家分组模态框（移植自 admin_trainings.html）
    // ============================================================
    async function showCountryGroupModal(trainingId, trainingName, countries) {
        // 设置模态框标题
        document.getElementById('groupModalTrainingName').textContent = trainingName || `培训 #${trainingId}`;
        
        const tbody = document.getElementById('groupTableBody');
        tbody.innerHTML = '<tr><td colspan="3" class="text-center"><span class="spinner-border spinner-border-sm me-2"></span>加载中...</td></tr>';
        
        try {
            // 获取按国家分组的签到记录
            const groupsRes = await fetch(`/api/admin/training/${trainingId}/attendance_by_country`);
            const groups = await groupsRes.json();
            
            if (!groups || groups.length === 0) {
                // 如果培训没有国家列表，显示提示
                tbody.innerHTML = `
                    <tr>
                        <td colspan="3" class="text-center text-warning">
                            <i class="bi bi-exclamation-triangle me-1"></i>
                            该培训未指定国家，请先在培训管理中设置国家
                        </td>
                    </tr>
                `;
            } else {
                const currentLang = window.i18n?.currentLang || 'zh';
                
                const groupRows = await Promise.all(groups.map(async (g) => {
                    // 获取该国家的表头模板状态
                    let template = {};
                    let status = 'empty';
                    let tooltipText = '';
                    let statusIcon = '📋';
                    let statusClass = 'header-status-empty';
                    
                    try {
                        const templateRes = await fetch(`/api/admin/training/${trainingId}/country_template?country=${g.country}`);
                        if (templateRes.ok) {
                            const templateData = await templateRes.json();
                            template = templateData.template || {};
                            const headerStatus = checkHeaderTemplateStatus(template);
                            status = headerStatus.status;
                            tooltipText = getHeaderTooltipText(template, currentLang);
                            statusIcon = getHeaderStatusIcon(template);
                            statusClass = getHeaderStatusClass(template);
                        }
                    } catch (err) {
                        console.warn(`获取国家 ${g.country} 模板失败:`, err);
                    }
                    
                    // 根据状态设置按钮颜色
                    let btnColorClass = 'btn-outline-info';
                    let statusBadge = '';
                    if (status === 'full') {
                        btnColorClass = 'btn-outline-success';
                        statusBadge = `<span class="badge bg-success ms-1" style="font-size: 0.55rem;">✓</span>`;
                    } else if (status === 'partial') {
                        btnColorClass = 'btn-outline-warning';
                        statusBadge = `<span class="badge bg-warning text-dark ms-1" style="font-size: 0.55rem;">!</span>`;
                    } else if (status === 'empty') {
                        btnColorClass = 'btn-outline-secondary';
                        statusBadge = `<span class="badge bg-secondary ms-1" style="font-size: 0.55rem;">?</span>`;
                    }
                    
                    // ========== 关键修改：根据是否有签到记录决定按钮状态 ==========
                    const hasAttendance = g.has_attendance === true;
                    const attendanceCount = g.count || 0;
                    
                    // 签到详情按钮：有签到记录才可点击
                    let viewBtnHtml;
                    if (hasAttendance) {
                        viewBtnHtml = `
                            <button class="btn btn-sm btn-primary view-country-attendance" 
                                data-training-id="${trainingId}" 
                                data-country="${g.country}">
                                ${safeT('title_view_attendance_details') || '查看签到'} (${attendanceCount})
                            </button>
                        `;
                    } else {
                        viewBtnHtml = `
                            <button class="btn btn-sm btn-secondary" 
                                style="opacity:0.6; cursor: not-allowed;"
                                data-bs-toggle="tooltip"
                                data-bs-placement="top"
                                title="${safeT('no_sign_in_record_available') || '暂无签到记录'}">
                                ${safeT('title_view_attendance_details') || '查看签到'} (0)
                            </button>
                        `;
                    }
                    
                    return `
                        <tr>
                            <td><strong>${g.country}</strong></td>
                            <td>
                                <span class="badge ${hasAttendance ? 'bg-success' : 'bg-secondary'}">
                                    ${attendanceCount}
                                </span>
                                ${!hasAttendance ? '<span class="text-muted small ms-1">(无签到)</span>' : ''}
                            </td>
                            <td>
                                <div class="d-flex gap-1 flex-wrap">
                                    ${viewBtnHtml}
                                    <button class="btn btn-sm ${btnColorClass} group-header-btn header-status-${status}" 
                                        data-training-id="${trainingId}" 
                                        data-country="${g.country}"
                                        data-bs-toggle="tooltip"
                                        data-bs-placement="top"
                                        data-bs-title="${tooltipText || (status === 'full' ? '✅ 表头已录入' : '📋 点击录入表头')}"
                                        data-header-status="${status}">
                                        ${statusIcon} ${safeT('header_input') || '表头'}
                                        ${statusBadge}
                                    </button>
                                </div>
                            </td>
                        </tr>
                    `;
                }));
                
                tbody.innerHTML = groupRows.join('');
            }
            
            // 绑定查看签到按钮（只有有签到记录的才绑定事件）
            document.querySelectorAll('.view-country-attendance').forEach(btn => {
                btn.onclick = function() {
                    const tid = this.dataset.trainingId;
                    const country = this.dataset.country;
                    window.open(`/admin/training/${tid}/attendance?country=${encodeURIComponent(country)}&from=dashboard`, '_blank');
                };
            });
            
            // 绑定表头录入按钮
            document.querySelectorAll('.group-header-btn').forEach(btn => {
                btn.onclick = function() {
                    const trainingId = this.dataset.trainingId;
                    const country = this.dataset.country;
                    const modal = bootstrap.Modal.getInstance(document.getElementById('countryGroupModal'));
                    if (modal) modal.hide();
                    setTimeout(() => {
                        openHeaderModalForTraining(trainingId, country);
                    }, 300);
                };
            });
            
            // 初始化 Tooltip
            setTimeout(() => {
                document.querySelectorAll('.group-header-btn[data-bs-toggle="tooltip"], .btn-secondary[data-bs-toggle="tooltip"]').forEach(el => {
                    try {
                        const oldTooltip = bootstrap.Tooltip.getInstance(el);
                        if (oldTooltip) oldTooltip.dispose();
                        new bootstrap.Tooltip(el, {
                            container: 'body',
                            trigger: 'hover focus',
                            placement: 'top'
                        });
                    } catch (e) {}
                });
            }, 100);
            
        } catch (err) {
            console.error('加载国家分组数据失败:', err);
            tbody.innerHTML = `<tr><td colspan="3" class="text-center text-danger">加载失败: ${err.message}</td></tr>`;
        }
        
        // 显示模态框
        const modalEl = document.getElementById('countryGroupModal');
        let modal = bootstrap.Modal.getInstance(modalEl);
        if (modal) modal.dispose();
        modal = new bootstrap.Modal(modalEl);
        modal.show();
    }
    
    // ============================================================
    // 表头模板状态检查工具（移植自 admin_trainings.html）
    // ============================================================
    
    const HEADER_FIELD_MAP = {
        'course_name': { cn: '名称', en: 'Name' },
        'project_no': { cn: '编号', en: 'PrjNo' },
        'venue': { cn: '地点', en: 'Place' },
        'language': { cn: '语言', en: 'Lang' },
        'target': { cn: '对象', en: 'Target' },
        'dept': { cn: '部门', en: 'Dept' },
        'organizer': { cn: '组织', en: 'Org' },
        'training_date': { cn: '日期', en: 'Date' },
        'lecturer': { cn: '主讲', en: 'Orator' },
        'translator': { cn: '翻译', en: 'Trans' }
    };
    const HEADER_FIELDS = Object.keys(HEADER_FIELD_MAP);
    
    function checkHeaderTemplateStatus(template) {
        if (!template || typeof template !== 'object') {
            return { status: 'empty', emptyFields: [], filledFields: [] };
        }
        const emptyFields = [];
        const filledFields = [];
        HEADER_FIELDS.forEach(field => {
            const value = template[field] || '';
            if (value.trim() === '') {
                emptyFields.push(field);
            } else {
                filledFields.push(field);
            }
        });
        let status = 'full';
        if (filledFields.length === 0) {
            status = 'empty';
        } else if (filledFields.length < HEADER_FIELDS.length) {
            status = 'partial';
        }
        return { status, emptyFields, filledFields };
    }
    
    function getHeaderTooltipText(template, lang = 'zh') {
        const { status, emptyFields } = checkHeaderTemplateStatus(template);
        switch (status) {
            case 'empty':
                return lang === 'zh' ? '📋 培训表头为空，点击录入' : '📋 Training header is empty, click to add';
            case 'full':
                return lang === 'zh' ? '✅ 培训表头已全部录入，点击编辑' : '✅ Training header is complete, click to edit';
            case 'partial':
                const emptyNames = emptyFields.map(f => {
                    const fieldInfo = HEADER_FIELD_MAP[f];
                    return lang === 'zh' ? fieldInfo.cn : fieldInfo.en;
                });
                return lang === 'zh' 
                    ? `⚠️ 培训表头录入不全，点击编辑补全（${emptyNames.join('、')}为空）`
                    : `⚠️ Training header incomplete, click to edit (${emptyNames.join(', ')} empty)`;
            default:
                return lang === 'zh' ? '📋 表头录入' : 'Header Input';
        }
    }
    
    function getHeaderStatusClass(template) {
        const { status } = checkHeaderTemplateStatus(template);
        switch (status) {
            case 'empty': return 'header-status-empty';
            case 'full': return 'header-status-full';
            case 'partial': return 'header-status-partial';
            default: return '';
        }
    }
    
    function getHeaderStatusIcon(template) {
        const { status } = checkHeaderTemplateStatus(template);
        switch (status) {
            case 'empty': return '📋';
            case 'full': return '✅';
            case 'partial': return '⚠️';
            default: return '📋';
        }
    }
    
    // ============================================================
    // 表头录入功能（移植自 admin_trainings.html）
    // ============================================================
    
    /**
     * 打开表头录入模态框
     */
    async function openHeaderModalForTraining(trainingId, countryCode = null) {
        const lockKey = countryCode ? `${trainingId}_${countryCode}` : `${trainingId}`;
        if (_headerModalLoadingSet.has(lockKey)) {
            if (typeof showToast === 'function') {
                showToast('数据加载中，请稍候...', 'warning');
            }
            return;
        }
        _headerModalLoadingSet.add(lockKey);
        
        try {
            // 获取培训名称
            const trainingRes = await fetch(`/api/admin/trainings/${trainingId}`);
            let trainingName = `培训 #${trainingId}`;
            if (trainingRes.ok) {
                const result = await trainingRes.json();
                const training = result.data || result;
                trainingName = training.name || trainingName;
            }
            
            // 设置模态框标题
            const nameSpan = document.getElementById('headerModalTrainingName');
            if (nameSpan) {
                nameSpan.textContent = countryCode ? `- ${trainingName} (${countryCode})` : `- ${trainingName}`;
            }
            const countrySpan = document.getElementById('headerModalCountry');
            if (countrySpan) {
                countrySpan.textContent = countryCode ? `[${countryCode}]` : '';
            }
            
            // 设置隐藏字段
            document.getElementById('currentEditTrainingId').value = trainingId;
            document.getElementById('currentEditCountry').value = countryCode || '';
            
            // 加载表头数据
            await loadHeaderDataForTraining(trainingId, countryCode);
            
            // 显示模态框
            const modalEl = document.getElementById('headerModal');
            let modal = bootstrap.Modal.getInstance(modalEl);
            if (modal) modal.dispose();
            modal = new bootstrap.Modal(modalEl);
            modal.show();
            
        } catch (err) {
            console.error('打开表头模态框失败:', err);
            if (typeof showToast === 'function') {
                showToast('加载失败: ' + err.message, 'error');
            }
        } finally {
            _headerModalLoadingSet.delete(lockKey);
        }
    }
    
    /**
     * 加载表头数据
     */
    async function loadHeaderDataForTraining(trainingId, countryCode = null) {
        let template = {};
        try {
            if (countryCode) {
                const url = `/api/admin/training/${trainingId}/country_template?country=${countryCode}&_t=${Date.now()}`;
                const res = await fetch(url);
                if (res.ok) {
                    const data = await res.json();
                    template = data.template || {};
                }
            } else {
                const res = await fetch(`/api/admin/trainings?id=${trainingId}&_t=${Date.now()}`);
                if (res.ok) {
                    const data = await res.json();
                    const trainingsList = Array.isArray(data) ? data : (data.data || []);
                    const training = trainingsList.find(t => t.id == trainingId);
                    if (training) {
                        template = training.header_template || {};
                    }
                }
            }
            
            // 填充表单
            const fields = ['course_name', 'project_no', 'venue', 'language', 'target', 'dept', 'organizer', 'training_date', 'lecturer', 'translator'];
            fields.forEach(field => {
                const el = document.getElementById(field);
                if (el) {
                    el.value = template[field] || '';
                }
            });
            
        } catch (err) {
            console.error('加载表头失败:', err);
            throw err;
        }
    }
    
    /**
     * 保存表头模板
     */
    async function saveHeaderTemplate() {
        const trainingId = document.getElementById('currentEditTrainingId').value;
        const country = document.getElementById('currentEditCountry').value;
        
        if (!trainingId) {
            alert('培训ID无效，请刷新页面重试');
            return;
        }
        
        const template = {
            course_name: document.getElementById('course_name').value,
            project_no: document.getElementById('project_no').value,
            venue: document.getElementById('venue').value,
            language: document.getElementById('language').value,
            target: document.getElementById('target').value,
            dept: document.getElementById('dept').value,
            organizer: document.getElementById('organizer').value,
            training_date: document.getElementById('training_date').value,
            lecturer: document.getElementById('lecturer').value,
            translator: document.getElementById('translator').value
        };
        
        const saveBtn = document.getElementById('saveHeaderBtn');
        const originalText = saveBtn.innerHTML;
        saveBtn.disabled = true;
        saveBtn.innerHTML = `<span class="spinner-border spinner-border-sm me-1"></span> ${safeT('saving') || '保存中...'}`;
        
        try {
            const payload = { template: template };
            if (country && country !== 'null' && country !== 'undefined') {
                payload.country = country;
            }
            
            const res = await fetch(`/api/admin/training/${trainingId}/country_template`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            const result = await res.json();
            
            if (result.success) {
                // 关闭模态框
                const modalEl = document.getElementById('headerModal');
                const modal = bootstrap.Modal.getInstance(modalEl);
                if (modal) modal.hide();
                
                // 更新按钮状态
                updateHeaderButtonStatus(trainingId, template, country);
                
                if (typeof showToast === 'function') {
                    showToast('表头保存成功', 'success');
                }
            } else {
                throw new Error(result.message || '保存失败');
            }
        } catch (err) {
            console.error('保存表头失败:', err);
            if (typeof showToast === 'function') {
                showToast('保存失败: ' + err.message, 'error');
            }
        } finally {
            saveBtn.disabled = false;
            saveBtn.innerHTML = originalText;
        }
    }
    
    /**
     * 更新表头按钮状态
     */
    function updateHeaderButtonStatus(trainingId, template, countryCode = null) {
        const lang = window.i18n?.currentLang || 'zh';
        const tooltipText = getHeaderTooltipText(template, lang);
        const statusIcon = getHeaderStatusIcon(template);
        const statusClass = getHeaderStatusClass(template);
        
        let selector;
        if (countryCode) {
            selector = `.group-header-btn[data-training-id="${trainingId}"][data-country="${countryCode}"]`;
        } else {
            selector = `.header-btn[data-id="${trainingId}"]`;
        }
        
        const btn = document.querySelector(selector);
        if (btn) {
            // 更新属性
            btn.setAttribute('data-bs-title', tooltipText);
            btn.setAttribute('data-header-status', statusClass);
            
            // 更新按钮颜色
            let btnColorClass = 'btn-outline-info';
            let statusBadgeHtml = '';
            if (statusClass === 'header-status-full') {
                btnColorClass = 'btn-outline-success';
                statusBadgeHtml = `<span class="badge bg-success ms-1" style="font-size: 0.55rem;">✓</span>`;
            } else if (statusClass === 'header-status-partial') {
                btnColorClass = 'btn-outline-warning';
                statusBadgeHtml = `<span class="badge bg-warning text-dark ms-1" style="font-size: 0.55rem;">!</span>`;
            } else if (statusClass === 'header-status-empty') {
                btnColorClass = 'btn-outline-secondary';
                statusBadgeHtml = `<span class="badge bg-secondary ms-1" style="font-size: 0.55rem;">?</span>`;
            }
            
            // 更新类
            btn.className = btn.className.replace(/btn-outline-\w+/g, btnColorClass);
            btn.className = btn.className.replace(/header-status-\w+/g, '');
            btn.classList.add(`header-status-${statusClass}`);
            
            // 更新图标
            const iconMatch = btn.innerHTML.match(/^[📋✅⚠️]/);
            if (iconMatch) {
                btn.innerHTML = btn.innerHTML.replace(/^[📋✅⚠️]/, statusIcon);
            }
            
            // 更新徽章
            const existingBadge = btn.querySelector('.badge');
            if (existingBadge) {
                existingBadge.outerHTML = statusBadgeHtml;
            } else if (statusBadgeHtml) {
                btn.innerHTML += statusBadgeHtml;
            }
            
            // 重新创建 Tooltip
            try {
                const oldTooltip = bootstrap.Tooltip.getInstance(btn);
                if (oldTooltip) oldTooltip.dispose();
                new bootstrap.Tooltip(btn, {
                    container: 'body',
                    trigger: 'hover focus',
                    placement: 'top'
                });
            } catch (e) {}
        }
    }
    
    // ============================================================
    // 表头录入按钮点击处理
    // ============================================================
    
    async function handleHeaderBtnClick(e) {
        const btn = e.currentTarget;
        const trainingId = btn.dataset.id;
        const isMultiCountry = btn.dataset.multiCountry === 'true';
        const countries = btn.dataset.countries ? JSON.parse(btn.dataset.countries) : [];
        
        // 防止重复点击
        if (btn.dataset.loading === 'true') {
            return;
        }
        
        btn.dataset.loading = 'true';
        btn.disabled = true;
        btn.style.opacity = '0.7';
        const originalText = btn.innerHTML;
        btn.innerHTML = `<span class="spinner-border spinner-border-sm me-1"></span> ${safeT('loading') || '加载中...'}`;
        
        try {
            if (isMultiCountry && countries.length > 1) {
                // 多国家：弹出国家分组模态框
                // 先获取培训名称
                const trainingRes = await fetch(`/api/admin/trainings/${trainingId}`);
                let trainingName = `培训 #${trainingId}`;
                if (trainingRes.ok) {
                    const result = await trainingRes.json();
                    const training = result.data || result;
                    trainingName = training.name || trainingName;
                }
                await showCountryGroupModal(trainingId, trainingName, countries);
            } else {
                // 单国家：直接打开表头录入模态框
                const country = btn.dataset.country || '';
                await openHeaderModalForTraining(trainingId, country);
            }
        } catch (err) {
            console.error('打开表头录入失败:', err);
            if (typeof showToast === 'function') {
                showToast('加载失败: ' + err.message, 'error');
            }
        } finally {
            btn.dataset.loading = 'false';
            btn.disabled = false;
            btn.style.opacity = '';
            btn.innerHTML = originalText;
        }
    }
    
    // ============================================================
    // 事件绑定
    // ============================================================
    
    function bindEvents() {
        // 培训名称点击
        document.querySelectorAll('#dashboardTrainingTbody .training-name-link').forEach(link => {
            link.removeEventListener('click', handleTrainingNameClick);
            link.addEventListener('click', handleTrainingNameClick);
        });
        
        // 表头录入按钮
        document.querySelectorAll('#dashboardTrainingTbody .header-btn').forEach(btn => {
            btn.removeEventListener('click', handleHeaderBtnClick);
            btn.addEventListener('click', handleHeaderBtnClick);
        });
        
        // 推送按钮
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
        
        // 表头保存按钮
        document.getElementById('saveHeaderBtn')?.addEventListener('click', saveHeaderTemplate);
        
        // 初始化 Tooltip
        initTooltips();
    }
    
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
            } catch (e) {}
        });
    }
    
    // ============================================================
    // 推送功能（完整版）
    // ============================================================
    
    async function handlePushClick(e) {
        const btn = e.currentTarget;
        const trainingId = btn.dataset.id;
        const row = btn.closest('tr');
        const nameLink = row?.querySelector('.training-name-link');
        const trainingName = nameLink ? nameLink.textContent.trim() : `培训 #${trainingId}`;
        const statusBadge = row?.querySelector('.badge');
        const isDraft = statusBadge && statusBadge.textContent.trim() === '草稿';
        
        let startTime = btn.dataset.start;
        let endTime = btn.dataset.end;
        
        if (isDraft || !startTime || !endTime || startTime.startsWith('1970-01-01')) {
            const now = new Date();
            const thirtyDaysLater = new Date(now.getTime() + 30 * 24 * 60 * 60 * 1000);
            startTime = formatDateTimeLocal(now);
            endTime = formatDateTimeLocal(thirtyDaysLater);
        } else {
            startTime = startTime ? utcToLocalDatetimeLocal(startTime) : '';
            endTime = endTime ? utcToLocalDatetimeLocal(endTime) : '';
        }
        
        _currentPushTrainingId = trainingId;
        document.getElementById('start_time').value = startTime;
        document.getElementById('end_time').value = endTime;
        
        const nameSpan = document.getElementById('pushModalTrainingName');
        if (nameSpan) {
            nameSpan.textContent = trainingName ? `- ${escapeHtml(trainingName)}` : '';
        }
        
        const tbody = document.getElementById('pushUserListBody');
        if (tbody) {
            tbody.innerHTML = `<tr><td colspan="5" class="text-center">${safeT('loading') || '加载中...'}</td></tr>`;
        }
        
        const modalElement = document.getElementById('pushModal');
        if (modalElement) {
            const modal = new bootstrap.Modal(modalElement);
            modal.show();
        }
        
        await loadPushUserList(trainingId);
    }
    
    async function loadPushUserList(trainingId) {
        const search = document.getElementById('pushUserSearch')?.value || '';
        const whFilter = document.getElementById('pushWhFilter')?.value.toLowerCase() || '';
        const isPartner = document.getElementById('pushIsPartner')?.value || '';
        const countryFilterInput = document.getElementById('pushCountryFilter');
        const countryFilter = countryFilterInput ? countryFilterInput.value : '';
        const tid = trainingId || _currentPushTrainingId;
        
        if (!tid) {
            const tbody = document.getElementById('pushUserListBody');
            if (tbody) {
                tbody.innerHTML = '<tr><td colspan="5" class="text-center text-warning">请先选择培训</td></tr>';
            }
            return;
        }
        
        try {
            const trainingRes = await fetch(`/api/admin/trainings/${tid}`);
            if (!trainingRes.ok) throw new Error(`HTTP ${trainingRes.status}`);
            const trainingData = await trainingRes.json();
            const training = trainingData.data || trainingData;
            if (!training) {
                const tbody = document.getElementById('pushUserListBody');
                if (tbody) {
                    tbody.innerHTML = '<tr><td colspan="5" class="text-center text-danger">培训不存在</td></tr>';
                }
                return;
            }
            
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
            
            const params = new URLSearchParams();
            if (search) params.append('search', search);
            if (whFilter) params.append('wh', whFilter);
            if (isPartner) params.append('is_partner', isPartner === 'Y' ? 'true' : 'false');
            trainingCountries.forEach(c => params.append('countries', c));
            if (countryFilter) {
                const resolved = resolveCountryParam(countryFilter);
                if (resolved) params.append('country', resolved);
            }
            
            const res = await fetch(`/api/admin/users/push_list?${params.toString()}`);
            const data = await res.json();
            let users = data.data || [];
            const actualCountries = data.countries || trainingCountries;
            
            const attRes = await fetch(`/api/training/attendance/${tid}`);
            const attData = await attRes.json();
            const attendances = attData.attendances || [];
            const attendanceMap = {};
            attendances.forEach(att => {
                attendanceMap[att.user_id] = {
                    has_signature: !!(att.signature_url && att.signature_url !== '' && att.signature_url !== 'null'),
                    sign_time: att.sign_time,
                    signed_name: att.signed_name
                };
            });
            
            const filteredUsers = users.filter(user => {
                const userId = user.id;
                const attendance = attendanceMap[userId];
                if (!attendance) return true;
                if (!attendance.has_signature) return true;
                return false;
            });
            
            const tbody = document.getElementById('pushUserListBody');
            if (!tbody) return;
            
            if (filteredUsers.length === 0) {
                let message = users.length === 0 
                    ? `该培训国家(${actualCountries.join(', ')})下暂无用户`
                    : `该培训国家(${actualCountries.join(', ')})下所有用户已签到`;
                tbody.innerHTML = `
                    <tr>
                        <td colspan="5" class="text-center text-muted">
                            <i class="bi bi-info-circle me-1"></i> ${message}
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
    
    // ============================================================
    // 拷贝、删除、编辑功能
    // ============================================================
    
    async function handleCopyClick(e) {
        const btn = e.currentTarget;
        const trainingId = btn.dataset.id;
        if (!confirm('确定要拷贝此培训吗？')) return;
        
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
                countries = typeof training.countries === 'string' ? JSON.parse(training.countries) : training.countries;
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
                if (typeof showToast === 'function') {
                    showToast('培训拷贝成功', 'success');
                }
                setTimeout(() => location.reload(), 1000);
            } else {
                const error = await createRes.json();
                throw new Error(error.message || '拷贝失败');
            }
        } catch (error) {
            console.error('拷贝培训失败:', error);
            if (typeof showToast === 'function') {
                showToast('拷贝失败: ' + error.message, 'error');
            }
            btn.disabled = false;
            btn.innerHTML = originalText;
        }
    }
    
    async function handleDeleteClick(e) {
        const btn = e.currentTarget;
        const trainingId = btn.dataset.id;
        const row = btn.closest('tr');
        const nameLink = row?.querySelector('.training-name-link');
        const trainingName = nameLink ? nameLink.textContent.trim() : '';
        if (!confirm(`确定要删除培训「${trainingName}」吗？此操作不可恢复！`)) return;
        
        const originalText = btn.innerHTML;
        btn.disabled = true;
        btn.innerHTML = `<span class="spinner-border spinner-border-sm me-1"></span> ${safeT('deleting') || '删除中...'}`;
        
        try {
            const res = await fetch(`/api/admin/trainings?id=${trainingId}`, { method: 'DELETE' });
            const result = await res.json();
            if (result.success) {
                if (typeof showToast === 'function') {
                    showToast('培训删除成功', 'success');
                }
                if (row) {
                    row.style.transition = 'all 0.3s ease';
                    row.style.opacity = '0';
                    row.style.transform = 'translateX(-20px)';
                    setTimeout(() => row.remove(), 300);
                }
            } else {
                throw new Error(result.message || '删除失败');
            }
        } catch (error) {
            console.error('删除培训失败:', error);
            if (typeof showToast === 'function') {
                showToast('删除失败: ' + error.message, 'error');
            }
            btn.disabled = false;
            btn.innerHTML = originalText;
        }
    }
    
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
                onChange: (selected) => { console.log('已选国家:', selected); }
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
        saveBtn.onclick = function() { saveEdit(row, countryInstance, uniqueId); };
        const cancelBtn = actionsCell.querySelector('.cancel-edit-btn');
        cancelBtn.onclick = function() { cancelEdit(row); };
        
        const inputs = nameCell.querySelectorAll('input');
        inputs.forEach(input => {
            input.addEventListener('keydown', function(e) {
                if (e.key === 'Enter') { e.preventDefault(); saveBtn.click(); }
                else if (e.key === 'Escape') { e.preventDefault(); cancelBtn.click(); }
            });
        });
        row.classList.add('editing-mode');
        _currentEditingRow = row;
        setTimeout(() => {
            const nameInput = nameCell.querySelector('.edit-name-input');
            if (nameInput) { nameInput.focus(); nameInput.select(); }
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
            if (typeof showToast === 'function') { showToast('培训名称不能为空', 'warning'); }
            nameInput.focus();
            return;
        }
        if (countries.length === 0) {
            if (typeof showToast === 'function') { showToast('请至少选择一个国家', 'warning'); }
            return;
        }
        
        const saveBtn = row.querySelector('.save-edit-btn');
        const originalText = saveBtn.innerHTML;
        saveBtn.disabled = true;
        saveBtn.innerHTML = `<span class="spinner-border spinner-border-sm me-1"></span> ${safeT('saving') || '保存中...'}`;
        
        try {
            const updateData = { id: trainingId, name: newName, countries: countries };
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
                if (typeof showToast === 'function') { showToast('培训更新成功', 'success'); }
                if (instanceId && _countryTagInstances.has(instanceId)) {
                    const instance = _countryTagInstances.get(instanceId);
                    if (instance && typeof instance.destroy === 'function') instance.destroy();
                    _countryTagInstances.delete(instanceId);
                }
                setTimeout(() => location.reload(), 500);
            } else {
                throw new Error(result.message || '更新失败');
            }
        } catch (error) {
            console.error('更新培训失败:', error);
            if (typeof showToast === 'function') { showToast('更新失败: ' + error.message, 'error'); }
            saveBtn.disabled = false;
            saveBtn.innerHTML = originalText;
        }
    }
    
    function cancelEdit(row) {
        if (!row) return;
        _countryTagInstances.forEach((instance, key) => {
            if (instance && typeof instance.destroy === 'function') instance.destroy();
        });
        _countryTagInstances.clear();
        location.reload();
    }
    
    // ============================================================
    // 新增培训
    // ============================================================
    
    function showAddTrainingDialog() {
        const existingEditRow = document.getElementById('new-training-row');
        if (existingEditRow) existingEditRow.remove();
        const tbody = document.getElementById('dashboardTrainingTbody');
        if (!tbody) return;
        const hasData = tbody.querySelector('tr[data-training-id]');
        const newRow = document.createElement('tr');
        newRow.id = 'new-training-row';
        newRow.className = 'table-active';
        const now = new Date();
        const thirtyDaysLater = new Date(now.getTime() + 30 * 24 * 60 * 60 * 1000);
        const prefillStart = formatDateTimeLocal(now);
        const prefillEnd = formatDateTimeLocal(thirtyDaysLater);
        const uniqueId = Date.now() + '_' + Math.random().toString(36).substr(2, 6);
        
        newRow.innerHTML = `
            <td colspan="5" style="padding: 12px 8px;">
                <div class="d-flex flex-wrap align-items-center gap-2">
                    <span class="badge bg-info text-white">${safeT('new') || '新增'}</span>
                    <div style="flex: 1; min-width: 200px;">
                        <input type="text" id="newTrainingName_${uniqueId}" class="form-control form-control-sm" 
                            placeholder="${safeT('placeholder_training_name') || '培训名称'}" style="min-width: 150px;">
                    </div>
                    <div style="min-width: 200px;">
                        <div id="newCountryTag_${uniqueId}" class="country-tag-input-wrapper" style="min-width: 150px;"></div>
                    </div>
                    <div style="display: flex; align-items: center; gap: 4px; flex-wrap: wrap;">
                        <span class="text-muted small">${safeT('valid_period') || '有效期'}:</span>
                        <input type="datetime-local" id="newStartTime_${uniqueId}" class="form-control form-control-sm" 
                            value="${prefillStart}" style="width: 150px;">
                        <span class="text-muted">→</span>
                        <input type="datetime-local" id="newEndTime_${uniqueId}" class="form-control form-control-sm" 
                            value="${prefillEnd}" style="width: 150px;">
                    </div>
                    <div class="d-flex gap-1">
                        <button class="btn btn-sm btn-success save-new-training-btn" data-unique-id="${uniqueId}">
                            <i class="bi bi-check"></i> ${safeT('save') || '保存'}
                        </button>
                        <button class="btn btn-sm btn-secondary cancel-new-training-btn" data-unique-id="${uniqueId}">
                            <i class="bi bi-x"></i> ${safeT('cancel') || '取消'}
                        </button>
                    </div>
                </div>
            </td>
        `;
        
        if (hasData) {
            tbody.insertBefore(newRow, tbody.firstChild);
        } else {
            const emptyRow = tbody.querySelector('tr:not([data-training-id])');
            if (emptyRow) emptyRow.remove();
            tbody.appendChild(newRow);
        }
        
        const wrapper = document.getElementById(`newCountryTag_${uniqueId}`);
        let countryInstance = null;
        if (wrapper) {
            countryInstance = new CountryTagInput({
                container: wrapper,
                selectedCountries: [],
                placeholder: safeT('placeholder_coutry_search') || '输入国家名称或代码',
                maxTags: 30,
                onChange: (selected) => { console.log('已选国家:', selected); }
            });
        }
        
        const saveBtn = newRow.querySelector('.save-new-training-btn');
        saveBtn.onclick = async function() {
            const nameInput = document.getElementById(`newTrainingName_${uniqueId}`);
            const startInput = document.getElementById(`newStartTime_${uniqueId}`);
            const endInput = document.getElementById(`newEndTime_${uniqueId}`);
            const name = nameInput ? nameInput.value.trim() : '';
            const start = startInput ? startInput.value : '';
            const end = endInput ? endInput.value : '';
            const selectedCountries = countryInstance ? countryInstance.getSelected() : [];
            
            if (!name) {
                if (typeof showToast === 'function') { showToast(safeT('training_name_cannot_empty') || '培训名称不能为空', 'warning'); }
                if (nameInput) nameInput.focus();
                return;
            }
            if (selectedCountries.length === 0) {
                if (typeof showToast === 'function') { showToast('请至少选择一个国家', 'warning'); }
                return;
            }
            
            const startISO = start ? localDateTimeToUTC(start) : '';
            const endISO = end ? localDateTimeToUTC(end) : '';
            const originalText = saveBtn.innerHTML;
            saveBtn.disabled = true;
            saveBtn.innerHTML = `<span class="spinner-border spinner-border-sm me-1"></span> ${safeT('saving') || '保存中...'}`;
            
            try {
                const payload = { name: name, countries: selectedCountries, start_time: startISO, end_time: endISO, header_template: {} };
                const res = await fetch('/api/admin/trainings', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });
                if (res.ok) {
                    if (typeof showToast === 'function') { showToast('培训创建成功', 'success'); }
                    setTimeout(() => location.reload(), 800);
                } else {
                    const error = await res.json();
                    throw new Error(error.message || '创建失败');
                }
            } catch (error) {
                console.error('创建培训失败:', error);
                if (typeof showToast === 'function') { showToast('创建失败: ' + error.message, 'error'); }
                saveBtn.disabled = false;
                saveBtn.innerHTML = originalText;
            }
        };
        
        const cancelBtn = newRow.querySelector('.cancel-new-training-btn');
        cancelBtn.onclick = function() {
            if (countryInstance && typeof countryInstance.destroy === 'function') countryInstance.destroy();
            newRow.remove();
            const hasDataAfterRemove = tbody.querySelector('tr[data-training-id]');
            if (!hasDataAfterRemove && !tbody.querySelector('#new-training-row')) {
                let emptyRow = tbody.querySelector('tr:not([data-training-id])');
                if (!emptyRow) {
                    emptyRow = document.createElement('tr');
                    emptyRow.innerHTML = `
                        <td colspan="5" class="text-center py-4">
                            <div class="d-flex flex-column align-items-center gap-3">
                                <div class="text-muted">
                                    <i class="bi bi-inbox" style="font-size: 2rem; display: block; margin-bottom: 8px;"></i>
                                    <span data-i18n="no_training_data">暂无培训数据</span>
                                </div>
                                <button class="btn btn-primary btn-sm" id="dashboardAddTrainingBtn">
                                    <i class="bi bi-plus-circle me-1"></i>
                                    <span data-i18n="new_training">新增培训</span>
                                </button>
                            </div>
                        </td>
                    `;
                    tbody.appendChild(emptyRow);
                    const addBtn = document.getElementById('dashboardAddTrainingBtn');
                    if (addBtn) addBtn.onclick = showAddTrainingDialog;
                }
            }
        };
        
        const inputs = newRow.querySelectorAll('input');
        inputs.forEach(input => {
            input.addEventListener('keydown', function(e) {
                if (e.key === 'Enter') { e.preventDefault(); saveBtn.click(); }
                else if (e.key === 'Escape') { e.preventDefault(); cancelBtn.click(); }
            });
        });
        setTimeout(() => {
            const nameInput = document.getElementById(`newTrainingName_${uniqueId}`);
            if (nameInput) nameInput.focus();
        }, 200);
    }
    
    function bindAddTrainingButton() {
        const addBtn = document.getElementById('dashboardAddTrainingBtn');
        if (addBtn) {
            const newBtn = addBtn.cloneNode(true);
            addBtn.parentNode.replaceChild(newBtn, addBtn);
            newBtn.addEventListener('click', showAddTrainingDialog);
        }
    }
    
    // ============================================================
    // 绑定推送模态框事件
    // ============================================================
    
    function bindPushModalEvents() {
        const confirmBtn = document.getElementById('confirmPushBtn');
        if (confirmBtn) {
            const newBtn = confirmBtn.cloneNode(true);
            confirmBtn.parentNode.replaceChild(newBtn, confirmBtn);
            newBtn.addEventListener('click', confirmPushHandler);
        }
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
                    if (typeof showToast === 'function') { showToast(safeT('t_list_refreshed') || '列表已刷新', 'success'); }
                } catch (err) {
                    console.error('加载失败:', err);
                    if (typeof showToast === 'function') { showToast(safeT('load_failed') || '加载失败', 'error'); }
                } finally {
                    btn.disabled = false;
                    btn.innerHTML = originalHTML;
                }
            });
        }
        const whFilterInput = document.getElementById('pushWhFilter');
        if (whFilterInput) {
            whFilterInput.addEventListener('input', async function() {
                const q = this.value.trim();
                const datalist = document.getElementById('whDatalist');
                if (q.length === 0) { datalist.innerHTML = ''; return; }
                try {
                    const res = await fetch(`/api/search/warehouses?q=${encodeURIComponent(q)}`);
                    const items = await res.json();
                    datalist.innerHTML = items.map(name => `<option value="${escapeHtml(name)}">`).join('');
                } catch (e) { console.error('搜索库房失败', e); }
            });
        }
        setupCountryAutocompleteForPush();
    }
    
    function setupCountryAutocompleteForPush() {
        const input = document.getElementById('pushCountryFilter');
        if (!input) return;
        if (!document.getElementById('pushCountryCode')) {
            const hidden = document.createElement('input');
            hidden.type = 'hidden';
            hidden.id = 'pushCountryCode';
            input.after(hidden);
        }
        if (window._pushCountryAutocompleteInited) return;
        window._pushCountryAutocompleteInited = true;
        input.addEventListener('blur', function() {
            const val = this.value.trim();
            if (!val) { document.getElementById('pushCountryCode').value = ''; return; }
            const code = resolveCountryParam(val);
            if (code) document.getElementById('pushCountryCode').value = code;
        });
    }
    
    async function confirmPushHandler() {
        const startLocal = document.getElementById('start_time').value;
        const endLocal = document.getElementById('end_time').value;
        if (!startLocal || !endLocal) {
            alert(safeT('t_fill_start_end_time') || '请填写开始和结束时间');
            return;
        }
        const startISO = localDateTimeToUTC(startLocal);
        const endISO = localDateTimeToUTC(endLocal);
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
                    showToast(count > 0 ? `已推送给 ${count} 名选中学员` : '已推送给所有国家用户', 'success');
                }
                setTimeout(() => location.reload(), 1000);
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
    
    function initPushModal() {
        const modalElement = document.getElementById('pushModal');
        if (modalElement) {
            modalElement.addEventListener('shown.bs.modal', function() {
                setupCountryAutocompleteForPush();
                if (_currentPushTrainingId) loadPushUserList(_currentPushTrainingId);
            });
        }
        bindPushModalEvents();
    }
    
    // ============================================================
    // 重新初始化
    // ============================================================
    
    function reinit() {
        _currentEditingRow = null;
        _countryTagInstances.clear();
        bindEvents();
        initTooltips();
        initPushModal();
    }
    
    // ============================================================
    // 公共 API
    // ============================================================
    
    return {
        init: function() {
            if (_isInitialized) return;
            _isInitialized = true;
            setTimeout(function() {
                bindEvents();
                initTooltips();
                initPushModal();
                bindAddTrainingButton();
                console.log('✅ 培训列表操作模块已初始化（完整版：名称点击+表头录入+国家分组）');
            }, 300);
        },
        reinit: reinit,
        refresh: function() { location.reload(); },
        // 暴露给外部调用
        showCountryGroupModal: showCountryGroupModal,
        openHeaderModalForTraining: openHeaderModalForTraining,
        loadPushUserList: loadPushUserList
    };
})();

// 暴露到全局
window.TrainingListModule = TrainingListModule;