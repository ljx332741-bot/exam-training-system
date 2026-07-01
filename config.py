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
    
    # ============================================================
    # 环境识别（适配你的架构）
    # ============================================================
    @staticmethod
    def get_env():
        """
        获取当前运行环境
        优先级: APP_ENV > RENDER_ENV > FLASK_ENV > 自动检测
        
        本地开发: APP_ENV=development 或默认
        测试环境: Render Dashboard 设置 APP_ENV=testing
        生产环境: Render Dashboard 设置 APP_ENV=production
        """
        # 1. 优先使用 APP_ENV（你在 Render Dashboard 设置）
        app_env = os.environ.get('APP_ENV')
        if app_env:
            return app_env.lower()
        
        # 2. Render 专用环境变量（Render 自动设置）
        render_env = os.environ.get('RENDER_ENV')
        if render_env:
            return render_env.lower()
        
        # 3. Flask 标准环境变量
        flask_env = os.environ.get('FLASK_ENV')
        if flask_env:
            return flask_env.lower()
        
        # 4. 自动检测 Render 平台
        if os.environ.get('RENDER') == 'true':
            # Render 上默认生产环境
            return 'production'
        
        # 5. 本地开发默认
        return 'development'
    
    @staticmethod
    def is_production():
        return Config.get_env() in ['production', 'prod']
    
    @staticmethod
    def is_testing():
        return Config.get_env() in ['testing', 'test']
    
    @staticmethod
    def is_development():
        return Config.get_env() in ['development', 'dev']
    
    # ============================================================
    # 环境属性（供 app.py 使用）
    # ============================================================
    ENV = get_env.__func__()
    IS_PRODUCTION = is_production.__func__()
    IS_TESTING = is_testing.__func__()
    IS_DEVELOPMENT = is_development.__func__()
    
    # ============================================================
    # Supabase 配置
    # ============================================================
    @staticmethod
    def _get_supabase_url():
        """根据环境获取 Supabase URL"""
        env = Config.get_env()
        # 测试环境：使用 TEST_ 前缀的环境变量
        if env in ['testing', 'test'] and os.environ.get('TEST_SUPABASE_URL'):
            return os.environ.get('TEST_SUPABASE_URL')
        # 生产环境：使用 PROD_ 前缀的环境变量
        if env in ['production', 'prod'] and os.environ.get('PROD_SUPABASE_URL'):
            return os.environ.get('PROD_SUPABASE_URL')
        # 默认（本地开发）
        return os.environ.get('SUPABASE_URL')
    
    @staticmethod
    def _get_supabase_key():
        """根据环境获取 Supabase Key"""
        env = Config.get_env()
        if env in ['testing', 'test'] and os.environ.get('TEST_SUPABASE_KEY'):
            return os.environ.get('TEST_SUPABASE_KEY')
        if env in ['production', 'prod'] and os.environ.get('PROD_SUPABASE_KEY'):
            return os.environ.get('PROD_SUPABASE_KEY')
        return os.environ.get('SUPABASE_KEY')
    
    # 使用 property 让 app.config.from_object 能正确读取
    @property
    def SUPABASE_URL(self):
        return self._get_supabase_url()
    
    @property
    def SUPABASE_KEY(self):
        return self._get_supabase_key()
    
    # 为了兼容直接类属性访问，使用 classmethod
    @classmethod
    def get_supabase_url(cls):
        return cls._get_supabase_url()
    
    @classmethod
    def get_supabase_key(cls):
        return cls._get_supabase_key()
    
    # 服务密钥（直接用环境变量）
    SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY")
    
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
    
    # 业务参数
    OTP_EXPIRE_MIN = int(os.environ.get("OTP_EXPIRE_MIN", 5))
    DEFAULT_EXAM_DURATION = int(os.environ.get("DEFAULT_EXAM_DURATION", 60))
    
    # ============================================================
    # Cloudflare R2 配置
    # ============================================================
    @staticmethod
    def _get_r2_public_url():
        """根据环境获取 R2 公共 URL"""
        env = Config.get_env()
        # 测试环境
        if env in ['testing', 'test'] and os.environ.get('TEST_R2_PUBLIC_URL'):
            return os.environ.get('TEST_R2_PUBLIC_URL')
        # 生产环境
        if env in ['production', 'prod'] and os.environ.get('PROD_R2_PUBLIC_URL'):
            return os.environ.get('PROD_R2_PUBLIC_URL')
        # 开发环境（本地）
        if env in ['development', 'dev'] and os.environ.get('DEV_R2_PUBLIC_URL'):
            return os.environ.get('DEV_R2_PUBLIC_URL')
        # 默认
        return os.environ.get('CLOUDFLARE_R2_PUBLIC_URL')
    
    # 基础 R2 配置（所有环境共用）
    CLOUDFLARE_R2_ACCESS_KEY_ID = os.environ.get('CLOUDFLARE_R2_ACCESS_KEY_ID')
    CLOUDFLARE_R2_SECRET_ACCESS_KEY = os.environ.get('CLOUDFLARE_R2_SECRET_ACCESS_KEY')
    CLOUDFLARE_R2_ENDPOINT = os.environ.get('CLOUDFLARE_R2_ENDPOINT')
    CLOUDFLARE_R2_BUCKET = os.environ.get('CLOUDFLARE_R2_BUCKET')
    CLOUDFLARE_R2_REGION = os.environ.get('CLOUDFLARE_R2_REGION', 'auto')
    
    @property
    def CLOUDFLARE_R2_PUBLIC_URL(self):
        return self._get_r2_public_url()
    
    @classmethod
    def get_r2_public_url(cls):
        return cls._get_r2_public_url()
    
    # ============================================================
    # 兼容 app.config.from_object
    # ============================================================
    @classmethod
    def get_config_dict(cls):
        """获取所有配置（供 from_object 使用）"""
        return {
            'SECRET_KEY': cls.SECRET_KEY,
            'SUPABASE_URL': cls.get_supabase_url(),
            'SUPABASE_KEY': cls.get_supabase_key(),
            'SUPABASE_SERVICE_KEY': cls.SUPABASE_SERVICE_KEY,
            'EMAIL_PROVIDER': cls.EMAIL_PROVIDER,
            'BREVO_API_KEY': cls.BREVO_API_KEY,
            'SENDGRID_API_KEY': cls.SENDGRID_API_KEY,
            'FROM_EMAIL': cls.FROM_EMAIL,
            'FROM_NAME': cls.FROM_NAME,
            'SMTP_SERVER': cls.SMTP_SERVER,
            'SMTP_PORT': cls.SMTP_PORT,
            'SMTP_EMAIL': cls.SMTP_EMAIL,
            'SMTP_AUTH_CODE': cls.SMTP_AUTH_CODE,
            'OTP_EXPIRE_MIN': cls.OTP_EXPIRE_MIN,
            'DEFAULT_EXAM_DURATION': cls.DEFAULT_EXAM_DURATION,
            'CLOUDFLARE_R2_ACCESS_KEY_ID': cls.CLOUDFLARE_R2_ACCESS_KEY_ID,
            'CLOUDFLARE_R2_SECRET_ACCESS_KEY': cls.CLOUDFLARE_R2_SECRET_ACCESS_KEY,
            'CLOUDFLARE_R2_ENDPOINT': cls.CLOUDFLARE_R2_ENDPOINT,
            'CLOUDFLARE_R2_BUCKET': cls.CLOUDFLARE_R2_BUCKET,
            'CLOUDFLARE_R2_REGION': cls.CLOUDFLARE_R2_REGION,
            'CLOUDFLARE_R2_PUBLIC_URL': cls.get_r2_public_url(),
            'ENV': cls.ENV,
            'IS_PRODUCTION': cls.IS_PRODUCTION,
            'IS_TESTING': cls.IS_TESTING,
            'IS_DEVELOPMENT': cls.IS_DEVELOPMENT,
        }
    
    @classmethod
    def update_env(cls, key, value):
        set_key(str(ENV_FILE), key, str(value))
    
    @classmethod
    def check(cls):
        """检查必要的配置是否存在"""
        # 邮件配置检查（保持原有）
        provider = cls.EMAIL_PROVIDER
        if provider == "brevo" and not cls.BREVO_API_KEY:
            raise RuntimeError("❌ EMAIL_PROVIDER=brevo 但未配置 BREVO_API_KEY")
        if provider == "sendgrid" and not cls.SENDGRID_API_KEY:
            raise RuntimeError("❌ EMAIL_PROVIDER=sendgrid 但未配置 SENDGRID_API_KEY")
        if provider == "smtp" and not all([cls.SMTP_EMAIL, cls.SMTP_AUTH_CODE]):
            raise RuntimeError("❌ EMAIL_PROVIDER=smtp 但未配置 SMTP 信息")
    
    @classmethod
    def print_env_info(cls):
        """打印当前环境信息（启动时显示）"""
        print("=" * 60)
        print(f"🔧 环境: {cls.ENV.upper()}")
        print(f"   IS_PRODUCTION: {cls.IS_PRODUCTION}")
        print(f"   IS_TESTING: {cls.IS_TESTING}")
        print(f"   IS_DEVELOPMENT: {cls.IS_DEVELOPMENT}")
        print(f"   SUPABASE_URL: {cls.get_supabase_url()[:35] + '...' if cls.get_supabase_url() else '未配置'}")
        print(f"   R2_BUCKET: {cls.CLOUDFLARE_R2_BUCKET}")
        print(f"   R2_PUBLIC_URL: {cls.get_r2_public_url()}")
        print("=" * 60)


# 为了保持原有兼容，直接导出 Config