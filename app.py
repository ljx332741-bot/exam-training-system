# app.py (最终重构版 - 带日志脱敏和自动清理)
import os
import json
import logging
import sys
import traceback
import pytz
import re
import string
from routes import register_blueprints
from datetime import datetime, timezone, date
from flask import Flask, request, jsonify, session, make_response, redirect, url_for
from services.db import get_supabase
from services import auth, exam, export
from services.auth import hash_password
from config import Config
from routes.helpers import login_required, admin_required
from services.scheduler import init_scheduler
from utils.permissions import is_developer
from utils.common import utc_to_local, format_datetime_local
from utils.i18n_messages import I18nMessages
from utils.timezone_utils import get_user_timezone, format_datetime, utc_string_to_local, format_datetime_24h, format_datetime_24h_short, set_user_timezone
from utils.cache_manager import training_cache
from utils.logger import setup_logging, clean_old_logs, init_default_logging


# ========== 1. 日志配置 ==========
# 方法一：使用默认初始化（兼容旧方式）
IS_PRODUCTION = init_default_logging()
logger = logging.getLogger(__name__)


# 手动清理一次旧日志（启动时清理）
clean_old_logs('logs', 2)


# ========== 2. 应用配置 ==========
app = Flask(__name__)
app.config.from_object(Config)
app.secret_key = Config.SECRET_KEY

# ✅ 关联日志到 app
app.logger = logger

# 将 Flask 的日志也纳入管理
werkzeug_logger = logging.getLogger('werkzeug')
werkzeug_logger.handlers = logging.root.handlers
werkzeug_logger.setLevel(logging.WARNING if IS_PRODUCTION else logging.DEBUG)

# ✅ 缓存管理器日志（已经由 logger 处理）
logger.info("=" * 60)
logger.info("🚀 缓存管理器已初始化")
logger.info(f"📊 当前缓存: {training_cache.get_stats()}")
logger.info("=" * 60)

# 根据生产环境强制设置 debug
if IS_PRODUCTION:
    app.debug = False
    os.environ['FLASK_DEBUG'] = 'false'
else:
    app.debug = os.environ.get('FLASK_DEBUG', 'false').lower() == 'true'

logger.info(f"App debug mode: {app.debug}")


# ========== 3. 请求日志（脱敏版本）==========
@app.before_request
def log_request_safe():
    """安全的请求日志 - 不记录敏感参数"""
    logger.debug(f"Request: {request.method} {request.path}")


# ========== 4. 模板上下文处理器 ==========
@app.context_processor
def inject_translation():
    """注入翻译函数到所有模板"""
    def t(key, **params):
        """服务端翻译函数"""
        lang = session.get('lang', 'zh')
        return I18nMessages.get_message(key, lang, **params)
    return dict(t=t)


@app.context_processor
def utility_processor():
    from utils.permissions import is_developer
    
    def has_permission(permission):
        """检查特定权限"""
        role = session.get('role', 'user')
        is_dev = is_developer()
        
        permissions = {
            'batch_delete': role == 'super_admin' or is_dev,
            'view_deleted_list': role == 'super_admin' or is_dev,
            'edit_user': role in ['admin', 'super_admin'] or is_dev,
            'delete_user': role in ['admin', 'super_admin'] or is_dev,
            'reset_password': role in ['admin', 'super_admin'] or is_dev,
            'view_all_list': role in ['admin', 'super_admin'] or is_dev,
        }
        result = permissions.get(permission, False)
        return result
    
    return {
        'current_user_role': session.get('role', 'user'),
        'current_user_id': session.get('user_id'),
        'is_super_admin': session.get('role') == 'super_admin',
        'is_developer': is_developer(),
        'has_permission': has_permission,
    }


@app.context_processor
def utility_processor_timezone():
    """向模板注入时区相关函数"""
    def tz_now():
        """获取当前用户本地时间"""
        from datetime import datetime
        from utils.timezone_utils import get_current_local_time
        return get_current_local_time()
    
    return {
        'user_timezone': get_user_timezone(),
        'tz_format': utc_string_to_local,
        'tz_now': tz_now,
    }


