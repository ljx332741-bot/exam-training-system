# utils/email_notifier.py
from enum import Enum
from typing import Dict, Any, Optional
from string import Template
from datetime import datetime
from services import auth, exam, export
from services.db import get_supabase
import pytz, os
import logging

logger = logging.getLogger(__name__)

# 默认时区（可以从环境变量或用户配置读取）
DEFAULT_TIMEZONE = os.environ.get('LOCAL_TIMEZONE', 'Asia/Kathmandu')  # Nepal 时区

class EmailScenario(Enum):
    """邮件场景枚举 - 便于类型检查和自动补全"""
    PASSWORD_RESET = "password_reset"      # 密码重置
    EXAM_ASSIGNMENT = "exam_assignment"    # 考试分配/推送
    USER_CREATED = "user_created"          # 用户创建
    ACCOUNT_RESTORED = "account_restored"  # 账号恢复（可选扩展）
    TRAINING_ASSIGNMENT = "training_assignment"  # 培训分配/推送（可选扩展）

# ============================================================================
# 📦 模板配置中心 - 所有邮件模板集中管理，便于维护和国际化
# ============================================================================
EMAIL_TEMPLATES: Dict[EmailScenario, Dict[str, Any]] = {
    EmailScenario.PASSWORD_RESET: {
        "subject_zh": "您的考试系统密码已重置",
        "subject_en": "Your Exam System Password Has Been Reset",
        "body_template": Template("""
            <p style="margin: 0 0 10px; font-size: 16px;">尊敬的 <strong>$name</strong>，您好！</p>
            <p style="margin: 0 0 22px; font-size: 16px;">Dear <strong>$name</strong>,</p>

            <p style="margin: 0 0 16px; font-size: 15px;">您的考试系统账号密码已被管理员重置。</p>
            <p style="margin: 0 0 26px; font-size: 15px;">Your exam system account password has been reset by the administrator.</p>

            <table role="presentation" width="100%" cellpadding="14" cellspacing="0" style="background-color: #f8f9fa; border-radius: 8px; border-left: 4px solid #2a5298; margin-bottom: 26px;">
                <tr>
                    <td>
                        <p style="margin: 0 0 10px; font-size: 15px;"><strong>🔑 新密码 / New Password:</strong> <code style="background: #e9ecef; padding: 2px 6px; border-radius: 3px;">$new_password</code></p>
                        <p style="margin: 0; font-size: 15px;"><strong>💡 安全提示 / Security Tip:</strong> 请登录后立即修改密码 / Please change your password immediately after login</p>
                    </td>
                </tr>
            </table>

            <p style="text-align: center; margin: 0;">
                <a href="$host_url" target="_blank" rel="noopener noreferrer" style="display: inline-block; background: #2a5298; color: #ffffff; padding: 14px 36px; text-decoration: none; border-radius: 6px; font-size: 16px; font-weight: 500;">🌐 立即登录 / Login Now</a>
            </p>
        """),
        "required_params": ["name", "new_password", "host_url"]
    },
    
    EmailScenario.EXAM_ASSIGNMENT: {
        "subject_zh": "考试通知",
        "subject_en": "Exam Notification",
        "body_template": Template("""
            <p style="margin: 0 0 10px; font-size: 16px;">尊敬的 <strong>$name</strong>，您好！</p>
            <p style="margin: 0 0 22px; font-size: 16px;">Dear <strong>$name</strong>,</p>

            <p style="margin: 0 0 16px; font-size: 15px;">您已成功分配一场在线考试，具体安排如下：</p>
            <p style="margin: 0 0 26px; font-size: 15px;">You have been assigned an online exam. Please find the details below:</p>

            <table role="presentation" width="100%" cellpadding="14" cellspacing="0" style="background-color: #f8f9fa; border-radius: 8px; border-left: 4px solid #2a5298; margin-bottom: 26px;">
                <tr>
                    <td>
                        <p style="margin: 0 0 8px; font-size: 15px;"><strong>📝 考试科目 / Exam:</strong> $exam_title</p>
                        <p style="margin: 0 0 8px; font-size: 15px;"><strong>⏰ 有效时间 / Validity:</strong> $start_display ——> $end_display</p>
                        <p style="margin: 0 0 8px; font-size: 15px;"><strong>🕐 考试时长 / Duration:</strong> $duration 分钟 / minutes</p>
                        <p style="margin: 0; font-size: 15px;"><strong>👨‍🏫 阅卷人 / Reviewer:</strong> $reviewer</p>
                    </td>
                </tr>
            </table>

            <p style="margin: 0 0 16px; font-size: 15px;">请在上述规定时间内登录系统完成考试。逾期未参加将视为自动放弃。</p>
            <p style="margin: 0 0 30px; font-size: 15px;">Please log in and complete the exam within the specified period. Failure to participate will be considered a forfeiture.</p>

            <p style="text-align: center; margin: 0;">
                <a href="$host_url" target="_blank" rel="noopener noreferrer" style="display: inline-block; background: #2a5298; color: #ffffff; padding: 14px 36px; text-decoration: none; border-radius: 6px; font-size: 16px; font-weight: 500;">🌐 立即登录系统 / Login Now</a>
            </p>
        """),
        "required_params": ["name", "exam_title", "start_display", "end_display", "duration", "reviewer", "host_url"]
    },
    
    EmailScenario.USER_CREATED: {
        "subject_zh": "您的考试系统账号已创建",
        "subject_en": "Your Exam System Account Has Been Created",
        "body_template": Template("""
            <p style="margin: 0 0 10px; font-size: 16px;">尊敬的 <strong>$name</strong>，您好！</p>
            <p style="margin: 0 0 22px; font-size: 16px;">Dear <strong>$name</strong>,</p>

            <p style="margin: 0 0 16px; font-size: 15px;">您的在线考试系统账号已由管理员创建成功。</p>
            <p style="margin: 0 0 26px; font-size: 15px;">Your online exam system account has been successfully created by the administrator.</p>

            <table role="presentation" width="100%" cellpadding="14" cellspacing="0" style="background-color: #f8f9fa; border-radius: 8px; border-left: 4px solid #2a5298; margin-bottom: 26px;">
                <tr>
                    <td>
                        <p style="margin: 0 0 8px; font-size: 15px;"><strong>📧 登录邮箱 / Login Email:</strong> $email</p>
                        <p style="margin: 0 0 8px; font-size: 15px;"><strong>🔑 临时密码 / Temp Password:</strong> <code style="background: #e9ecef; padding: 2px 6px; border-radius: 3px;">$temp_password</code></p>
                        <p style="margin: 0; font-size: 15px;"><strong>💡 首次登录提示:</strong> 请登录后立即修改密码 / Please change your password after first login</p>
                    </td>
                </tr>
            </table>

            <p style="text-align: center; margin: 0;">
                <a href="$host_url" target="_blank" rel="noopener noreferrer" style="display: inline-block; background: #2a5298; color: #ffffff; padding: 14px 36px; text-decoration: none; border-radius: 6px; font-size: 16px; font-weight: 500;">🌐 立即激活账号 / Activate Account</a>
            </p>
        """),
        "required_params": ["name", "email", "temp_password", "host_url"]
    },
    
    EmailScenario.ACCOUNT_RESTORED: {
        "subject_zh": "您的考试系统账号已恢复",
        "subject_en": "Your Exam System Account Has Been Restored",
        "body_template": Template("""
            <p style="margin: 0 0 10px; font-size: 16px;">尊敬的 <strong>$name</strong>，您好！</p>
            <p style="margin: 0 0 22px; font-size: 16px;">Dear <strong>$name</strong>,</p>

            <p style="margin: 0 0 16px; font-size: 15px;">您的在线考试系统账号已由管理员恢复启用。</p>
            <p style="margin: 0 0 26px; font-size: 15px;">Your online exam system account has been restored and reactivated by the administrator.</p>

            <table role="presentation" width="100%" cellpadding="14" cellspacing="0" style="background-color: #f8f9fa; border-radius: 8px; border-left: 4px solid #2a5298; margin-bottom: 26px;">
                <tr>
                    <td>
                        <p style="margin: 0 0 8px; font-size: 15px;"><strong>📧 登录邮箱 / Login Email:</strong> $email</p>
                        <p style="margin: 0 0 8px; font-size: 15px;"><strong>🔑 新密码 / New Password:</strong> <code style="background: #e9ecef; padding: 2px 6px; border-radius: 3px;">$new_password</code></p>
                    </td>
                </tr>
            </table>

            <p style="margin: 0 0 16px; font-size: 15px;">请使用新密码登录系统，并建议尽快修改为个人专属密码。</p>
            <p style="margin: 0 0 30px; font-size: 15px;">Please log in with the new password and consider changing it to a personal one for security.</p>

            <p style="text-align: center; margin: 0;">
                <a href="$host_url" target="_blank" rel="noopener noreferrer" style="display: inline-block; background: #2a5298; color: #ffffff; padding: 14px 36px; text-decoration: none; border-radius: 6px; font-size: 16px; font-weight: 500;">🌐 立即登录 / Login Now</a>
            </p>
        """),
        "required_params": ["name", "email", "new_password", "host_url"]
    },

    EmailScenario.TRAINING_ASSIGNMENT: {
        "subject_zh": "培训签到通知",
        "subject_en": "Training Attendance Notification",
        "body_template": Template("""
            <p>尊敬的 <strong>$name</strong>，您好！</p>
            <p>Dear <strong>$name</strong>,</p>

            <p>您有一场培训需要签到：<strong>$training_name</strong></p>
            <p>You have been assigned a training session: <strong>$training_name</strong></p>

            <table style="background-color: #f8f9fa; border-radius: 8px; border-left: 4px solid #2a5298; padding: 14px;">
                <tr><td>📅 有效期 / Validity: $start_display ——> $end_display</td></tr>
            </table>

            <p><a href="$host_url" style="background: #2a5298; color: white; padding: 12px 28px; text-decoration: none; border-radius: 6px;">📝 立即签到 / Sign In Now</a></p>
        """),
        "required_params": ["name", "training_name", "start_display", "end_display", "host_url"]
    }
}

