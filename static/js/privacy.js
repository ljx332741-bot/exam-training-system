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
            const res = await fetch('/api/privacy/status');
            const data = await res.json();

            if (data.needs_acknowledgment && data.agreement) {
                this.agreementData = data.agreement;
                await this.showModal();
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
                    <p class="text-danger">${t('failed_to_load_try_again')}</p>
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
        btn.innerHTML = `<span class="spinner-border spinner-border-sm me-2"></span>${t('confirming')}`;

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
                    showToast(t('privacy_tatement_onfirmed'), 'success');
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
                const msg = data.message || t('confirmation_failed_try_again');
                if (typeof showToast === 'function') {
                    showToast(msg, 'error');
                }
                btn.disabled = false;
                btn.innerHTML = originalText;
            }
        } catch (err) {
            console.error('确认失败:', err);
            if (typeof showToast === 'function') {
                showToast(t('network_error_retry'), 'error');
            }
            btn.disabled = false;
            btn.innerHTML = originalText;
        } finally {
            this.isLoading = false;
        }
    }
}
// privacy.js