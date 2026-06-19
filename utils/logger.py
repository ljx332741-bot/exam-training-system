# utils/logger.py
import os
import sys
import logging
import logging.handlers
import re
import glob
from datetime import datetime, timedelta


# ============================================================
# 1. 敏感数据脱敏过滤器（保留您的原有逻辑）
# ============================================================
class SensitiveDataFilter(logging.Filter):
    """过滤日志中的敏感信息"""
    
    # 敏感信息模式
    PATTERNS = {
        'uuid': re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', re.I),
        'email': re.compile(r'\b[\w\.-]+@[\w\.-]+\.\w+\b'),
        'user_id_eq': re.compile(r'id=eq\.[0-9a-f-]+', re.I),
        'email_eq': re.compile(r'email=eq\.[^&]+', re.I),
        'supabase_ref': re.compile(r'mrkukgnkrefhruoxuflz|hhupzorgxzwoxrrqaqjq', re.I),
        'api_key': re.compile(r'eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+', re.I),
        # 额外增加一些常见敏感字段
        'password': re.compile(r'"password":\s*"[^"]*"', re.I),
        'token': re.compile(r'"token":\s*"[^"]*"', re.I),
        'secret': re.compile(r'"secret":\s*"[^"]*"', re.I),
    }
    
    REPLACEMENTS = {
        'uuid': '[UUID]',
        'email': '[EMAIL]',
        'user_id_eq': 'id=eq.[ID]',
        'email_eq': 'email=eq.[EMAIL]',
        'supabase_ref': '[PROJECT]',
        'api_key': '[API_KEY]',
        'password': '"password":"[REDACTED]"',
        'token': '"token":"[REDACTED]"',
        'secret': '"secret":"[REDACTED]"',
    }
    
    def filter(self, record):
        if hasattr(record, 'msg') and record.msg:
            msg = str(record.msg)
            for pattern_name, pattern in self.PATTERNS.items():
                try:
                    msg = pattern.sub(self.REPLACEMENTS.get(pattern_name, '[REDACTED]'), msg)
                except:
                    pass
            record.msg = msg
        return True


# ============================================================
# 2. 带自动清理功能的日志轮转处理器
# ============================================================
class TimedRotatingFileHandlerWithCleanup(logging.handlers.TimedRotatingFileHandler):
    """
    带自动清理功能的日志轮转处理器
    - 每天轮转一次
    - 自动删除超过指定天数的旧日志文件
    """
    
    def __init__(self, filename, when='midnight', interval=1, backup_count=2, 
                 encoding=None, delay=False, utc=False, at_time=None):
        """
        Args:
            filename: 日志文件路径
            when: 轮转周期 ('midnight'=每天午夜, 'H'=每小时, 'D'=每天)
            interval: 轮转间隔
            backup_count: 保留的日志文件数量（对应天数）
            encoding: 文件编码
        """
        super().__init__(filename, when, interval, backup_count, encoding, delay, utc, at_time)
        self.backup_count = backup_count
        self.log_dir = os.path.dirname(filename)
        self.log_basename = os.path.basename(filename)
    
    def doRollover(self):
        """轮转日志并清理旧文件"""
        # 执行父类的轮转逻辑
        super().doRollover()
        
        # 清理旧的日志文件
        self._cleanup_old_logs()
    
    def _cleanup_old_logs(self):
        """删除超过指定天数的日志文件"""
        if not self.log_dir:
            return
        
        # 获取所有相关日志文件
        pattern = os.path.join(self.log_dir, f"{self.log_basename}*")
        log_files = glob.glob(pattern)
        
        # 计算截止时间
        cutoff_time = datetime.now() - timedelta(days=self.backup_count)
        cutoff_timestamp = cutoff_time.timestamp()
        
        deleted_count = 0
        for file_path in log_files:
            # 跳过当前正在写入的日志文件
            if file_path == self.baseFilename:
                continue
            
            try:
                # 获取文件修改时间
                file_mtime = os.path.getmtime(file_path)
                
                # 如果文件修改时间早于截止时间，删除它
                if file_mtime < cutoff_timestamp:
                    os.remove(file_path)
                    deleted_count += 1
                    print(f"[LOG] 已删除旧日志: {os.path.basename(file_path)}")
            except (OSError, FileNotFoundError):
                # 文件可能已被删除或权限问题
                pass
        
        if deleted_count > 0:
            print(f"[LOG] 共删除 {deleted_count} 个旧日志文件")