# ============================================================================
# 🎨 邮件外壳模板 - 统一视觉风格，内容区域动态注入
# ============================================================================
EMAIL_SHELL_TEMPLATE = Template("""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body style="margin: 0; padding: 0; background-color: #f4f6f8; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width: 620px; margin: 24px auto; background: #ffffff; border-radius: 10px; box-shadow: 0 2px 8px rgba(0,0,0,0.08); overflow: hidden;">
        <!-- Header -->
        <tr>
            <td style="background: linear-gradient(135deg, #1e3c72 0%, #2a5298 100%); padding: 16px 30px; text-align: center;">
                <h1 style="color: #ffffff; margin: 0; font-size: 24px; font-weight: 600; letter-spacing: 0.5px;">$header_title</h1>
            </td>
        </tr>
        <!-- Content -->
        <tr>
            <td style="padding: 28px 32px; color: #333333; line-height: 1.7;">
                $content_body
            </td>
        </tr>
        <!-- Footer -->
        <tr>
            <td style="background-color: #f1f3f5; padding: 20px 32px; text-align: center; font-size: 13px; color: #666666; border-top: 1px solid #e0e0e0;">
                <p style="margin: 0 0 6px;">祝您使用愉快！ / Best regards!</p>
                <p style="margin: 0;">此邮件由系统自动发送，请勿直接回复。 / This is an automated message. Please do not reply directly.</p>
            </td>
        </tr>
    </table>
</body>
</html>""")

