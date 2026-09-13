# routes/helpers.py - 精简版，只保留装饰器和业务辅助函数
import os
import json
import re
import random
import logging
import time
from dateutil import parser
from datetime import datetime, timezone, timedelta
from functools import wraps
from flask import session, redirect, url_for, flash, jsonify
from services.db import get_supabase, get_supabase_admin
from utils.permissions import get_allowed_countries, get_admin_allowed_countries, is_developer
from utils.timezone_utils import get_current_local_time, format_datetime_24h_short, format_datetime

logger = logging.getLogger(__name__)

# ========== 用户状态缓存 ==========
# 结构: {user_id: (timestamp, status_dict_or_None)}
_USER_STATUS_CACHE = {}
_USER_STATUS_CACHE_TTL = 60  # 秒

def _get_user_status(user_id):
    """获取用户当前状态（带 60 秒缓存）"""
    if not user_id:
        return None
    
    now = time.time()
    cached = _USER_STATUS_CACHE.get(user_id)
    if cached and now - cached[0] < _USER_STATUS_CACHE_TTL:
        return cached[1]
    
    try:
        db = get_supabase()
        res = db.table("users").select(
            "id, role, is_resign, deleted_at, is_protected, "
            "admin_countries, country, user_status, is_active"
        ).eq("id", user_id).maybe_single().execute()
        status = res.data if res and res.data else None
    except Exception as e:
        logger.warning(f"校验用户状态失败 (user_id={user_id}): {e}")
        # 出错时返回"上次缓存"（如果有），避免误踢
        if cached:
            return cached[1]
        return "DB_ERROR"  # 特殊标记，不踢人
    
    _USER_STATUS_CACHE[user_id] = (now, status)
    return status


def invalidate_user_status_cache(user_id):
    """清除指定用户的缓存（离职/删除/改权限后调用）"""
    if user_id in _USER_STATUS_CACHE:
        del _USER_STATUS_CACHE[user_id]
    logger.debug(f"清除用户状态缓存: {user_id}")


def _reject_request(reason, status_code=401):
    """统一的拒绝响应"""
    is_ajax = (
        request.headers.get('X-Requested-With') == 'XMLHttpRequest'
        or request.path.startswith('/api/')
    )
    
    if is_ajax:
        return jsonify({
            "success": False,
            "code": reason,
            "message": reason
        }), status_code
    
    # 普通页面请求：清 session 并跳登录
    session.clear()
    flash({'msg': reason, 'params': []}, 'danger')
    return redirect('/login')


# ================= 装饰器 =================

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return _reject_request('not_logged_in', 401)
        
        user_id = session['user_id']
        status = _get_user_status(user_id)
        
        # DB 异常：放行（避免因 DB 抖动误踢用户）
        if status == "DB_ERROR":
            logger.warning(f"DB 异常，临时放行 user_id={user_id}")
            return f(*args, **kwargs)
        
        # 用户不存在（被硬删除）
        if not status:
            session.clear()
            logger.warning(f"用户不存在，清 session: {user_id}")
            return _reject_request('account_deleted', 401)
        
        # 被软删除
        if status.get('deleted_at'):
            session.clear()
            logger.warning(f"用户已删除，清 session: {user_id}")
            return _reject_request('account_deleted', 401)
        
        # 已离职
        if status.get('is_resign'):
            session.clear()
            logger.warning(f"用户已离职，清 session: {user_id}")
            return _reject_request('account_resigned', 401)
        
        # 状态异常（未激活）
        if status.get('user_status') not in ('registered', 'imported'):
            session.clear()
            return _reject_request('account_inactive', 401)
        
        # ========== 实时同步 session（角色/权限被改） ==========
        current_role = status.get('role', 'user')
        if session.get('role') != current_role:
            logger.info(f"用户 {user_id} 角色变更: {session.get('role')} → {current_role}")
            session['role'] = current_role
        
        # 同步 admin_countries
        db_admin_countries = status.get('admin_countries')
        if db_admin_countries != session.get('admin_countries'):
            session['admin_countries'] = db_admin_countries
            logger.info(f"用户 {user_id} 权限范围已同步")
        
        # 同步 user_country
        db_country = status.get('country')
        if db_country != session.get('user_country'):
            session['user_country'] = db_country
        
        return f(*args, **kwargs)
    
    return decorated


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        # 先走 login_required 的校验
        if 'user_id' not in session:
            return _reject_request('not_logged_in', 401)
        
        user_id = session['user_id']
        status = _get_user_status(user_id)
        
        if status == "DB_ERROR":
            # DB 异常时用 session 里的角色继续
            pass
        elif not status or status.get('deleted_at') or status.get('is_resign'):
            session.clear()
            return _reject_request('account_resigned', 401)
        
        # 角色校验（用实时角色，而不是 session）
        current_role = status.get('role') if status and status != "DB_ERROR" else session.get('role')
        
        if current_role not in ('admin', 'super_admin', 'developer'):
            if request.path.startswith('/api/') or request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return jsonify({"success": False, "message": "permission_denied"}), 403
            flash({'msg': 'permission_denied_admin', 'params': []}, 'danger')
            return redirect('/dashboard')
        
        return f(*args, **kwargs)
    return decorated


