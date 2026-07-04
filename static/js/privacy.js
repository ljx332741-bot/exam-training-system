    // static/js/privacy.js
    /**
     * 隐私声明管理 - 前端模块
     */

    class PrivacyManager {
        constructor() {
            this.modal = null;
            this.agreementData = null;
            this.isLoading = false;
            this.initialized = false;
            this.onConfirmCallback = null;
        }

        init(options = {}) {
            if (this.initialized) return;
            this.initialized = true;
            
            if (options.onConfirm) {
                this.onConfirmCallback = options.onConfirm;
            }
            
            this.bindEvents();
            this.checkStatus();
        }

        bindEvents() {
            const confirmBtn = document.getElementById('privacyConfirmBtn');
            const agreeCheck = document.getElementById('privacyAgreeCheck');

            if (confirmBtn) {
                confirmBtn.addEventListener('click', () => {
                    this.confirmAgreement();
                });
            }

            if (agreeCheck) {
                agreeCheck.addEventListener('change', (e) => {
                    if (confirmBtn) {
                        confirmBtn.disabled = !e.target.checked;
                    }
                });
            }

            // 防止用户通过点击外部关闭
            const modalEl = document.getElementById('privacyModal');
            if (modalEl) {
                modalEl.addEventListener('hide.bs.modal', (e) => {
                    if (!this.agreementData?.acknowledged) {
                        e.preventDefault();
                        return false;
                    }
                });
            }
        }

        async checkStatus() {
            try {
                console.log('🔍 检查隐私状态...');
                const res = await fetch('/api/privacy/status');
                const data = await res.json();
                console.log('📊 隐私状态:', data);

                if (data.needs_acknowledgment && data.agreement) {
                    console.log('✅ 需要确认隐私声明，显示弹窗');
                    this.agreementData = data.agreement;
                    await this.showModal();
                } else {
                    console.log('❌ 不需要确认隐私声明');
                }
            } catch (err) {
                console.error('检查隐私状态失败:', err);
            }
        }

        async showModal() {
            await this.loadContent();
            
            const modalEl = document.getElementById('privacyModal');
            this.modal = new bootstrap.Modal(modalEl, {
                backdrop: 'static',
                keyboard: false
            });
            this.modal.show();
        }

        async loadContent() {
            const container = document.getElementById('privacyContent');

            // ✅ 检查容器是否存在
            if (!container) {
                console.error('❌ privacyContent 元素不存在，请确保 privacy_modal.html 已引入');
                return;
            }
            
            try {
                const res = await fetch('/api/privacy/current');
                const data = await res.json();

                if (data.success) {
                    const agreement = data.data;
                    
                    // 更新版本和日期
                    const versionEl = document.getElementById('privacyVersion');
                    const dateEl = document.getElementById('privacyDate');
                    
                    if (versionEl) {
                        versionEl.textContent = `v${agreement.version}`;
                    }
                    if (dateEl && agreement.created_at) {
                        dateEl.textContent = `${t('update_at')} ${new Date(agreement.created_at).toLocaleDateString()}`;
                    }
                    
                    // 渲染内容（支持 HTML）
                    container.innerHTML = agreement.content;
                    
                    // 存储 agreement_id
                    this.agreementData = agreement;
                } else {
                    container.innerHTML = `
                        <div class="text-center py-4">
                            <i class="bi bi-shield-exclamation" style="font-size: 2rem; color: #fbbf24; display: block; margin-bottom: 12px;"></i>
                            <p class="text-muted">${t('no_privacy_statement_content')}</p>
                        </div>
                    `;
                }
            } catch (err) {
                console.error('加载隐私声明失败:', err);
                container.innerHTML = `
                    <div class="text-center py-4">
                        <i class="bi bi-exclamation-triangle" style="font-size: 2rem; color: #f87171; display: block; margin-bottom: 12px;"></i>
                        <p class="text-danger">加载失败，请刷新页面重试</p>
                    </div>
                `;
            }
        }

        async confirmAgreement() {
            if (this.isLoading) return;
            
            const btn = document.getElementById('privacyConfirmBtn');
            const originalText = btn.innerHTML;
            
            this.isLoading = true;
            btn.disabled = true;
            btn.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span> 确认中...';

            try {
                const res = await fetch('/api/privacy/agree', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        agreement_id: this.agreementData.id
                    })
                });
                
                const data = await res.json();
                
                if (data.success) {
                    // 标记已确认
                    this.agreementData.acknowledged = true;
                    
                    // 关闭模态框
                    if (this.modal) {
                        this.modal.hide();
                    }
                    
                    // 显示成功提示
                    if (typeof showToast === 'function') {
                        showToast('已确认隐私声明', 'success');
                    }
                    
                    // 执行回调
                    if (typeof this.onConfirmCallback === 'function') {
                        this.onConfirmCallback();
                    }
                    
                    // 移除遮罩残留
                    setTimeout(() => {
                        document.querySelectorAll('.modal-backdrop').forEach(el => el.remove());
                        document.body.classList.remove('modal-open');
                    }, 300);
                } else {
                    const msg = data.message || '确认失败，请重试';
                    if (typeof showToast === 'function') {
                        showToast(msg, 'error');
                    }
                    btn.disabled = false;
                    btn.innerHTML = originalText;
                }
            } catch (err) {
                console.error('确认失败:', err);
                if (typeof showToast === 'function') {
                    showToast('网络错误，请重试', 'error');
                }
                btn.disabled = false;
                btn.innerHTML = originalText;
            } finally {
                this.isLoading = false;
            }
        }
    }

    // 初始化
    document.addEventListener('DOMContentLoaded', function() {
        // 检查是否已初始化
        if (window._privacyManagerInitialized) return;
        window._privacyManagerInitialized = true;
        
        const privacyManager = new PrivacyManager();
        privacyManager.init({
            onConfirm: function() {
                // 确认后可执行额外操作，如刷新页面数据
                console.log('隐私声明已确认');
                // 如果当前在 dashboard，刷新数据
                if (window.location.pathname.includes('/dashboard')) {
                    if (typeof loadDashboardData === 'function') {
                        loadDashboardData();
                    }
                }
            }
        });
        
        // 暴露到全局
        window.privacyManager = privacyManager;
    });