def _format_time(time_str: Optional[str]) -> str:
    """
    将 UTC 时间格式化为本地时间字符串（用于邮件显示）
    
    支持格式:
    - 2026-05-25T08:15:00.000Z
    - 2026-05-25T08:15:00+00:00
    - 2026-05-25 08:15:00
    """
    if not time_str:
        return "待定 / TBD"
    
    try:
        # 处理 Z 结尾的 UTC 时间
        if time_str.endswith('Z'):
            time_str = time_str.replace('Z', '+00:00')
        
        # 如果已经是简单格式（没有 T），直接返回
        if 'T' not in time_str and ' ' in time_str:
            # 假设已经是本地时间格式
            return time_str[:19] if len(time_str) >= 19 else time_str
        
        # 解析 ISO 格式时间
        dt = datetime.fromisoformat(time_str)
        
        # 如果时间没有时区信息，假定为 UTC
        if dt.tzinfo is None:
            dt = pytz.UTC.localize(dt)
        
        # 转换为本地时区
        local_tz = pytz.timezone(DEFAULT_TIMEZONE)
        local_dt = dt.astimezone(local_tz)
        
        # 格式化显示 YYYY-MM-DD HH:MM:SS
        return local_dt.strftime('%Y-%m-%d %H:%M:%S')
    
    except Exception as e:
        logger.warning(f"时间格式化失败: {time_str}, error: {e}")
        # 降级处理：简单替换
        result = time_str.replace('T', ' ').replace('Z', '').replace('+00:00', '')
        return result[:19] if len(result) >= 19 else result

def _format_date(date_str: Optional[str]) -> str:
    """格式化日期（只显示年月日）"""
    if not date_str:
        return "待定 / TBD"
    
    formatted = _format_time(date_str)
    return formatted[:10] if len(formatted) >= 10 else formatted