# ================= 业务辅助函数 =================
def random_pick_questions(exam_id, count):
    db = get_supabase()
    q_res = db.table("questions").select("*").eq("exam_id", exam_id).execute()
    questions = q_res.data or []
    if len(questions) <= count:
        return questions
    return random.sample(questions, count)


def get_default_reviewer_by_country(country_code):
    if not country_code:
        return None
    db = get_supabase()
    res = db.table("users").select("name_en, employee_id").eq("country", country_code).eq("user_status", "registered").in_("role", ["admin", "super_admin"]).limit(1).execute()
    if res.data and len(res.data) > 0:
        admin = res.data[0]
        name = admin.get('name_en', '')
        emp_id = admin.get('employee_id', '')
        if name and emp_id:
            return f"{name} ({emp_id})"
        elif name:
            return name
    return None

def get_current_user():
    return {'id': session.get('user_id'), 'role': session.get('role'), 'is_protected': False}

def robust_parse_json(value, field_name=""):
    if not value:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, str):
                return robust_parse_json(parsed, field_name)
            return parsed
        except Exception as e:
            if field_name:
                logger.error(f"{field_name} 解析失败: {e}")
            return {}
    return {}

def get_training_status(training):
    start = training.get('start_time')
    end = training.get('end_time')
    now = datetime.now(timezone.utc)
    if not start or not end:
        return 'draft'
    start_dt = datetime.fromisoformat(start)
    end_dt = datetime.fromisoformat(end)
    if now < start_dt:
        return 'pending'
    elif now > end_dt:
        return 'closed'
    else:
        return 'active'


def upload_signature(signature_base64, training_id, user_id):
    """统一上传签名函数"""
    import base64
    from services.db import get_supabase
    from supabase import create_client
    from config import Config
    
    try:
        header, encoded = signature_base64.split(',', 1)
        img_data = base64.b64decode(encoded)
        storage_path = f"signatures/{training_id}/{user_id}.png"
        storage_client = create_client(Config.SUPABASE_URL, os.environ.get('SUPABASE_SERVICE_KEY', Config.SUPABASE_KEY))
        supabase_storage = storage_client.storage.from_("signatures")
        try:
            supabase_storage.remove([storage_path])
        except:
            pass
        supabase_storage.upload(storage_path, img_data, {"content-type": "image/png"})
        return supabase_storage.get_public_url(storage_path)
    except Exception as e:
        logger.error(f"上传签名失败: {e}")
        raise e

def convert_time_for_export(time_str, timezone_param=None):
    """将 UTC 时间字符串转换为本地时间，用于 Excel 或报表导出"""
    if not time_str:
        return ''
    try:
        # 方式1：如果传入了时区参数，直接使用
        if timezone_param:
            from utils.timezone_utils import utc_string_to_local
            return utc_string_to_local(time_str, timezone_param, '%Y-%m-%d %H:%M:%S')
        
        # 方式2：使用默认的 format_datetime
        return format_datetime(time_str)
    except Exception as e:
        # 方式3：降级方案 - 直接格式化 UTC 时间
        try:
            if 'T' in time_str:
                return time_str.replace('T', ' ')[:19]
            elif ' ' in time_str:
                return time_str[:19]
            return str(time_str)[:19] if time_str else ''
        except:
            return str(time_str)[:19] if time_str else ''

