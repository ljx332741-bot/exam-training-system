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
from flask import Flask, request, jsonify, make_response
from services.db import get_supabase
from services import auth, exam, export
from services.auth import hash_password
from config import Config
from services.scheduler import init_scheduler

# 1. 日志配置
logging.basicConfig(level=logging.DEBUG, format='[%(asctime)s] %(levelname)s in %(module)s: %(message)s', handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler('exam_debug.log', encoding='utf-8', mode='a')])
DEFAULT_LOCAL_TIMEZONE = os.environ.get('LOCAL_TIMEZONE', 'Asia/Kathmandu')
logger = logging.getLogger(__name__)
logger.info("🚀 Flask 应用启动，日志级别: DEBUG")

# 2. 应用配置
app = Flask(__name__)
app.config.from_object(Config)
app.secret_key = Config.SECRET_KEY
app.debug = os.environ.get('FLASK_DEBUG', 'false').lower() == 'true'

@app.route('/debug/routes')
def debug_routes():
    """查看所有已注册的路由（仅用于调试）"""
    routes = []
    for rule in app.url_map.iter_rules():
        routes.append({
            'endpoint': rule.endpoint,
            'methods': list(rule.methods),
            'path': str(rule)
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
    if not utc_string: return ''
    try:
        s = utc_string.replace('Z', '+00:00') if utc_string.endswith('Z') else utc_string
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None: dt = pytz.UTC.localize(dt)
        local_tz = pytz.timezone('Asia/Shanghai')
        return dt.astimezone(local_tz).strftime('%Y-%m-%dT%H:%M')
    except: return ''

@app.template_filter('format_datetime_local')
def format_datetime_local(utc_string): return utc_to_local_filter(utc_string)

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

    # app.py 临时添加
    @app.route('/debug/routes')
    def debug_routes():
        routes = []
        for rule in app.url_map.iter_rules():
            routes.append({
                "endpoint": rule.endpoint,
                "methods": list(rule.methods - {'HEAD', 'OPTIONS'}),
                "url": rule.rule
            })
        return jsonify(routes)

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

# 6. 注册路由 & 启动调度器 & 初始化
with app.app_context():
    register_blueprints(app)
    init_scheduler(app)
    init_developer_account()

if __name__ == '__main__':
    HOST = os.environ.get('HOST', '127.0.0.1')
    PORT = int(os.environ.get('PORT', 5000))
    app.run(host=HOST, port=PORT, debug=app.debug, use_reloader=False, threaded=True)