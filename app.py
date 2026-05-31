# app.py (最终重构版)
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
from flask import Flask, request, jsonify, session, make_response
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


# 1. 日志配置
logging.basicConfig(level=logging.DEBUG, format='[%(asctime)s] %(levelname)s in %(module)s: %(message)s', handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler('exam_debug.log', encoding='utf-8', mode='a')])
logger = logging.getLogger(__name__)
logger.info("🚀 Flask 应用启动，日志级别: DEBUG")

# 2. 应用配置
app = Flask(__name__)
app.config.from_object(Config)
app.secret_key = Config.SECRET_KEY
app.debug = os.environ.get('FLASK_DEBUG', 'false').lower() == 'true'

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
        print(f"permission '{permission}' result: {result}")
        print(f"=========================")
        
        return result
    
    return {
        'current_user_role': session.get('role', 'user'),
        'current_user_id': session.get('user_id'),
        'is_super_admin': session.get('role') == 'super_admin',
        'is_developer': is_developer(),
        'has_permission': has_permission,
    }

# app.py

@app.before_request
def detect_user_timezone():
    """
    在每个请求前检测并存储用户时区
    """
    # 从 Cookie 获取用户时区
    user_tz = request.cookies.get('user_timezone')
    
    # 也可以从请求头获取
    if not user_tz:
        user_tz = request.headers.get('X-Timezone')
    
    if user_tz:
        from utils.timezone_utils import set_user_timezone
        set_user_timezone(user_tz)
    
    # ✅ 不在 before_request 中主动调用 get_user_timezone()
    # 让各个函数按需获取


@app.route('/api/user/timezone', methods=['GET', 'POST'])
@login_required
def user_timezone():
    """获取或设置用户时区"""
    from utils.timezone_utils import get_user_timezone, set_user_timezone
    
    if request.method == 'POST':
        timezone_str = request.json.get('timezone')
        if set_user_timezone(timezone_str):
            # 同时设置 Cookie
            resp = jsonify({"success": True, "timezone": timezone_str})
            resp.set_cookie('user_timezone', timezone_str, max_age=365*24*60*60)
            return resp
        return jsonify({"success": False, "message": "无效的时区"}), 400
    else:
        # GET 请求：返回当前时区
        current_tz = get_user_timezone()
        return jsonify({"timezone": current_tz})

@app.template_filter('local_time')
def local_time_filter(utc_string, format_str='%Y-%m-%d %H:%M:%S'):
    """Jinja2 过滤器：将 UTC 时间转换为用户本地时间"""
    return utc_string_to_local(utc_string, format_str=format_str)

@app.template_filter('local_date')
def local_date_filter(utc_string):
    """Jinja2 过滤器：只显示日期"""
    return utc_string_to_local(utc_string, format_str='%Y-%m-%d')

@app.template_filter('local_datetime')
def local_datetime_filter(utc_string):
    """Jinja2 过滤器：显示日期时间（简洁版）"""
    return utc_string_to_local(utc_string, format_str='%Y-%m-%d %H:%M')

@app.context_processor
def utility_processor():
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

@app.route('/debug/routes')
@login_required
@admin_required
def debug_routes():
    """查看所有注册的路由"""
    routes = []
    for rule in app.url_map.iter_rules():
        routes.append({
            "endpoint": rule.endpoint,
            "methods": list(rule.methods),
            "path": str(rule)
        })
    return jsonify(routes)

@app.before_request
def log_404_path():
    print(f"🌐 请求: {request.method} {request.path}", flush=True)

@app.route('/favicon.ico')
def favicon():
    """浏览器 favicon 请求，返回 204 无内容"""
    # ✅ 简单返回空响应，避免文件不存在报错
    from flask import make_response
    response = make_response('', 204)
    response.headers['Content-Type'] = 'image/x-icon'
    return response

@app.before_request
def log_request_url():
    logging.info(f"🌐 请求路径: {request.method} {request.url}")

# 3. 模板过滤器 (必须留在 app.py)
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
def format_options_filter(options): return join_options_filter(options)