def get_attendance_data(training_id, country=''):
    """获取培训签到数据"""
    db = get_supabase()
    admin_db = get_supabase_admin()

    # 1. 获取培训信息
    training_res = db.table("trainings").select("*").eq("id", training_id).maybe_single().execute()
    if not training_res.data:
        return None
    training = training_res.data

    # 2. 获取表头模板（优先使用国家模板）
    header_template = None
    if country:
        # 查询国家模板
        ct_res = admin_db.table("training_country_templates")\
            .select("header_template")\
            .eq("training_id", training_id)\
            .eq("country", country)\
            .execute()
        if ct_res.data and len(ct_res.data) > 0:
            header_template = ct_res.data[0].get('header_template', {})
            logger.info(f"✅ PDF: 加载国家模板: training_id={training_id}, country={country}")
    
    if not header_template:
        header_template = training.get('header_template', {})
        logger.info(f"✅ PDF: 加载主表头模板: training_id={training_id}")
    
    # 3. 获取签到记录
    att_res = db.table("training_attendances") \
        .select("id, user_id, signature_url, signed_name, sign_time, users(email, name_cn, name_en, department, employee_id, country, company, is_resign, wh_id, wh_name_en)") \
        .eq("training_id", training_id) \
        .execute()

    att_list = att_res.data or []

    # 过滤离职人员
    att_list = [rec for rec in att_list if not rec.get('users', {}).get('is_resign', False)]
    
    # 国家权限过滤（如果有）
    allowed_countries = get_admin_allowed_countries()
    if allowed_countries is not None:
        if allowed_countries:
            filtered_list = []
            for rec in att_list:
                user = rec.get('users', {})
                user_country = user.get('country')
                if user_country and user_country in allowed_countries:
                    filtered_list.append(rec)
            att_list = filtered_list
        else:
            att_list = []
    
    # 按国家过滤（如果指定）
    if country:
        att_list = [rec for rec in att_list if rec.get('users', {}).get('country') == country]
    
    # 构建返回数据
    attendance_list = []
    for rec in att_list:
        user = rec.get('users') or {}
        if user is None:
            user = {}
        attendance_list.append({
            "id": rec['id'], 
            "user_id": rec['user_id'], 
            "department": user.get('department', ''),
            "name_cn": user.get('name_cn', ''), 
            "name_en": user.get('name_en', ''),
            "employee_id": user.get('employee_id', ''), 
            "signed_name": rec.get('signed_name', ''),
            "signature_url": rec.get('signature_url', ''), 
            "sign_time": rec.get('sign_time'),
            "company": user.get('company', ''), 
            "wh_id": user.get('wh_id', ''),
            "wh_name_en": user.get('wh_name_en', ''),
            "country": user.get('country', '')
        })
        
    return {
        "training": training, 
        "attendances": attendance_list, 
        "header_template": header_template
    }


def get_default_exam_values(request=None):
    """
    获取新建考试时的默认值（有效期和默认国家）
    
    Args:
        request: Flask request 对象（用于获取 URL 参数）
    
    Returns:
        dict: 包含 exam_start_time, exam_end_time, exam_countries, 
              from_binding, training_id, training_country
    """
    # 1. 计算默认有效期（当前时间 至 7天后）
    now_local = get_current_local_time()
    default_start_time = now_local.strftime('%Y-%m-%dT%H:%M')
    default_end_time = (now_local + timedelta(days=7)).strftime('%Y-%m-%dT%H:%M')
    
    # 2. 获取当前管理员的权限范围，预填默认国家
    allowed_countries = get_admin_allowed_countries()
    default_countries = []
    
    if allowed_countries is not None:
        if len(allowed_countries) > 0:
            default_countries = [allowed_countries[0]]
    else:
        # 开发者或超管无权限限制，不预填国家（让用户自己选择）
        default_countries = []
    
    # 3. 检查是否从培训绑定进入
    from_binding = False
    training_id = ''
    training_country = ''
    
    if request:
        from_binding = request.args.get('from_binding') == 'true'
        training_id = request.args.get('training_id', '')
        training_country = request.args.get('country', '')
        
        if from_binding and training_country:
            default_countries = [training_country]
    
    return {
        'exam_start_time': default_start_time,
        'exam_end_time': default_end_time,
        'exam_countries': default_countries,
        'from_binding': from_binding,
        'training_id': training_id,
        'training_country': training_country
    }

# ================= 时区辅助函数 =================
def local_to_utc(local_time_str, local_tz=None):
    """将本地时间字符串转换为 UTC ISO 字符串"""
    import pytz
    if not local_time_str:
        return None
    local_dt = datetime.fromisoformat(local_time_str)
    DEFAULT_LOCAL_TIMEZONE = os.environ.get('LOCAL_TIMEZONE', 'Asia/Kathmandu')
    tz_name = local_tz if local_tz else DEFAULT_LOCAL_TIMEZONE
    local_tz_obj = pytz.timezone(tz_name)
    local_dt_aware = local_tz_obj.localize(local_dt)
    utc_dt = local_dt_aware.astimezone(timezone.utc)
    return utc_dt.isoformat()

def safe_parse_datetime(dt_str):
    """安全解析时间字符串"""
    if not dt_str:
        return None
    try:
        # 统一处理 Z 结尾
        if dt_str.endswith('Z'):
            dt_str = dt_str.replace('Z', '+00:00')
        from datetime import datetime
        return datetime.fromisoformat(dt_str)
    except:
        return None
            
