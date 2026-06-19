# services/auth.py - SHA256+bcrypt 终极修复版
import smtplib, random, string, logging, os, json, httpx, hashlib, re
from email.mime.text import MIMEText
from datetime import datetime, timedelta, timezone
from argon2 import PasswordHasher  # 🔁 新增
from argon2.exceptions import VerifyMismatchError  # 🔁 新增
from config import Config
from services.db import get_supabase
from email.mime.multipart import MIMEMultipart

# 初始化 hasher（全局单例）
ph = PasswordHasher()

logger = logging.getLogger(__name__)

def send_otp(email: str) -> bool:
    """发送邮箱验证码"""
    logger.info(f"📧 尝试发送验证码到: {email}")
    
    if not _is_valid_email(email):
        logger.warning(f"❌ 邮箱格式无效: {email}")
        return False
    
    otp = "".join(random.choices(string.digits, k=6))
    expires_at = (datetime.now(timezone.utc) + timedelta(minutes=Config.OTP_EXPIRE_MIN)).isoformat()
    
    try:
        db = get_supabase()
        db.table("email_otps").upsert({
            "email": email, "code": otp, "expires_at": expires_at
        }).execute()
        logger.debug(f"✅ 验证码已存入数据库: {email[:3]}***@***")
    except Exception as e:
        logger.error(f"❌ 数据库写入失败: {type(e).__name__}: {e}")
        return False
    
    try:
        if Config.EMAIL_PROVIDER == "brevo":
            return _send_via_brevo(email, otp)
        elif Config.EMAIL_PROVIDER == "smtp":
            return _send_via_smtp(email, otp)
        else:
            if os.environ.get("DEV_SKIP_OTP") == "true":
                logger.info(f"🧪 [DEV MODE] 跳过发送，验证码: {otp}")
                return True
            logger.error(f"❌ 未配置有效的邮件通道: EMAIL_PROVIDER={Config.EMAIL_PROVIDER}")
            return False
    except Exception as e:
        logger.exception(f"❌ send_otp 主流程异常: {type(e).__name__}: {e}")
        return False

def send_email(to, subject, body, is_html=True):
    """
    发送邮件（支持纯文本和 HTML）
    
    Args:
        to: 收件人邮箱
        subject: 邮件主题
        body: 邮件内容（如果 is_html=True，应为 HTML 格式）
        is_html: 是否为 HTML 格式，默认 True
    """
    # 优先使用 Brevo API
    if Config.EMAIL_PROVIDER == 'brevo' and Config.BREVO_API_KEY:
        import requests
        url = "https://api.brevo.com/v3/smtp/email"
        headers = {
            "api-key": Config.BREVO_API_KEY,
            "Content-Type": "application/json"
        }
        
        # ✅ 根据 is_html 决定内容类型
        if is_html:
            html_content = body  # 直接使用 HTML 内容
            text_content = None
        else:
            html_content = None
            text_content = body
        
        payload = {
            "sender": {"email": Config.FROM_EMAIL, "name": Config.FROM_NAME},
            "to": [{"email": to}],
            "subject": subject,
        }
        
        # 添加内容（优先 HTML）
        if html_content:
            payload["htmlContent"] = html_content
        if text_content:
            payload["textContent"] = text_content
        
        response = requests.post(url, json=payload, headers=headers)
        if response.status_code != 201:
            raise Exception(f"Brevo send failed: {response.text}")
        return True
    
    # 降级使用 SMTP
    if Config.SMTP_EMAIL and Config.SMTP_AUTH_CODE:
        msg = MIMEMultipart('alternative')
        msg['From'] = Config.FROM_EMAIL
        msg['To'] = to
        msg['Subject'] = subject
        
        # 添加纯文本版本
        plain_text = body.replace('<br>', '\n').replace('</p>', '\n').replace('</div>', '\n')
        plain_text = re.sub(r'<[^>]+>', '', plain_text)  # 移除 HTML 标签
        msg.attach(MIMEText(plain_text, 'plain', 'utf-8'))
        
        # 如果是 HTML 格式，也添加 HTML 版本
        if is_html:
            msg.attach(MIMEText(body, 'html', 'utf-8'))
        
        with smtplib.SMTP_SSL(Config.SMTP_SERVER, Config.SMTP_PORT) as server:
            server.login(Config.SMTP_EMAIL, Config.SMTP_AUTH_CODE)
            server.send_message(msg)
        return True
    
    raise Exception("未配置邮件通道")