# ========== 5. 时区检测 ==========
@app.before_request
def detect_user_timezone():
    """在每个请求前检测并存储用户时区"""
    user_tz = request.cookies.get('user_timezone')
    if not user_tz:
        user_tz = request.headers.get('X-Timezone')
    if user_tz:
        set_user_timezone(user_tz)


# ========== 6. API 路由 ==========
@app.route('/api/user/timezone', methods=['GET', 'POST'])
@login_required
def user_timezone():
    """获取或设置用户时区"""
    from utils.timezone_utils import get_user_timezone, set_user_timezone
    
    if request.method == 'POST':
        timezone_str = request.json.get('timezone')
        if set_user_timezone(timezone_str):
            resp = jsonify({"success": True, "timezone": timezone_str})
            resp.set_cookie('user_timezone', timezone_str, max_age=365*24*60*60)
            return resp
        return jsonify({"success": False, "message": "无效的时区"}), 400
    else:
        current_tz = get_user_timezone()
        return jsonify({"timezone": current_tz})


# ========== 7. 模板过滤器 ==========
@app.template_filter('local_time')
def local_time_filter(utc_string, format_str='%Y-%m-%d %H:%M:%S'):
    """Jinja2 过滤器：将 UTC 时间转换为用户本地时间"""
    return utc_string_to_local(utc_string, format_str=format_str) if utc_string else ''


@app.template_filter('local_date')
def local_date_filter(utc_string):
    """Jinja2 过滤器：只显示日期"""
    return utc_string_to_local(utc_string, format_str='%Y-%m-%d') if utc_string else ''


@app.template_filter('local_datetime')
def local_datetime_filter(utc_string):
    """Jinja2 过滤器：显示日期时间（简洁版）"""
    return utc_string_to_local(utc_string, format_str='%Y-%m-%d %H:%M') if utc_string else ''


@app.template_filter('join_options')
def join_options_filter(options):
    import json
    if not options: return ''
    if isinstance(options, str):
        try: options = json.loads(options)
        except: return options
    if not isinstance(options, dict): return ''
    parts = [f"{k}. {options[k]}" for k in sorted(options.keys()) if options.get(k)]
    return '; '.join(parts)


@app.template_filter('format_options')
def format_options_filter(options): 
    return join_options_filter(options)


@app.template_filter('utc_to_local')
def utc_to_local_filter(utc_string):
    """UTC 转本地时间字符串（24小时制，带秒）"""
    return format_datetime_24h(utc_string) if utc_string else ''


@app.template_filter('format_datetime_local')
def format_datetime_local_filter(utc_string):
    """格式化本地时间（24小时制，简洁版）"""
    return format_datetime_24h_short(utc_string) if utc_string else ''


@app.template_filter('datetime_24h')
def datetime_24h_filter(utc_string):
    """24小时制时间格式化：YYYY-MM-DD HH:MM:SS"""
    return format_datetime_24h(utc_string) if utc_string else ''


@app.template_filter('datetime_24h_short')
def datetime_24h_short_filter(utc_string):
    """24小时制时间格式化（简洁版）：YYYY-MM-DD HH:MM"""
    return format_datetime_24h_short(utc_string) if utc_string else ''


# ========== 8. 调试路由 ==========
@app.route('/debug/routes')
@login_required
@admin_required
def debug_routes():
    """查看所有注册的路由（仅管理员）"""
    routes = []
    for rule in app.url_map.iter_rules():
        routes.append({
            "endpoint": rule.endpoint,
            "methods": list(rule.methods),
            "path": str(rule)
        })
    return jsonify(routes)


# ========== 9. 静态资源和健康检查 ==========
@app.route('/health')
def health_check():
    return "OK", 200


@app.route('/favicon.ico')
def favicon():
    """浏览器 favicon 请求，返回 204 无内容"""
    response = make_response('', 204)
    response.headers['Content-Type'] = 'image/x-icon'
    return response