@app.template_filter('utc_to_local')
def utc_to_local_filter(utc_string):
    """UTC 转本地时间字符串（24小时制，带秒）"""
    return format_datetime_24h(utc_string)

@app.template_filter('format_datetime_local')
def format_datetime_local_filter(utc_string):
    """格式化本地时间（24小时制，简洁版）"""
    return format_datetime_24h_short(utc_string)

@app.template_filter('format_datetime_local')
def format_datetime_local(utc_string): 
    return utc_to_local_filter(utc_string)

# ✅ 新增：专门的24小时制过滤器
@app.template_filter('datetime_24h')
def datetime_24h_filter(utc_string):
    """24小时制时间格式化：YYYY-MM-DD HH:MM:SS"""
    return format_datetime_24h(utc_string)

@app.template_filter('datetime_24h_short')
def datetime_24h_short_filter(utc_string):
    """24小时制时间格式化（简洁版）：YYYY-MM-DD HH:MM"""
    return format_datetime_24h_short(utc_string)

# 4. 错误处理器
@app.errorhandler(Exception)
def handle_all_exceptions(e):
    import traceback
    logger.error(f"未捕获异常: {type(e).__name__}: {e}", exc_info=True)
    print(f"\n❌❌❌ GLOBAL ERROR: {type(e).__name__}: {e}", file=sys.stderr)
    traceback.print_exc(file=sys.stderr)
    return f"500 Error: {type(e).__name__}", 500

# 5. 开发者账号初始化
def init_developer_account():
    dev_id = os.environ.get('DEVELOPER_USER_ID')
    if not dev_id: return
    db = get_supabase()
    existing = db.table("users").select("id").eq("id", dev_id).execute()
    if not existing.data:
        dev_data = {"id": dev_id, "email": os.environ.get('DEVELOPER_EMAIL', 'dev@example.com'), "name_en": "System Developer", "role": "developer", "user_status": "registered", "is_active": True, "is_protected": True, "password_hash": auth.hash_password(os.environ.get('DEVELOPER_PASSWORD', 'ChangeMe123!')), "created_at": datetime.now(timezone.utc).isoformat()}
        try: db.table("users").insert(dev_data).execute(); logger.info("开发者账号已创建")
        except Exception as e: logger.error(f"创建失败: {e}")
    else:
        db.table("users").update({"is_protected": True}).eq("id", dev_id).execute()

def register_global_routes(app):
    """注册不属于任何蓝图的全局路由"""
    
    @app.route('/health')
    def health_check():
        return "OK", 200
    
    @app.route('/favicon.ico')
    def favicon():
        return '', 204
    
    @app.route('/.well-known/appspecific/com.chrome.devtools.json')
    def devtools():
        return '', 204
    
    # 开发环境专用
    if app.debug:
        @app.route('/shutdown', methods=['POST'])
        def shutdown():
            func = request.environ.get('werkzeug.server.shutdown')
            if func:
                func()
            return 'Server shutting down...'

@app.route('/static/<path:filename>')
def static_proxy(filename):
    """兜底静态资源路由（可选）"""
    from flask import send_from_directory
    try:
        return send_from_directory(app.static_folder, filename)
    except:
        # 文件不存在时返回 404，但不抛异常
        from werkzeug.exceptions import NotFound
        raise NotFound()

@app.route('/.well-known/<path:filename>')
def well_known_files(filename):
    """处理 .well-known 目录下的请求，避免404日志"""
    # 这些是浏览器/工具的探测请求，返回空响应即可
    logger.debug(f"Well-known request: {filename}")
    return '', 204  # No Content

# 6. 注册路由 & 启动调度器 & 初始化
with app.app_context():
    register_blueprints(app)
    init_scheduler(app)
    init_developer_account()

if __name__ == '__main__':
    HOST = os.environ.get('HOST', '127.0.0.1')
    PORT = int(os.environ.get('PORT', 5000))
    app.run(host=HOST, port=PORT, debug=app.debug, use_reloader=False, threaded=True)