def verify_otp(email: str, code: str) -> bool:
    """校验邮箱验证码"""
    logger.info(f"🔍 验证邮箱验证码: {email[:3]}***@*** + {code}")
    
    if os.environ.get("DEV_SKIP_OTP") == "true" and len(code) == 6 and code.isdigit():
        logger.info(f"🧪 [DEV MODE] 验证码验证通过: {code}")
        return True
    
    try:
        db = get_supabase()
        res = db.table("email_otps").select("*").eq("email", email).single().execute()
        
        if not res.data:
            logger.warning(f"❌ 未找到该邮箱的验证码记录: {email}")
            return False
        
        record = res.data
        stored_code = record.get("code")
        expires_at_str = record.get("expires_at")
        
        if stored_code != code:
            logger.warning(f"❌ 验证码不匹配")
            return False
        
        # 时区感知时间比较
        expires_at = datetime.fromisoformat(expires_at_str)
        now = datetime.now(timezone.utc)
        
        if expires_at < now:
            logger.warning(f"❌ 验证码已过期")
            return False
        
        # 验证成功，删除记录（防重放）
        db.table("email_otps").delete().eq("email", email).execute()
        logger.info(f"✅ 验证码验证成功: {email[:3]}***@***")
        return True
        
    except Exception as e:
        logger.error(f"❌ verify_otp 异常: {type(e).__name__}: {e}")
        return False

def _is_valid_email(email: str) -> bool:
    """邮箱格式校验"""
    import re
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return re.match(pattern, email) is not None

def _send_via_brevo(to_email: str, otp: str) -> bool:
    """Brevo HTTP API 发送验证码（使用优雅的 HTML 模板）"""
    logger.info(f"🔄 使用 Brevo HTTP API 发送 → {to_email}")
    
    if not Config.BREVO_API_KEY:
        logger.error("❌ BREVO_API_KEY 未配置")
        return False

    # ✅ 检查 API Key 格式
    logger.info(f"📋 BREVO_API_KEY 前缀: {Config.BREVO_API_KEY[:10]}...")
    logger.info(f"📋 FROM_EMAIL: {Config.FROM_EMAIL}")
    logger.info(f"📋 FROM_NAME: {Config.FROM_NAME}")
    
    url = "https://api.brevo.com/v3/smtp/email"
    headers = {
        "api-key": Config.BREVO_API_KEY,
        "Content-Type": "application/json",
        "Accept": "application/json"
    }
    
    # ✅ 使用更优雅的 HTML 模板（与邮件工具风格一致）
    html_content = f"""
    <!DOCTYPE html>
    <html lang="zh-CN">
        <head>
            <meta charset="UTF-8">
        </head>
        <body style="margin: 0; padding: 0; background-color: #f4f6f8; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Arial, sans-serif;">
            <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width: 560px; margin: 30px auto; background: #ffffff; border-radius: 12px; box-shadow: 0 2px 8px rgba(0,0,0,0.08); overflow: hidden;">
                <!-- Header -->
                <tr>
                    <td style="background: linear-gradient(135deg, #1e3c72 0%, #2a5298 100%); padding: 24px 30px; text-align: center;">
                        <h1 style="color: #ffffff; margin: 0; font-size: 22px; font-weight: 600;">🔐 邮箱验证码 / Email Verification</h1>
                    </td>
                </tr>
                <!-- Content -->
                <tr>
                    <td style="padding: 28px 32px; color: #333333; line-height: 1.6;">
                        <p style="margin: 0 0 12px; font-size: 16px;">您好，</p>
                        <p style="margin: 0 0 20px; font-size: 16px;">Dear user,</p>
                        
                        <p style="margin: 0 0 16px; font-size: 15px;">您的验证码是：</p>
                        <p style="margin: 0 0 20px; font-size: 15px;">Your verification code is:</p>
                        
                        <div style="text-align: center; margin: 20px 0;">
                            <span style="display: inline-block; background: #e8f0fe; padding: 14px 28px; font-size: 32px; font-weight: bold; letter-spacing: 4px; color: #1e3c72; border-radius: 8px; font-family: monospace;">{otp}</span>
                        </div>
                        
                        <p style="margin: 0 0 12px; font-size: 14px; color: #666;">有效期：<strong>{Config.OTP_EXPIRE_MIN} 分钟</strong>，请勿泄露给他人。</p>
                        <p style="margin: 0 0 20px; font-size: 14px; color: #666;">Valid for <strong>{Config.OTP_EXPIRE_MIN} minutes</strong>. Please keep it confidential.</p>
                    </td>
                </tr>
                <!-- Footer -->
                <tr>
                    <td style="background-color: #f8f9fa; padding: 16px 32px; text-align: center; font-size: 12px; color: #999; border-top: 1px solid #eee;">
                        <p style="margin: 0;">此邮件由系统自动发送，请勿回复。</p>
                        <p style="margin: 5px 0 0;">This is an automated message, please do not reply.</p>
                    </td>
                </tr>
            </table>
        </body>
    </html>
        """
    
    payload = {
        "sender": {"name": Config.FROM_NAME, "email": Config.FROM_EMAIL},
        "to": [{"email": to_email}],
        "subject": "🔐 邮箱验证码 / Email Verification Code - 在线考试系统",
        "htmlContent": html_content
    }

    # ✅ 打印请求数据（不包含敏感信息）
    logger.info(f"📤 请求数据: sender={Config.FROM_EMAIL}, to={to_email}")
    
    try:
        logger.debug(f"📤 POST {url}")
        with httpx.Client(timeout=30.0) as client:
            response = client.post(url, headers=headers, json=payload)

        # ✅ 打印完整响应
        logger.info(f"📥 响应状态码: {response.status_code}")
        logger.info(f"📥 响应内容: {response.text}")
        
        if response.status_code in [200, 201]:
            logger.info(f"✅ Brevo 发送成功 → {to_email} | 状态码: {response.status_code}")
            return True
        else:
            # ✅ 解析错误详情
            try:
                error_data = response.json()
                logger.error(f"❌ Brevo 错误详情: {error_data}")
            except:
                logger.error(f"❌ Brevo 原始错误: {response.text}")
            return False
    except Exception as e:
        logger.error(f"❌ Brevo 发送异常: {type(e).__name__}: {e}")
        return False