# ================= 考试国家辅助函数 =================
def parse_exam_countries(exam):
    """
    解析考试的国家列表，支持新旧格式
    增强版：支持更多输入格式
    """
    if not exam:
        return []
    
    # 获取国家数据
    countries_data = exam.get('countries')
    if countries_data is None:
        countries_data = exam.get('country', '')
    
    # 如果已经是列表
    if isinstance(countries_data, list):
        return [c for c in countries_data if c and c.strip()]
    
    # 如果是字符串
    if isinstance(countries_data, str):
        # 尝试解析 JSON
        try:
            parsed = json.loads(countries_data)
            if isinstance(parsed, list):
                return [c for c in parsed if c and c.strip()]
            elif isinstance(parsed, str) and parsed.strip():
                return [parsed.strip()]
            else:
                return []
        except (json.JSONDecodeError, TypeError):
            # 不是 JSON，可能是单个国家或逗号分隔
            if ',' in countries_data:
                return [c.strip() for c in countries_data.split(',') if c.strip()]
            elif countries_data.strip():
                return [countries_data.strip()]
            return []
    
    return []


def exam_countries_intersection(exam, allowed_countries):
    """检查考试国家与允许国家是否有交集"""
    if not allowed_countries:
        return True
    exam_countries = parse_exam_countries(exam)
    return any(c in allowed_countries for c in exam_countries)


def get_exam_countries_display(exam, allowed_countries=None):
    """获取考试的国家显示字符串（带权限过滤）"""
    exam_countries = parse_exam_countries(exam)
    
    if allowed_countries is not None and isinstance(allowed_countries, list) and allowed_countries:
        filtered_countries = [c for c in exam_countries if c in allowed_countries]
    else:
        filtered_countries = exam_countries
    
    return ', '.join(filtered_countries) if filtered_countries else '-'

def check_country_permission(country_code=None, country_list=None):
    """
    检查当前用户是否有权管理指定的国家
    """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if is_developer():
                return f(*args, **kwargs)
            
            allowed = get_admin_allowed_countries()
            current_role = session.get('role')
            
            # 确定要检查的国家列表
            check_countries = []
            if country_code:
                check_countries = [country_code]
            elif country_list:
                check_countries = country_list
            elif 'exam_id' in kwargs:
                # 从数据库获取考试的国家
                db = get_supabase()
                exam_res = db.table("exams").select("countries, country").eq("id", kwargs['exam_id']).maybe_single().execute()
                if exam_res.data:
                    check_countries = parse_exam_countries(exam_res.data)
            
            if current_role == 'super_admin':
                if allowed is not None:
                    if not any(c in allowed for c in check_countries):
                        return jsonify({"error": "权限不足"}), 403
            elif current_role == 'admin':
                if allowed:
                    if not any(c in allowed for c in check_countries):
                        return jsonify({"error": "权限不足"}), 403
                else:
                    user_country = session.get('user_country')
                    if user_country not in check_countries:
                        return jsonify({"error": "权限不足"}), 403
            
            return f(*args, **kwargs)
        return decorated_function
    return decorator

def exam_permission_required(f):
    """
    装饰器：检查当前用户是否有权访问指定考试
    需要在 URL 中包含 exam_id 参数
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        exam_id = kwargs.get('exam_id')
        if not exam_id:
            # 尝试从 request 参数获取
            exam_id = request.view_args.get('exam_id') or request.args.get('exam_id')
        
        if exam_id:
            db = get_supabase()
            exam_res = db.table("exams").select("countries, country").eq("id", exam_id).maybe_single().execute()
            if exam_res.data and not can_access_exam(exam_res.data):
                return jsonify({"error": "无权访问此考试"}), 403
        
        return f(*args, **kwargs)
    return decorated_function

def can_access_exam(exam, allowed_countries=None):
    """
    检查当前用户是否有权访问考试
    
    Args:
        exam: 考试数据字典
        allowed_countries: 可选，直接传入允许的国家列表
    
    Returns:
        bool: True 表示有权访问
    """
    if is_developer():
        return True
    
    if allowed_countries is None:
        allowed_countries = get_allowed_countries()
    
    if allowed_countries is None:
        return True  # 无限制
    
    exam_countries = parse_exam_countries(exam)
    
    if not exam_countries:
        # 考试没有指定国家，按权限范围处理
        user_country = session.get('user_country')
        return user_country in allowed_countries if user_country else False
    
    return any(c in allowed_countries for c in exam_countries)


def filter_exams_by_permission(exams, allowed_countries=None):
    """根据权限过滤考试列表"""
    return [exam for exam in exams if can_access_exam(exam, allowed_countries)]

def is_user_resigned(user_id):
    """检查用户是否已离职"""
    db = get_supabase()
    res = db.table("users").select("is_resign").eq("id", user_id).maybe_single().execute()
    return res.data and res.data.get('is_resign', False)
