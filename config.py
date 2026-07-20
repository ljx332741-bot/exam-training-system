# config.py
import os
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv, set_key

BASE_DIR = Path(__file__).resolve().parent
ENV_FILE = BASE_DIR / ".env"
load_dotenv(ENV_FILE, override=True)

class Config:
    # 🔑 Flask
    SECRET_KEY = os.environ.get("FLASK_SECRET_KEY", "dev-change-this-now")
    
    # ============================================================
    # 应用版本号（启动时生成一次，零运行时开销）
    # ============================================================
    # 优先使用环境变量，否则使用启动时间
    APP_VERSION = os.environ.get('APP_VERSION') or datetime.now().strftime('%Y%m%d%H%M%S')
    
    # ============================================================
    # 环境识别
    # ============================================================
    @staticmethod
    def get_env():
        """获取当前运行环境"""
        app_env = os.environ.get('APP_ENV')
        if app_env:
            return app_env.lower()
        if os.environ.get('RENDER') == 'true':
            return 'production'
        flask_env = os.environ.get('FLASK_ENV')
        if flask_env:
            return flask_env.lower()
        return 'development'
    
    @classmethod
    def is_production(cls):
        return cls.get_env() in ['production', 'prod']
    
    @classmethod
    def is_testing(cls):
        return cls.get_env() in ['testing', 'test']
    
    @classmethod
    def is_development(cls):
        return cls.get_env() in ['development', 'dev']
    
    # ============================================================
    # ✅ 改为方法（避免类初始化顺序问题）
    # ============================================================
    @classmethod
    def get_env_name(cls):
        return cls.get_env().upper()
    
    @classmethod
    def is_production_env(cls):
        return cls.is_production()
    
    @classmethod
    def is_testing_env(cls):
        return cls.is_testing()
    
    @classmethod
    def is_development_env(cls):
        return cls.is_development()
    
    # ============================================================
    # Supabase 配置
    # ============================================================
    SUPABASE_URL = os.environ.get("SUPABASE_URL")
    SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
    SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", SUPABASE_KEY)
    
    # ============================================================
    # 邮件服务配置
    # ============================================================
    EMAIL_PROVIDER = os.environ.get("EMAIL_PROVIDER", "brevo")
    BREVO_API_KEY = os.environ.get("BREVO_API_KEY")
    SENDGRID_API_KEY = os.environ.get("SENDGRID_API_KEY")
    FROM_EMAIL = os.environ.get("FROM_EMAIL", "noreply@your-exam.com")
    FROM_NAME = os.environ.get("FROM_NAME", "在线考试系统")
    
    SMTP_SERVER = os.environ.get("SMTP_SERVER", "smtp.gmail.com")
    SMTP_PORT = int(os.environ.get("SMTP_PORT", 465))
    SMTP_EMAIL = os.environ.get("SMTP_EMAIL")
    SMTP_AUTH_CODE = os.environ.get("SMTP_AUTH_CODE")
    
    OTP_EXPIRE_MIN = int(os.environ.get("OTP_EXPIRE_MIN", 5))
    DEFAULT_EXAM_DURATION = int(os.environ.get("DEFAULT_EXAM_DURATION", 60))
    
    # ============================================================
    # Cloudflare R2 配置
    # ============================================================
    CLOUDFLARE_R2_ACCESS_KEY_ID = os.environ.get('CLOUDFLARE_R2_ACCESS_KEY_ID')
    CLOUDFLARE_R2_SECRET_ACCESS_KEY = os.environ.get('CLOUDFLARE_R2_SECRET_ACCESS_KEY')
    CLOUDFLARE_R2_ENDPOINT = os.environ.get('CLOUDFLARE_R2_ENDPOINT')
    CLOUDFLARE_R2_REGION = os.environ.get('CLOUDFLARE_R2_REGION', 'auto')
    
    @classmethod
    def get_r2_bucket(cls):
        """根据环境自动选择 R2 存储桶"""
        env = cls.get_env()
        
        # 生产环境：使用 PROD_R2_BUCKET
        if env in ['production', 'prod']:
            prod_bucket = os.environ.get('PROD_R2_BUCKET')
            if prod_bucket:
                return prod_bucket
            return os.environ.get('CLOUDFLARE_R2_BUCKET', 'training-photos-prod')
        
        # 测试环境和本地开发：使用默认
        return os.environ.get('CLOUDFLARE_R2_BUCKET', 'training-photos')
    
    @classmethod
    def get_r2_public_url(cls):
        """根据环境自动选择 R2 公共 URL"""
        env = cls.get_env()
        
        # 生产环境：使用 PROD_R2_PUBLIC_URL
        if env in ['production', 'prod']:
            prod_url = os.environ.get('PROD_R2_PUBLIC_URL')
            if prod_url:
                return prod_url
            return os.environ.get('CLOUDFLARE_R2_PUBLIC_URL')
        
        # 测试环境和本地开发：使用默认
        return os.environ.get('CLOUDFLARE_R2_PUBLIC_URL')
    
    # 兼容原有属性访问（使用类方法）
    @classmethod
    def get_r2_config(cls):
        return {
            'CLOUDFLARE_R2_ACCESS_KEY_ID': cls.CLOUDFLARE_R2_ACCESS_KEY_ID,
            'CLOUDFLARE_R2_SECRET_ACCESS_KEY': cls.CLOUDFLARE_R2_SECRET_ACCESS_KEY,
            'CLOUDFLARE_R2_ENDPOINT': cls.CLOUDFLARE_R2_ENDPOINT,
            'CLOUDFLARE_R2_BUCKET': cls.get_r2_bucket(),
            'CLOUDFLARE_R2_PUBLIC_URL': cls.get_r2_public_url(),
            'CLOUDFLARE_R2_REGION': cls.CLOUDFLARE_R2_REGION,
        }
    
    @classmethod
    def update_env(cls, key, value):
        set_key(str(ENV_FILE), key, str(value))
    
    @classmethod
    def check(cls):
        """检查必要的配置是否存在"""
        provider = cls.EMAIL_PROVIDER
        if provider == "brevo" and not cls.BREVO_API_KEY:
            raise RuntimeError("❌ EMAIL_PROVIDER=brevo 但未配置 BREVO_API_KEY")
        if provider == "sendgrid" and not cls.SENDGRID_API_KEY:
            raise RuntimeError("❌ EMAIL_PROVIDER=sendgrid 但未配置 SENDGRID_API_KEY")
        if provider == "smtp" and not all([cls.SMTP_EMAIL, cls.SMTP_AUTH_CODE]):
            raise RuntimeError("❌ EMAIL_PROVIDER=smtp 但未配置 SMTP 信息")
    
    @classmethod
    def print_env_info(cls):
        """打印当前环境信息"""
        env = cls.get_env().upper()
        print("=" * 60)
        print(f"🔧 当前环境: {env}")
        print(f"   IS_PRODUCTION: {cls.is_production()}")
        print(f"   IS_TESTING: {cls.is_testing()}")
        print(f"   IS_DEVELOPMENT: {cls.is_development()}")
        print(f"   SUPABASE_URL: {cls.SUPABASE_URL[:35] + '...' if cls.SUPABASE_URL else '未配置'}")
        print(f"   R2_BUCKET: {cls.get_r2_bucket()}")
        print(f"   R2_PUBLIC_URL: {cls.get_r2_public_url()}")
        print("=" * 60)