def send_bilingual_notification(
    email: str,
    scenario: EmailScenario,
    params: Dict[str, Any],
    host_url: str,
    auth_module: Any,  # 传入 auth 模块以调用 send_email
    custom_subject: Optional[str] = None
) -> bool:
    """
    通用双语邮件发送函数
    
    Args:
        email: 收件人邮箱
        scenario: 邮件场景枚举 (EmailScenario)
        params: 模板参数字典，需包含场景所需的 required_params
        host_url: 系统登录地址（自动注入模板）
        auth_module: 包含 send_email 方法的认证模块
        custom_subject: 可选，自定义邮件主题（覆盖默认双语主题）
    
    Returns:
        bool: 发送是否成功
    
    Raises:
        ValueError: 当 params 缺少必需参数时
    """
    # 1. 获取场景配置
    if scenario not in EMAIL_TEMPLATES:
        logger.error(f"未知的邮件场景: {scenario}")
        return False
    
    config = EMAIL_TEMPLATES[scenario]
    
    # 2. 校验必需参数
    missing_params = [p for p in config["required_params"] if p not in params]
    if missing_params:
        raise ValueError(f"场景 [{scenario.value}] 缺少必需参数: {missing_params}")
    
    # 3. 准备模板变量（自动注入 host_url）
    template_vars = {**params, "host_url": host_url}
    
    # 4. 渲染邮件主题（支持自定义或默认双语）
    if custom_subject:
        subject = custom_subject
    else:
        subject = f"{config['subject_zh']} / {config['subject_en']}"
    
    # 5. 渲染邮件正文内容
    content_body = config["body_template"].safe_substitute(template_vars)
    
    # 6. 设置头部标题（根据场景动态生成）
    header_map = {
        EmailScenario.PASSWORD_RESET: "🔐 密码重置 / Password Reset",
        EmailScenario.EXAM_ASSIGNMENT: "📚 考试通知 / Exam Notification", 
        EmailScenario.USER_CREATED: "✅ 账号创建 / Account Created",
        EmailScenario.ACCOUNT_RESTORED: "♻️ 账号恢复 / Account Restored"
    }
    header_title = header_map.get(scenario, "系统通知 / System Notification")
    
    # 7. 组装完整 HTML 邮件
    full_body = EMAIL_SHELL_TEMPLATE.safe_substitute(
        header_title=header_title,
        content_body=content_body
    )
    
    # 8. 发送邮件（假设 auth_module.send_email 支持 HTML）
    try:
        # 根据实际 auth 模块调整参数，如 html=True 或 content_type='html'
        return auth_module.send_email(email, subject, full_body)
    except Exception as e:
        logger.warning(f"邮件发送失败 [{scenario.value}] to {email}: {e}")
        return False


def _send_training_notifications(training_id, start_time, end_time, user_ids, host_url):
    """
    发送培训推送邮件通知（后台线程执行）
    """
    db = get_supabase()
    
    # 1. 获取培训名称
    training_res = db.table("trainings").select("name").eq("id", training_id).maybe_single().execute()
    if not training_res.data:
        logger.warning(f"培训 {training_id} 不存在，无法发送通知")
        return
    
    training_name = training_res.data.get('name', '培训')
    
    # 2. 获取收件人列表
    if user_ids and len(user_ids) > 0:
        # 使用指定的用户列表
        users_res = db.table("users").select("id, email, name_en").in_("id", user_ids).execute()
        recipients = users_res.data or []
    else:
        # 获取所有已注册且有邮箱的用户
        users_res = db.table("users").select("id, email, name_en")\
            .eq("user_status", "registered")\
            .not_.is_("email", "null")\
            .execute()
        recipients = users_res.data or []
    
    if not recipients:
        logger.warning(f"培训 {training_id} 没有可发送邮件的收件人")
        return
    
    # 3. 格式化时间
    start_display = _format_time(start_time)
    end_display = _format_time(end_time)
    
    # 4. 批量发送邮件
    success_count = 0
    fail_count = 0
    
    for user in recipients:
        email = user.get('email')
        name = user.get('name_en') or '用户'
        
        if not email:
            continue
        
        try:
            send_bilingual_notification(
                email=email,
                scenario=EmailScenario.TRAINING_ASSIGNMENT,
                params={
                    "name": name,
                    "training_name": training_name,
                    "start_display": start_display,
                    "end_display": end_display,
                    "host_url": host_url
                },
                host_url=host_url,
                auth_module=auth
            )
            success_count += 1
        except Exception as e:
            fail_count += 1
            logger.warning(f"培训邮件发送失败 {email}: {e}")
    
    logger.info(f"培训 {training_id} 邮件发送完成: 成功={success_count}, 失败={fail_count}")
