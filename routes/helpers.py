# routes/helpers.py - 精简版，只保留装饰器和业务辅助函数
import os
import json
import re
import random
import logging
from dateutil import parser
from datetime import datetime, timezone
from functools import wraps
from flask import session, redirect, url_for, flash
from services.db import get_supabase
from utils.permissions import get_allowed_countries, get_admin_allowed_countries, is_developer

logger = logging.getLogger(__name__)

# ================= 装饰器 =================
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect('/login')
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if session.get('role') not in ('admin', 'super_admin', 'developer'):
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


def get_attendance_data(training_id, country=''):
    """获取培训签到数据"""
    db = get_supabase()
    training_res = db.table("trainings").select("*").eq("id", training_id).maybe_single().execute()
    if not training_res.data:
        return None
    training = training_res.data
    att_res = db.table("training_attendances").select("id, user_id, signature_url, signed_name, sign_time, users(email, name_cn, name_en, department, employee_id, country, company)").eq("training_id", training_id).execute()
    att_list = att_res.data or []
    if country:
        att_list = [rec for rec in att_list if rec.get('users', {}).get('country') == country]
    attendance_list = []
    for rec in att_list:
        user = rec.get('users') or {}
        attendance_list.append({
            "id": rec['id'], "user_id": rec['user_id'], "department": user.get('department', ''),
            "name_cn": user.get('name_cn', ''), "name_en": user.get('name_en', ''),
            "employee_id": user.get('employee_id', ''), "signed_name": rec.get('signed_name', ''),
            "signature_url": rec.get('signature_url', ''), "sign_time": rec.get('sign_time'),
            "company": user.get('company', ''), "country": user.get('country', '')
        })
    header_template = None
    if country:
        ct_res = db.table("training_country_templates").select("header_template").eq("training_id", training_id).eq("country", country).execute()
        if ct_res.data:
            header_template = ct_res.data[0].get('header_template')
    if not header_template:
        header_template = training.get('header_template', {})
    return {"training": training, "attendances": attendance_list, "header_template": header_template}


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
    """解析考试的国家列表，支持新旧格式"""
    countries_data = exam.get('countries') or exam.get('country', '')
    
    if isinstance(countries_data, str):
        try:
            countries = json.loads(countries_data)
            if isinstance(countries, list):
                return countries
            else:
                return [countries] if countries else []
        except:
            return [countries_data] if countries_data else []
    elif isinstance(countries_data, list):
        return countries_data
    else:
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

# routes/helpers.py - 添加权限检查装饰器

from functools import wraps
from flask import session, jsonify

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

# 使用示例
'''
@admin_exam_bp.route('/admin/exam_status/<int:exam_id>')
@login_required
@admin_required
@check_country_permission()  # 自动从 exam_id 获取国家
def admin_exam_status(exam_id):
    # ...
'''

# routes/helpers.py - 添加权限检查装饰器

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

# 使用示例
'''
@admin_exam_bp.route('/api/admin/exam/<int:exam_id>/assignments')
@login_required
@admin_required
@exam_permission_required  # 添加权限检查
def api_admin_exam_assignments(exam_id):
    # ...
'''

# routes/helpers.py - 添加便捷函数

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
