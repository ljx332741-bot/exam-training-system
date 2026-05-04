# services/auth.py - SHA256+bcrypt 终极修复版
import smtplib, random, string, logging, os, json, httpx, hashlib  # 🔁 添加 hashlib
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

def send_email(to, subject, body):
    """发送邮件（复用现有 OTP 发送逻辑或单独实现）"""
    # 优先使用 Brevo API
    if Config.EMAIL_PROVIDER == 'brevo' and Config.BREVO_API_KEY:
        import requests
        url = "https://api.brevo.com/v3/smtp/email"
        headers = {
            "api-key": Config.BREVO_API_KEY,
            "Content-Type": "application/json"
        }
        payload = {
            "sender": {"email": Config.FROM_EMAIL, "name": Config.FROM_NAME},
            "to": [{"email": to}],
            "subject": subject,
            "htmlContent": body.replace("\n", "<br>")
        }
        response = requests.post(url, json=payload, headers=headers)
        if response.status_code != 201:
            raise Exception(f"Brevo send failed: {response.text}")
        return True
    
    # 降级使用 SMTP
    if Config.SMTP_EMAIL and Config.SMTP_AUTH_CODE:
        msg = MIMEMultipart()
        msg['From'] = Config.FROM_EMAIL
        msg['To'] = to
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'plain', 'utf-8'))
        
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
    """Brevo HTTP API 发送"""
    logger.info(f"🔄 使用 Brevo HTTP API 发送 → {to_email}")
    
    if not Config.BREVO_API_KEY:
        logger.error("❌ BREVO_API_KEY 未配置")
        return False
    
    url = "https://api.brevo.com/v3/smtp/email"
    headers = {
        "api-key": Config.BREVO_API_KEY,
        "Content-Type": "application/json",
        "Accept": "application/json"
    }
    
    payload = {
        "sender": {"name": Config.FROM_NAME, "email": Config.FROM_EMAIL},
        "to": [{"email": to_email}],
        "subject": "🔐 您的验证码 - 在线考试系统",
        "htmlContent": f"""
        <div style="font-family:sans-serif;max-width:600px;margin:0 auto;padding:20px">
            <h2 style="color:#0d6efd;border-bottom:2px solid #0d6efd;padding-bottom:10px">🔐 验证码</h2>
            <p>您好，</p>
            <p>您的验证码是：<strong style="font-size:1.8em;color:#198754;background:#d1e7dd;padding:5px 15px;border-radius:4px;display:inline-block">{otp}</strong></p>
            <p>有效期：<strong>{Config.OTP_EXPIRE_MIN} 分钟</strong>，请勿泄露给他人。</p>
            <hr style="margin:20px 0;border:0;border-top:1px solid #eee">
            <small style="color:#6c757d">此邮件由系统自动发送，请勿回复。</small>
        </div>
        """
    }
    
    try:
        logger.debug(f"📤 POST {url}")
        with httpx.Client(timeout=30.0) as client:
            response = client.post(url, headers=headers, json=payload)
        
        if response.status_code in [200, 201]:
            logger.info(f"✅ Brevo 发送成功 → {to_email} | 状态码: {response.status_code}")
            return True
        else:
            logger.error(f"❌ Brevo API 返回错误: {response.status_code} | {response.text}")
            if response.status_code == 401:
                logger.error("💡 提示: API Key 无效")
            elif response.status_code == 403:
                logger.error(f"💡 提示: 发件邮箱 '{Config.FROM_EMAIL}' 未验证")
            return False
    except Exception as e:
        logger.error(f"❌ Brevo 发送异常: {type(e).__name__}: {e}")
        return False

def _send_via_smtp(to_email: str, otp: str) -> bool:
    """SMTP 备选通道"""
    logger.info(f"🔄 使用 SMTP 发送 → {to_email}")
    
    if not all([Config.SMTP_SERVER, Config.SMTP_EMAIL, Config.SMTP_AUTH_CODE]):
        logger.error("❌ SMTP 配置不完整")
        return False
    
    msg = MIMEText(f"您的验证码是：{otp}，{Config.OTP_EXPIRE_MIN}分钟内有效。", "plain", "utf-8")
    msg["Subject"] = "🔐 在线考试系统验证码"
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