# ============================================================
# 3. 兼容原有配置的统一日志设置函数
# ============================================================
def setup_logging(app=None, log_level=None, log_dir='logs', keep_days=2):
    """
    配置日志系统（支持自动清理和脱敏）
    
    Args:
        app: Flask app 实例（可选）
        log_level: 日志级别，None表示自动判断
        log_dir: 日志目录
        keep_days: 保留日志天数（默认2天）
    
    Returns:
        logger: 应用日志器
    """
    # 判断环境（保持与原有逻辑一致）
    is_production = os.environ.get('FLASK_ENV') == 'production' or \
                    os.environ.get('RENDER', '') == 'true'
    
    # 确定日志级别
    if log_level is None:
        if is_production:
            base_level = logging.WARNING
        else:
            base_level = logging.DEBUG
    else:
        base_level = log_level
    
    # 确保日志目录存在
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)
    
    # 日志文件路径
    log_file = os.path.join(log_dir, 'exam_app.log')
    
    # 清除已有的处理器（避免重复）
    root_logger = logging.getLogger()
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    
    # ========== 创建处理器 ==========
    
    # 1. 控制台处理器（所有环境都有）
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(base_level)
    console_formatter = logging.Formatter(
        '[%(asctime)s] %(levelname)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    console_handler.setFormatter(console_formatter)
    console_handler.addFilter(SensitiveDataFilter())
    root_logger.addHandler(console_handler)
    
    # 2. 文件处理器（带自动清理和脱敏）
    # 生产环境：只记录 WARNING 及以上
    # 开发环境：记录 DEBUG 及以上
    file_level = logging.WARNING if is_production else logging.DEBUG
    
    # 使用自定义的轮转处理器（每天轮转，保留指定天数）
    file_handler = TimedRotatingFileHandlerWithCleanup(
        filename=log_file,
        when='midnight',
        interval=1,
        backup_count=keep_days,
        encoding='utf-8'
    )
    file_handler.setLevel(file_level)
    file_formatter = logging.Formatter(
        '[%(asctime)s] %(levelname)s [%(name)s:%(lineno)d] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    file_handler.setFormatter(file_formatter)
    file_handler.addFilter(SensitiveDataFilter())
    root_logger.addHandler(file_handler)
    
    # ========== 配置第三方库日志级别 ==========
    # 生产环境降低第三方库日志级别
    if is_production:
        logging.getLogger('supabase').setLevel(logging.ERROR)
        logging.getLogger('httpx').setLevel(logging.WARNING)
        logging.getLogger('httpcore').setLevel(logging.WARNING)
        logging.getLogger('urllib3').setLevel(logging.WARNING)
        logging.getLogger('werkzeug').setLevel(logging.WARNING)
    
    # ========== 获取应用日志器 ==========
    app_logger = logging.getLogger(__name__)
    
    # ========== 启动日志 ==========
    if is_production:
        app_logger.warning("=" * 60)
        app_logger.warning("PRODUCTION MODE - Sensitive data will be redacted")
        app_logger.warning(f"Log level: {logging.getLevelName(base_level)}")
        app_logger.warning(f"Log file: {log_file}")
        app_logger.warning(f"Logs will be kept for {keep_days} days")
        app_logger.warning("=" * 60)
    else:
        app_logger.info("Development mode - Log level: DEBUG")
        app_logger.info(f"Log file: {log_file}")
        app_logger.info(f"Logs will be kept for {keep_days} days")
    
    # ========== Flask App 集成 ==========
    if app:
        app.logger = app_logger
        
        # 将 Flask 的日志也重定向到我们的处理器
        werkzeug_logger = logging.getLogger('werkzeug')
        werkzeug_logger.handlers = root_logger.handlers
        werkzeug_logger.setLevel(base_level)
    
    return app_logger


# ============================================================
# 4. 兼容原有接口的快捷函数
# ============================================================
def setup_production_logging():
    """
    兼容原有代码的函数
    返回: is_production (布尔值)
    """
    # 判断环境
    is_production = os.environ.get('FLASK_ENV') == 'production' or \
                    os.environ.get('RENDER', '') == 'true'
    
    # 配置日志
    setup_logging(
        app=None,
        log_dir='logs',
        keep_days=2
    )
    
    return is_production


def get_logger(name):
    """获取日志器"""
    return logging.getLogger(name)


def clean_old_logs(log_dir='logs', keep_days=2):
    """
    手动清理旧的日志文件
    
    Args:
        log_dir: 日志目录
        keep_days: 保留天数
    """
    if not os.path.exists(log_dir):
        return
    
    cutoff_time = datetime.now() - timedelta(days=keep_days)
    cutoff_timestamp = cutoff_time.timestamp()
    
    # 查找所有日志文件
    pattern = os.path.join(log_dir, '*.log*')
    log_files = glob.glob(pattern)
    
    deleted_count = 0
    for file_path in log_files:
        try:
            file_mtime = os.path.getmtime(file_path)
            if file_mtime < cutoff_timestamp:
                os.remove(file_path)
                deleted_count += 1
                print(f"[LOG] 已清理: {os.path.basename(file_path)}")
        except (OSError, FileNotFoundError):
            pass
    
    if deleted_count > 0:
        print(f"[LOG] 共清理 {deleted_count} 个旧日志文件")
    else:
        print(f"[LOG] 没有需要清理的日志文件（保留最近 {keep_days} 天）")


# ============================================================
# 5. 自动执行（兼容原 app.py 的调用方式）
# ============================================================
# 当直接导入时，自动执行日志配置
# 注意：为了避免在 app.py 中重复配置，这里使用延迟初始化
_is_production = None
_logger = None


def init_default_logging():
    """初始化默认日志配置（供 app.py 调用）"""
    global _is_production, _logger
    
    _is_production = os.environ.get('FLASK_ENV') == 'production' or \
                     os.environ.get('RENDER', '') == 'true'
    
    _logger = setup_logging(
        app=None,
        log_dir='logs',
        keep_days=2
    )
    
    return _is_production


# 为了兼容旧的 app.py，保留 IS_PRODUCTION 和 logger 的导出方式
# 但建议在 app.py 中显式调用 setup_logging()
IS_PRODUCTION = None
logger = None