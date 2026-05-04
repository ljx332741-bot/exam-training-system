# config.py
import os
from pathlib import Path
from dotenv import load_dotenv, set_key

BASE_DIR = Path(__file__).resolve().parent
ENV_FILE = BASE_DIR / ".env"
load_dotenv(ENV_FILE, override=True)

class Config:
    # 🔑 Flask
    SECRET_KEY = os.environ.get("FLASK_SECRET_KEY", "dev-change-this-now")
    
    # 🗄️ Supabase
    SUPABASE_URL = os.environ.get("SUPABASE_URL")
    SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
    
    # 📧 邮件服务配置（核心！）
    EMAIL_PROVIDER = os.environ.get("EMAIL_PROVIDER", "brevo")  # "brevo" or "smtp"
    
    # Brevo 配置
    BREVO_API_KEY = os.environ.get("BREVO_API_KEY")

    # SendGrid 配置（生产推荐）
    SENDGRID_API_KEY = os.environ.get("SENDGRID_API_KEY")
    FROM_EMAIL = os.environ.get("FROM_EMAIL", "noreply@your-exam.com")
    FROM_NAME = os.environ.get("FROM_NAME", "在线考试系统")
    
    # SMTP 配置（开发备选）
    SMTP_SERVER = os.environ.get("SMTP_SERVER", "smtp.gmail.com")
    SMTP_PORT = int(os.environ.get("SMTP_PORT", 465))
    SMTP_EMAIL = os.environ.get("SMTP_EMAIL")
    SMTP_AUTH_CODE = os.environ.get("SMTP_AUTH_CODE")
    
    # ⚙️ 业务参数
    OTP_EXPIRE_MIN = int(os.environ.get("OTP_EXPIRE_MIN", 5))
    
    # 新增：默认考试时长（分钟）
    DEFAULT_EXAM_DURATION = int(os.environ.get("DEFAULT_EXAM_DURATION", 60))

    @classmethod
    def update_env(cls, key, value):
        """更新 .env 文件中的键值"""
        set_key(str(ENV_FILE), key, str(value))

    @classmethod
    def check(cls):
        """启动校验：至少配置一种邮件通道"""
        provider = cls.EMAIL_PROVIDER
        if provider == "brevo" and not cls.BREVO_API_KEY:
            raise RuntimeError("❌ EMAIL_PROVIDER=brevo 但未配置 BREVO_API_KEY")
        if provider == "sendgrid" and not cls.SENDGRID_API_KEY:
            raise RuntimeError("❌ EMAIL_PROVIDER=sendgrid 但未配置 SENDGRID_API_KEY")
        if provider == "smtp" and not all([cls.SMTP_EMAIL, cls.SMTP_AUTH_CODE]):
            raise RuntimeError("❌ EMAIL_PROVIDER=smtp 但未配置 SMTP 信息")