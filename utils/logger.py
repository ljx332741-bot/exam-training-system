# utils/logger.py - 修改备份文件命名格式

import os
import time
import sys
import logging
import logging.handlers
import re
import glob
import json
from datetime import datetime, timedelta
import atexit
import signal


# ============================================================
# 1. 敏感数据脱敏过滤器
# ============================================================
class SensitiveDataFilter(logging.Filter):
    """过滤日志中的敏感信息"""
    
    PATTERNS = {
        'uuid': re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', re.I),
        'email': re.compile(r'\b[\w\.-]+@[\w\.-]+\.\w+\b'),
        'user_id_eq': re.compile(r'id=eq\.[0-9a-f-]+', re.I),
        'email_eq': re.compile(r'email=eq\.[^&]+', re.I),
        'supabase_ref': re.compile(r'mrkukgnkrefhruoxuflz|hhupzorgxzwoxrrqaqjq', re.I),
        'api_key': re.compile(r'eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+', re.I),
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
# 2. 自定义日志轮转处理器（修改备份文件命名格式）
# ============================================================

class CustomTimedRotatingFileHandler(logging.handlers.TimedRotatingFileHandler):
    """
    自定义日志轮转处理器
    - 备份文件命名格式：exam_app.2026-08-20.log（而不是 exam_app.log.2026-08-20）
    - 这样双击文件可以直接用记事本打开
    """
    
    def __init__(self, filename, when='midnight', interval=1, backup_count=7,
                 encoding=None, delay=False, utc=False, at_time=None):
        # 保存原始文件名
        self.base_filename = filename
        self.log_dir = os.path.dirname(filename)
        self.log_basename = os.path.basename(filename)
        # 去掉 .log 后缀，用于生成备份文件名
        self.log_name_without_ext = os.path.splitext(self.log_basename)[0]
        
        # 调用父类初始化
        super().__init__(filename, when, interval, backup_count, encoding, delay, utc, at_time)
        
        # 重写后缀格式
        self.suffix = "%Y-%m-%d"
        self.extMatch = re.compile(r"^\d{4}-\d{2}-\d{2}$")
        
        # 自定义清理间隔
        self._last_cleanup_time = datetime.now()
        self._cleanup_interval = timedelta(hours=1)
        
        # 启动时清理一次
        self._cleanup_old_logs()
    
    def getFilesToDelete(self):
        """
        重写父类方法，使用自定义的备份文件命名格式来匹配和删除
        """
        # 获取所有备份文件（使用自定义格式）
        dir_name = os.path.dirname(self.baseFilename)
        base_name = os.path.basename(self.baseFilename)
        name_without_ext = os.path.splitext(base_name)[0]
        
        # 匹配模式：exam_app.2026-08-20.log
        pattern = os.path.join(dir_name, f"{name_without_ext}.*.log")
        result = glob.glob(pattern)
        
        # 按修改时间排序
        result.sort(key=lambda x: os.path.getmtime(x))
        
        # 如果备份文件数量超过 backupCount，删除最旧的
        if len(result) < self.backupCount:
            return []
        
        # 返回需要删除的文件（最旧的那些）
        return result[:len(result) - self.backupCount]
    
    def doRollover(self):
        """
        重写轮转方法，使用自定义的备份文件名格式
        """
        if self.stream:
            self.stream.close()
            self.stream = None
        
        # 获取当前时间
        current_time = int(time.time())
        dst_now = time.localtime(current_time)[-1]
        
        # 获取轮转时间
        t = self.rolloverAt - self.interval
        if self.utc:
            time_tuple = time.gmtime(t)
        else:
            time_tuple = time.localtime(t)
            dst_then = time_tuple[-1]
            if dst_now != dst_then:
                if dst_now:
                    addend = 3600
                else:
                    addend = -3600
                time_tuple = time.localtime(t + addend)
        
        # 生成备份文件名：exam_app.2026-08-20.log
        dfn = self.rotation_filename(
            os.path.join(self.log_dir, 
                        f"{self.log_name_without_ext}.{time.strftime(self.suffix, time_tuple)}.log")
        )
        
        # 如果备份文件已存在，先删除
        if os.path.exists(dfn):
            os.remove(dfn)
        
        # 重命名当前日志文件为备份文件
        self.rotate(self.baseFilename, dfn)
        
        # 更新轮转时间
        if self.interval == 1 and self.when == 'midnight':
            self.rolloverAt = self.computeRollover(current_time)
        else:
            self.rolloverAt = self.computeRollover(current_time)
        
        # 创建新的日志文件
        if not self.delay:
            self.stream = self._open()
        
        # 删除过期的备份文件
        if self.backupCount > 0:
            for s in self.getFilesToDelete():
                try:
                    os.remove(s)
                except Exception:
                    pass
    
    def _cleanup_old_logs(self):
        """清理超过保留天数的日志文件"""
        # 从配置文件读取保留天数
        retention_days = self._get_retention_days()
        
        if not self.log_dir or not os.path.exists(self.log_dir):
            return
        
        # 匹配自定义格式的备份文件：exam_app.*.log
        pattern = os.path.join(self.log_dir, f"{self.log_name_without_ext}.*.log")
        log_files = glob.glob(pattern)
        
        # 计算截止时间
        cutoff_time = datetime.now() - timedelta(days=retention_days)
        cutoff_timestamp = cutoff_time.timestamp()
        
        deleted_count = 0
        for file_path in log_files:
            # 跳过当前正在写入的日志文件
            if file_path == self.baseFilename:
                continue
            
            try:
                file_mtime = os.path.getmtime(file_path)
                if file_mtime < cutoff_timestamp:
                    os.remove(file_path)
                    deleted_count += 1
            except (OSError, FileNotFoundError):
                pass
        
        if deleted_count > 0:
            print(f"[LOG] 已清理 {deleted_count} 个旧日志文件（保留 {retention_days} 天）")
    
    def _get_retention_days(self):
        """从配置文件读取保留天数"""
        config_file = 'log_config.json'
        default_days = 2
        try:
            if os.path.exists(config_file):
                with open(config_file, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                    days = config.get('retention_days', default_days)
                    if isinstance(days, int) and days > 0:
                        return days
        except Exception:
            pass
        return default_days
    
    def emit(self, record):
        """每次写入日志时检查是否需要清理"""
        try:
            now = datetime.now()
            if now - self._last_cleanup_time > self._cleanup_interval:
                self._cleanup_old_logs()
                self._last_cleanup_time = now
            super().emit(record)
        except Exception:
            self.handleError(record)


# ============================================================
# 3. 配置读取工具函数
# ============================================================
def get_retention_days_from_config():
    """从配置文件读取保留天数，如果失败则返回默认值 2"""
    config_file = 'log_config.json'
    default_days = 2
    try:
        if os.path.exists(config_file):
            with open(config_file, 'r', encoding='utf-8') as f:
                config = json.load(f)
                days = config.get('retention_days', default_days)
                if isinstance(days, int) and days > 0:
                    return days
    except Exception as e:
        print(f"[LOG] 读取配置文件失败: {e}")
    return default_days


# ============================================================
# 4. 统一的日志设置函数
# ============================================================
def setup_logging(app=None, log_level=None, log_dir='logs', keep_days=None, 
                   console_level=None, file_level=None, is_production=None):
    """
    配置日志系统（支持自动清理和脱敏）
    
    Args:
        app: Flask app 实例（可选）
        log_level: 全局日志级别
        log_dir: 日志目录
        keep_days: 保留天数（如果为 None，则从配置文件读取）
        console_level: 控制台日志级别
        file_level: 文件日志级别
        is_production: 是否生产环境
    
    Returns:
        logger: 应用日志器
    """
    # 判断环境
    if is_production is None:
        is_production = (
            os.environ.get('FLASK_ENV') == 'production' or
            os.environ.get('RENDER', '') == 'true' or
            os.environ.get('APP_ENV') in ['production', 'prod']
        )
    
    # 确定保留天数
    if keep_days is None:
        keep_days = get_retention_days_from_config()
    
    # 确定日志级别
    if log_level is None:
        base_level = logging.WARNING if is_production else logging.DEBUG
    else:
        base_level = log_level
    
    if console_level is None:
        console_level = base_level
    
    if file_level is None:
        file_level = base_level
    
    # 确保日志目录存在
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)
    
    log_file = os.path.join(log_dir, 'exam_app.log')
    
    # 清除已有的处理器
    root_logger = logging.getLogger()
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    
    # ========== 创建处理器 ==========
    
    # 1. 控制台处理器
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(console_level)
    console_formatter = logging.Formatter(
        '[%(asctime)s] %(levelname)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    console_handler.setFormatter(console_formatter)
    console_handler.addFilter(SensitiveDataFilter())
    root_logger.addHandler(console_handler)
    
    # 2. 文件处理器（使用自定义处理器）
    file_handler = CustomTimedRotatingFileHandler(
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
    if is_production:
        logging.getLogger('supabase').setLevel(logging.ERROR)
        logging.getLogger('httpx').setLevel(logging.WARNING)
        logging.getLogger('httpcore').setLevel(logging.WARNING)
        logging.getLogger('urllib3').setLevel(logging.WARNING)
        logging.getLogger('werkzeug').setLevel(logging.WARNING)
    
    # ========== 获取应用日志器 ==========
    app_logger = logging.getLogger(__name__)
    
    # ========== 启动日志 ==========
    env_name = "PRODUCTION" if is_production else "DEVELOPMENT"
    app_logger.info("=" * 60)
    app_logger.info(f"🔧 环境: {env_name}")
    app_logger.info(f"   Console level: {logging.getLevelName(console_level)}")
    app_logger.info(f"   File level: {logging.getLevelName(file_level)}")
    app_logger.info(f"   Log file: {log_file}")
    app_logger.info(f"   Keep logs: {keep_days} days (from config)")
    app_logger.info(f"   Backup format: {os.path.splitext(log_file)[0]}.YYYY-MM-DD.log")
    app_logger.info("=" * 60)
    
    # ========== Flask App 集成 ==========
    if app:
        app.logger = app_logger
        werkzeug_logger = logging.getLogger('werkzeug')
        werkzeug_logger.handlers = root_logger.handlers
        werkzeug_logger.setLevel(base_level)
    
    return app_logger


# ============================================================
# 5. 清理旧日志函数
# ============================================================
def clean_old_logs(log_dir='logs', keep_days=None):
    """
    手动清理旧的日志文件（支持新旧两种命名格式）
    
    Args:
        log_dir: 日志目录
        keep_days: 保留天数（如果为 None，则从配置文件读取）
    """
    if keep_days is None:
        keep_days = get_retention_days_from_config()
    
    if not os.path.exists(log_dir):
        return
    
    cutoff_time = datetime.now() - timedelta(days=keep_days)
    cutoff_timestamp = cutoff_time.timestamp()
    
    deleted_count = 0
    
    # 1. 匹配新格式：exam_app.*.log
    pattern_new = os.path.join(log_dir, 'exam_app.*.log')
    for file_path in glob.glob(pattern_new):
        try:
            file_mtime = os.path.getmtime(file_path)
            if file_mtime < cutoff_timestamp:
                os.remove(file_path)
                deleted_count += 1
        except (OSError, FileNotFoundError):
            pass
    
    # 2. 匹配旧格式：exam_app.log.*（兼容过渡期）
    pattern_old = os.path.join(log_dir, 'exam_app.log.*')
    for file_path in glob.glob(pattern_old):
        try:
            file_mtime = os.path.getmtime(file_path)
            if file_mtime < cutoff_timestamp:
                os.remove(file_path)
                deleted_count += 1
        except (OSError, FileNotFoundError):
            pass
    
    if deleted_count > 0:
        print(f"[LOG] 手动清理了 {deleted_count} 个旧日志文件（保留 {keep_days} 天）")
    else:
        print(f"[LOG] 没有需要清理的日志文件（保留最近 {keep_days} 天）")


# ============================================================
# 6. 兼容接口
# ============================================================
def init_default_logging():
    """初始化默认日志配置"""
    return setup_logging(
        app=None,
        log_dir='logs',
        keep_days=None
    )


def get_logger(name):
    """获取日志器"""
    return logging.getLogger(name)


# ============================================================
# 7. 环境变量控制
# ============================================================
def get_log_level_from_env():
    """从环境变量获取日志级别"""
    level_map = {
        'DEBUG': logging.DEBUG,
        'INFO': logging.INFO,
        'WARNING': logging.WARNING,
        'ERROR': logging.ERROR,
        'CRITICAL': logging.CRITICAL
    }
    env_level = os.environ.get('LOG_LEVEL', '').upper()
    return level_map.get(env_level, None)