@app.route('/.well-known/<path:filename>')
def well_known_files(filename):
    """处理 .well-known 目录下的请求，避免404日志"""
    logger.debug(f"Well-known request: {filename}")
    return '', 204


@app.route('/static/<path:filename>')
def static_proxy(filename):
    """兜底静态资源路由"""
    from flask import send_from_directory
    try:
        return send_from_directory(app.static_folder, filename)
    except:
        from werkzeug.exceptions import NotFound
        raise NotFound()


# ========== 10. 错误处理器 ==========
@app.errorhandler(Exception)
def handle_all_exceptions(e):
    """全局异常处理 - 不记录敏感信息"""
    logger.error(f"Uncaught exception: {type(e).__name__}: {e}")
    # 生产环境不返回详细错误信息
    if IS_PRODUCTION:
        return "500 Internal Server Error", 500
    else:
        return f"500 Error: {type(e).__name__}", 500


# ========== 11. 开发者账号初始化（安全版本）==========
def init_developer_account():
    """安全版本 - 不会因错误阻塞应用启动"""
    dev_id = os.environ.get('DEVELOPER_USER_ID')
    if not dev_id:
        logger.debug("No DEVELOPER_USER_ID set, skipping developer account init")
        return
    
    try:
        db = get_supabase()
        
        # 测试连接
        test_query = db.table("users").select("count").limit(1).execute()
        if not test_query or not hasattr(test_query, 'data'):
            logger.warning("Cannot connect to Supabase, skipping developer account init")
            return
        
        # 检查是否存在
        existing = db.table("users").select("id").eq("id", dev_id).execute()
        
        if not existing or not existing.data:
            dev_data = {
                "id": dev_id,
                "email": os.environ.get('DEVELOPER_EMAIL', 'dev@example.com'),
                "name_en": "System Developer",
                "role": "developer",
                "user_status": "registered",
                "is_active": True,
                "is_protected": True,
                "password_hash": auth.hash_password(os.environ.get('DEVELOPER_PASSWORD', 'ChangeMe123!')),
                "created_at": datetime.now(timezone.utc).isoformat()
            }
            db.table("users").insert(dev_data).execute()
            logger.info("Developer account created")
        else:
            # 已存在，更新保护状态
            db.table("users").update({"is_protected": True}).eq("id", dev_id).execute()
            logger.debug("Developer account already exists")
            
    except Exception as e:
        # 非致命错误，只记录警告
        if '23505' in str(e):  # 唯一约束冲突
            logger.debug("Developer account already exists (unique constraint)")
        else:
            logger.warning(f"Developer account init failed (non-fatal): {e}")


# ========== 12. 调度器初始化（安全版本）==========
def init_scheduler_safe(app):
    """安全启动调度器"""
    try:
        init_scheduler(app)
        logger.info("Scheduler initialized successfully")
    except Exception as e:
        logger.warning(f"Scheduler init failed (non-fatal): {e}")


# ========== 13. 注册路由 & 启动调度器 ==========
def register_global_routes(app):
    """注册不属于任何蓝图的全局路由"""
    pass


with app.app_context():
    # 注册蓝图
    try:
        register_blueprints(app)
        logger.info("Blueprints registered successfully")
    except Exception as e:
        logger.error(f"Failed to register blueprints: {e}")
    
    # 启动调度器
    init_scheduler_safe(app)
    
    # 初始化开发者账号
    init_developer_account()


# ========== 14. 启动入口 ==========
if __name__ == '__main__':
    HOST = os.environ.get('HOST', '127.0.0.1')
    PORT = int(os.environ.get('PORT', 5000))
    
    # 生产环境强制禁用 debug
    if IS_PRODUCTION:
        use_debug = False
    else:
        use_debug = app.debug
    
    logger.info(f"Starting on {HOST}:{PORT}, debug={use_debug}, production={IS_PRODUCTION}")
    
    app.run(
        host=HOST,
        port=PORT,
        debug=use_debug,
        use_reloader=False,
        threaded=True
    )