def _send_via_smtp(to_email: str, otp: str) -> bool:
    """SMTP 备选通道（验证码使用简单文本，但保持统一风格）"""
    logger.info(f"🔄 使用 SMTP 发送 → {to_email}")
    
    if not all([Config.SMTP_SERVER, Config.SMTP_EMAIL, Config.SMTP_AUTH_CODE]):
        logger.error("❌ SMTP 配置不完整")
        return False
    
    # SMTP 降级使用纯文本（简洁但信息完整）
    body = f"""
========================================
        邮箱验证码 / Email Verification
========================================

您的验证码是：{otp}
Your verification code is: {otp}

有效期：{Config.OTP_EXPIRE_MIN} 分钟
Valid for: {Config.OTP_EXPIRE_MIN} minutes

请勿泄露给他人。
Please keep it confidential.

========================================
此邮件由系统自动发送，请勿回复。
This is an automated message, please do not reply.
"""
    
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = "🔐 验证码 / Verification Code - 在线考试系统"
    msg["From"] = f"{Config.FROM_NAME} <{Config.SMTP_EMAIL}>"
    msg["To"] = to_email
    
    try:
        with smtplib.SMTP_SSL(Config.SMTP_SERVER, Config.SMTP_PORT) as srv:
            srv.login(Config.SMTP_EMAIL, Config.SMTP_AUTH_CODE)
            srv.sendmail(Config.SMTP_EMAIL, [to_email], msg.as_string())
        logger.info(f"✅ SMTP 发送成功 → {to_email}")
        return True
    except Exception as e:
        logger.error(f"❌ SMTP 发送失败: {type(e).__name__}: {e}")
        return False

def hash_password(pwd: str) -> str:
    """密码哈希加密（argon2，无长度限制）"""
    pwd = pwd.strip()
    return ph.hash(pwd)

def check_password(pwd: str, hashed: str) -> bool:
    """密码校验（argon2）"""
    pwd = pwd.strip()
    try:
        ph.verify(hashed, pwd)
        return True
    except VerifyMismatchError:
        return False


