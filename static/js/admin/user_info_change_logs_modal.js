/**
 * static/js/admin/user_info_change_logs_modal.js
 * 全局用户修改记录（审计日志）模态框
 * 
 * 用法：
 *   1. 顶部工具栏按钮 <button id="viewAuditLogBtn">操作日志</button>
 *   2. 点击 → 打开模态框 → 默认展示所有修改记录
 *   3. 支持按用户、字段、日期筛选
 */

(function () {
    'use strict';

    const API_ENDPOINT = '/api/admin/users/user_info_change_logs';
    const PER_PAGE = 20;

    // ==================== 字段中文映射 ====================
    const FIELD_LABELS = {
        'email': '邮箱',
        'name_en': '姓名',
        'name_cn': '中文名',
        'company': '公司',
        'department': '部门',
        'employee_id': '工号',
        'birthday': '生日',
        'country': '国家',
        'phone': '手机号',
        'role': '角色',
        'admin_countries': '权限范围',
        'user_status': '用户状态',
        'is_partner': '服务商',
        'wh_type': '库房类型',
        'wh_id': '库房编码',
        'wh_name_en': '库房名称',
        'is_resign': '离职状态',
        'is_rehire': '复职状态',
        'is_active': '账号激活',
        'resigned_at': '离职时间',
        'rehire_at': '复职时间',
        'remark': '备注',
        'password_hash': '密码'
    };

    const ROLE_LABELS = {
        'user': '普通用户',
        'admin': '管理员',
        'super_admin': '超级管理员',
        'developer': '开发者'
    };

    const USER_STATUS_LABELS = {
        'imported': '已添加',
        'registered': '已注册'
    };

    const BOOLEAN_FIELDS = ['is_partner', 'is_resign', 'is_rehire', 'is_active'];

    // ==================== 状态 ====================
    const state = {
        logs: [],
        total: 0,
        currentPage: 1,
        totalPages: 1,
        filters: {
            search: '',       // 用户搜索
            field_name: '',   // 字段名
            date_from: '',    // 起始日期
            date_to: ''       // 结束日期
        },
        modalInstance: null,
        initialized: false
    };

    // ==================== 工具函数 ====================

    function escapeHtmlSafe(str) {
        if (str === null || str === undefined) return '';
        return String(str).replace(/[&<>"']/g, function (m) {
            if (m === '&') return '&amp;';
            if (m === '<') return '&lt;';
            if (m === '>') return '&gt;';
            if (m === '"') return '&quot;';
            if (m === "'") return '&#39;';
            return m;
        });
    }

    function tr(key, fallback) {
        if (typeof window.t === 'function') {
            const result = window.t(key);
            if (result && result !== key) return result;
        }
        return fallback || key;
    }

    function formatLocalTime(isoString) {
        if (!isoString) return '-';
        try {
            const date = new Date(isoString);
            if (isNaN(date.getTime())) return isoString;
            const y = date.getFullYear();
            const m = String(date.getMonth() + 1).padStart(2, '0');
            const d = String(date.getDate()).padStart(2, '0');
            const hh = String(date.getHours()).padStart(2, '0');
            const mm = String(date.getMinutes()).padStart(2, '0');
            const ss = String(date.getSeconds()).padStart(2, '0');
            return `${y}-${m}-${d} ${hh}:${mm}:${ss}`;
        } catch (e) {
            return isoString;
        }
    }

    function debounce(fn, delay) {
        let timer;
        return function (...args) {
            clearTimeout(timer);
            timer = setTimeout(() => fn.apply(this, args), delay);
        };
    }

    function formatFieldValue(fieldName, value) {
        if (value === null || value === undefined || value === '') {
            return '<span class="text-muted fst-italic">空</span>';
        }
        const strValue = String(value);

        if (fieldName === 'role') {
            return escapeHtmlSafe(ROLE_LABELS[strValue] || strValue);
        }
        if (fieldName === 'user_status') {
            return escapeHtmlSafe(USER_STATUS_LABELS[strValue] || strValue);
        }
        if (BOOLEAN_FIELDS.includes(fieldName)) {
            const boolVal = strValue.toLowerCase() === 'true' || strValue === '1';
            return boolVal
                ? `<span class="badge bg-success">是</span>`
                : `<span class="badge bg-secondary">否</span>`;
        }
        if (fieldName === 'admin_countries') {
            try {
                let codes = JSON.parse(strValue);
                if (Array.isArray(codes)) {
                    if (codes.length === 0) {
                        return '<span class="text-muted fst-italic">空</span>';
                    }
                    return codes.map(c => `<span class="badge bg-info text-dark me-1">${escapeHtmlSafe(c)}</span>`).join('');
                }
                return escapeHtmlSafe(strValue);
            } catch (e) {
                return escapeHtmlSafe(strValue);
            }
        }
        if (fieldName === 'password_hash') {
            return '<span class="text-muted">***（已脱敏）***</span>';
        }
        if (fieldName === 'resigned_at' || fieldName === 'rehire_at') {
            return escapeHtmlSafe(formatLocalTime(strValue));
        }
        if (strValue.length > 200) {
            return `<span title="${escapeHtmlSafe(strValue)}">${escapeHtmlSafe(strValue.slice(0, 200))}...</span>`;
        }
        return escapeHtmlSafe(strValue);
    }

    function getFieldLabel(fieldName) {
        return FIELD_LABELS[fieldName] || fieldName;
    }

    // ==================== API 调用 ====================

    async function fetchAllAuditLogs() {
        const params = new URLSearchParams({
            page: state.currentPage,
            per_page: PER_PAGE
        });

        if (state.filters.search) params.append('search', state.filters.search);
        if (state.filters.field_name) params.append('field_name', state.filters.field_name);
        if (state.filters.date_from) params.append('date_from', state.filters.date_from);
        if (state.filters.date_to) params.append('date_to', state.filters.date_to);

        const res = await fetch(`${API_ENDPOINT}?${params.toString()}`);
        if (!res.ok) {
            throw new Error(`HTTP ${res.status}: ${res.statusText}`);
        }
        return await res.json();
    }

    // ==================== 渲染 ====================

    function renderTable() {
        const tbody = document.getElementById('auditLogsTbody');
        if (!tbody) return;

        if (!state.logs || state.logs.length === 0) {
            tbody.innerHTML = `
                <tr>
                    <td colspan="8" class="text-center text-muted" style="padding:30px;">
                        <i class="bi bi-inbox" style="font-size:1.8rem; display:block; margin-bottom:8px;"></i>
                        ${escapeHtmlSafe(tr('no_audit_records', '暂无修改记录'))}
                    </td>
                </tr>`;
            return;
        }

        const startIndex = (state.currentPage - 1) * PER_PAGE;

        const rows = state.logs.map((log, idx) => {
            const globalIndex = startIndex + idx + 1;
            const fieldName = log.field_name || '';
            const fieldLabel = getFieldLabel(fieldName);
            const oldValueHtml = formatFieldValue(fieldName, log.old_value);
            const newValueHtml = formatFieldValue(fieldName, log.new_value);
            const changedByName = log.changed_by_name || '-';
            const changedAt = formatLocalTime(log.changed_at);
            const userName = log.user_name || '-';
            const userEmail = log.user_email || '';

            const oldCellClass = (log.old_value === null || log.old_value === '') ? 'text-muted' : '';
            const newCellClass = (log.new_value === null || log.new_value === '') ? 'text-muted' : '';

            return `
                <tr>
                    <td class="text-muted">${globalIndex}</td>
                    <td>
                        <div>${escapeHtmlSafe(userName)}</div>
                        ${userEmail ? `<small class="text-muted">${escapeHtmlSafe(userEmail)}</small>` : ''}
                    </td>
                    <td>
                        <span class="badge bg-light text-dark border" title="${escapeHtmlSafe(fieldName)}">
                            ${escapeHtmlSafe(fieldLabel)}
                        </span>
                    </td>
                    <td class="${oldCellClass}" style="max-width:200px; word-break:break-all;">
                        ${oldValueHtml}
                    </td>
                    <td class="${newCellClass}" style="max-width:200px; word-break:break-all;">
                        ${newValueHtml}
                    </td>
                    <td>${escapeHtmlSafe(changedByName)}</td>
                    <td class="text-nowrap"><small>${escapeHtmlSafe(changedAt)}</small></td>
                </tr>
            `;
        }).join('');

        tbody.innerHTML = rows;
    }

    function renderPagination() {
        const paginationEl = document.getElementById('auditPagination');
        if (!paginationEl) return;

        const totalCountEl = document.getElementById('auditTotalCount');
        if (totalCountEl) totalCountEl.textContent = state.total;

        const totalPages = state.totalPages;
        const current = state.currentPage;

        if (totalPages <= 1) {
            paginationEl.innerHTML = '';
            return;
        }

        let html = '';

        html += `
            <li class="page-item ${current === 1 ? 'disabled' : ''}">
                <a class="page-link" href="#" data-page="${current - 1}">
                    <i class="bi bi-chevron-left"></i>
                </a>
            </li>`;

        let startPage = Math.max(1, current - 3);
        let endPage = Math.min(totalPages, startPage + 6);
        if (endPage - startPage < 6) {
            startPage = Math.max(1, endPage - 6);
        }

        if (startPage > 1) {
            html += `<li class="page-item"><a class="page-link" href="#" data-page="1">1</a></li>`;
            if (startPage > 2) {
                html += `<li class="page-item disabled"><span class="page-link">...</span></li>`;
            }
        }

        for (let i = startPage; i <= endPage; i++) {
            html += `
                <li class="page-item ${i === current ? 'active' : ''}">
                    <a class="page-link" href="#" data-page="${i}">${i}</a>
                </li>`;
        }

        if (endPage < totalPages) {
            if (endPage < totalPages - 1) {
                html += `<li class="page-item disabled"><span class="page-link">...</span></li>`;
            }
            html += `<li class="page-item"><a class="page-link" href="#" data-page="${totalPages}">${totalPages}</a></li>`;
        }

        html += `
            <li class="page-item ${current === totalPages ? 'disabled' : ''}">
                <a class="page-link" href="#" data-page="${current + 1}">
                    <i class="bi bi-chevron-right"></i>
                </a>
            </li>`;

        paginationEl.innerHTML = html;

        paginationEl.querySelectorAll('.page-link').forEach(link => {
            link.addEventListener('click', (e) => {
                e.preventDefault();
                const page = parseInt(link.dataset.page, 10);
                if (isNaN(page) || page === current || page < 1 || page > totalPages) return;
                state.currentPage = page;
                loadLogs();
            });
        });
    }

    // ==================== 加载数据 ====================

    async function loadLogs() {
        const tbody = document.getElementById('auditLogsTbody');
        if (tbody) {
            tbody.innerHTML = `
                <tr>
                    <td colspan="8" class="text-center" style="padding:30px;">
                        <div class="spinner-border text-primary" role="status" style="width:1.8rem; height:1.8rem;">
                            <span class="visually-hidden">Loading...</span>
                        </div>
                        <div class="text-muted mt-2">${escapeHtmlSafe(tr('loading', '加载中...'))}</div>
                    </td>
                </tr>`;
        }

        try {
            const data = await fetchAllAuditLogs();
            if (!data.success) {
                throw new Error(data.message || 'API 返回失败');
            }

            state.logs = data.data || [];
            state.total = data.total || 0;
            state.currentPage = data.page || 1;
            state.totalPages = Math.ceil(state.total / PER_PAGE) || 1;

            renderTable();
            renderPagination();
        } catch (err) {
            console.error('加载审计日志失败:', err);
            if (tbody) {
                tbody.innerHTML = `
                    <tr>
                        <td colspan="8" class="text-center text-danger" style="padding:30px;">
                            <i class="bi bi-exclamation-triangle" style="font-size:1.8rem; display:block; margin-bottom:8px;"></i>
                            ${escapeHtmlSafe(tr('load_failed', '加载失败'))}: ${escapeHtmlSafe(err.message)}
                        </td>
                    </tr>`;
            }
        }
    }

    // ==================== 筛选事件 ====================

    function bindFilterEvents() {
        // 用户搜索框
        const searchInput = document.getElementById('auditUserSearch');
        if (searchInput) {
            searchInput.addEventListener('input', debounce(function () {
                state.filters.search = this.value.trim();
                state.currentPage = 1;
                loadLogs();
            }, 300));
        }

        // 字段名筛选
        const fieldInput = document.getElementById('auditFieldFilter');
        if (fieldInput) {
            fieldInput.addEventListener('input', debounce(function () {
                state.filters.field_name = this.value.trim();
                state.currentPage = 1;
                loadLogs();
            }, 300));
        }

        // 起始日期
        const dateFrom = document.getElementById('auditDateFrom');
        if (dateFrom) {
            dateFrom.addEventListener('change', function () {
                state.filters.date_from = this.value;
                state.currentPage = 1;
                loadLogs();
            });
        }

        // 结束日期
        const dateTo = document.getElementById('auditDateTo');
        if (dateTo) {
            dateTo.addEventListener('change', function () {
                state.filters.date_to = this.value;
                state.currentPage = 1;
                loadLogs();
            });
        }

        // 重置按钮
        const resetBtn = document.getElementById('auditResetBtn');
        if (resetBtn) {
            resetBtn.addEventListener('click', function () {
                state.filters = { search: '', field_name: '', date_from: '', date_to: '' };
                state.currentPage = 1;
                if (searchInput) searchInput.value = '';
                if (fieldInput) fieldInput.value = '';
                if (dateFrom) dateFrom.value = '';
                if (dateTo) dateTo.value = '';
                loadLogs();
            });
        }
    }

    // ==================== 模态框控制 ====================

    async function openAuditModal() {
        const modalEl = document.getElementById('userAuditModal');
        if (!modalEl) {
            console.error('未找到 #userAuditModal 元素');
            alert('审计日志模态框未加载，请刷新页面重试');
            return;
        }

        if (!state.modalInstance) {
            state.modalInstance = new bootstrap.Modal(modalEl, {
                backdrop: true,
                keyboard: true
            });
        }

        // 重置筛选条件
        state.filters = { search: '', field_name: '', date_from: '', date_to: '' };
        state.currentPage = 1;
        const searchInput = document.getElementById('auditUserSearch');
        const fieldInput = document.getElementById('auditFieldFilter');
        const dateFrom = document.getElementById('auditDateFrom');
        const dateTo = document.getElementById('auditDateTo');
        if (searchInput) searchInput.value = '';
        if (fieldInput) fieldInput.value = '';
        if (dateFrom) dateFrom.value = '';
        if (dateTo) dateTo.value = '';

        state.modalInstance.show();
        await loadLogs();
    }

    // ==================== 初始化 ====================

    function init() {
        if (state.initialized) return;
        state.initialized = true;

        bindFilterEvents();

        const viewBtn = document.getElementById('viewAuditLogBtn');
        if (viewBtn) {
            viewBtn.addEventListener('click', function () {
                openAuditModal();
            });
        }

        const modalEl = document.getElementById('userAuditModal');
        if (modalEl) {
            modalEl.addEventListener('hidden.bs.modal', function () {
                state.logs = [];
                state.total = 0;
                state.currentPage = 1;
            });
        }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

    window.UserAuditModal = {
        open: openAuditModal,
        refresh: loadLogs
    };

})();