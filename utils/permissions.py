# utils/permissions.py
import json
import logging
from functools import wraps
from flask import session, jsonify
from services.db import get_supabase

logger = logging.getLogger(__name__)

# ==================== 开发者账号配置 ====================
def is_developer(user_id=None):
    """判断是否为开发者账号"""
    import os
    dev_id = os.environ.get('DEVELOPER_USER_ID', '')
    if not dev_id:
        return False
    target_id = user_id or session.get('user_id')
    return target_id == dev_id


def get_developer_id():
    """获取开发者账号ID"""
    import os
    return os.environ.get('DEVELOPER_USER_ID', '')


# ==================== 角色判断 ====================
def has_role(required_roles):
    """检查当前用户是否拥有指定角色"""
    if is_developer():
        return True
    user_role = session.get('role', 'user')
    if isinstance(required_roles, str):
        required_roles = [required_roles]
    return user_role in required_roles


def can_manage_role(target_role):
    """检查当前用户是否可以管理目标角色"""
    current_role = session.get('role', 'user')
    
    if is_developer():
        return True
    
    if current_role == 'super_admin':
        return target_role in ['user', 'admin', 'super_admin']
    if current_role == 'admin':
        return target_role == 'user'
    return False


def can_view_user(target_user):
    """检查当前用户是否可以查看目标用户"""
    current_role = session.get('role', 'user')
    current_user_id = session.get('user_id')
    
    if is_developer():
        return True
    
    if current_role == 'user':
        return target_user.get('id') == current_user_id
    
    if current_role == 'super_admin':
        return not target_user.get('is_protected', False)
    
    if current_role == 'admin':
        allowed_countries = get_admin_allowed_countries()
        if allowed_countries is None:
            return True
        user_country = target_user.get('country', '')
        return user_country in allowed_countries
    
    return False


# ==================== 权限范围管理 ====================
def get_admin_allowed_countries():
    """
    获取当前管理员的允许国家列表
    返回:
        - None: 无限制
        - []: 无权限
        - list: 允许的国家代码列表
    """
    from flask import session
    import json
    
    # 开发者：无限制
    if is_developer():
        logger.info("get_admin_allowed_countries: 开发者，返回 None")
        return None
    
    role = session.get('role', 'user')
    logger.info(f"get_admin_allowed_countries: role={role}, session keys={list(session.keys())}")
    
    # 超管：如果有权限范围则限制，否则无限制
    if role == 'super_admin':
        admin_countries = session.get('admin_countries')
        logger.info(f"get_admin_allowed_countries: super_admin, admin_countries={admin_countries}")
        if admin_countries:
            try:
                countries = json.loads(admin_countries) if isinstance(admin_countries, str) else admin_countries
                if countries:
                    logger.info(f"get_admin_allowed_countries: 超管限制在 {countries}")
                    return countries
            except Exception as e:
                logger.error(f"解析超管权限范围失败: {e}")
        logger.info("get_admin_allowed_countries: 超管无限制")
        return None
    
    # 管理员
    if role == 'admin':
        admin_countries = session.get('admin_countries')
        logger.info(f"get_admin_allowed_countries: admin, admin_countries={admin_countries}")
        
        # 优先使用权限范围
        if admin_countries:
            try:
                countries = json.loads(admin_countries) if isinstance(admin_countries, str) else admin_countries
                if countries:
                    logger.info(f"get_admin_allowed_countries: 管理员权限范围 {countries}")
                    return countries
            except Exception as e:
                logger.error(f"解析管理员权限范围失败: {e}")
        
        # 没有权限范围，使用自己所在国家
        user_country = session.get('user_country')
        logger.info(f"get_admin_allowed_countries: 使用默认国家 user_country={user_country}")
        if user_country:
            return [user_country]
        
        logger.warning("get_admin_allowed_countries: 管理员没有国家信息，返回空列表")
        return []
    
    # 普通用户
    logger.info("get_admin_allowed_countries: 普通用户，返回空列表")
    return []


def get_current_user_country():
    """获取当前登录用户的国家代码"""
    from flask import session
    from services.db import get_supabase
    
    user_id = session.get('user_id')
    if not user_id:
        return None
    
    # 先从 session 获取
    if 'user_country' in session:
        return session.get('user_country')
    
    # 从数据库获取并缓存到 session
    db = get_supabase()
    res = db.table("users").select("country").eq("id", user_id).maybe_single().execute()
    if res.data:
        country = res.data.get('country')
        session['user_country'] = country
        logger.info(f"从数据库获取用户国家: user_id={user_id}, country={country}")
        return country
    
    return None


def set_admin_allowed_countries(user_id, countries):
    """设置管理员/超管的允许国家列表"""
    db = get_supabase()
    countries_json = json.dumps(countries) if countries else None
    db.table("users").update({"admin_countries": countries_json}).eq("id", user_id).execute()
    
    if user_id == session.get('user_id'):
        session['admin_countries'] = countries_json


def parse_countries_input(country_str):
    """
    解析权限范围输入
    支持格式: "NP", "NP,LK", "NP;LK", "尼泊尔,斯里兰卡"
    """
    if not country_str:
        return []
    
    import re
    from services.db import get_supabase
    
    # 按常见分隔符分割
    delimiters = r'[,;:：\s]+'
    parts = re.split(delimiters, country_str.strip())
    parts = [p.strip() for p in parts if p.strip()]
    
    if not parts:
        return []
    
    # 获取国家映射表
    db = get_supabase()
    countries_res = db.table("countries").select("code, name_zh, name_en").execute()
    country_map = {}
    for c in (countries_res.data or []):
        country_map[c['code']] = c['code']
        country_map[c['name_zh']] = c['code']
        country_map[c['name_en'].lower()] = c['code']
    
    codes = []
    for part in parts:
        part_lower = part.lower()
        if part_lower in country_map:
            codes.append(country_map[part_lower])
        elif part.upper() in country_map:
            codes.append(country_map[part.upper()])
        else:
            for name, code in country_map.items():
                if part_lower in name.lower():
                    codes.append(code)
                    break
    
    return list(set(codes))


# ==================== 权限装饰器 ====================
def developer_required(f):
    """开发者权限装饰器"""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not is_developer():
            return jsonify({"success": False, "message": "developer_only"}), 403
        return f(*args, **kwargs)
    return decorated


def super_admin_required(f):
    """超管权限装饰器"""
    @wraps(f)
    def decorated(*args, **kwargs):
        if is_developer():
            return f(*args, **kwargs)
        if session.get('role') != 'super_admin':
            return jsonify({"success": False, "message": "super_admin_only"}), 403
        return f(*args, **kwargs)
    return decorated


# ==================== 国家过滤函数 ====================
def apply_country_filter(query, table_alias='country'):
    """
    应用国家过滤到数据库查询
    返回过滤后的 query
    """
    from flask import session
    import json
    
    role = session.get('role', 'user')
    is_dev = is_developer()
    
    logger.info(f"apply_country_filter: role={role}, is_dev={is_dev}, table_alias={table_alias}")
    
    # 开发者：无限制
    if is_dev:
        logger.info("apply_country_filter: 开发者，无限制")
        return query
    
    # 超管：如果有权限范围则限制，否则无限制
    if role == 'super_admin':
        admin_countries = session.get('admin_countries')
        logger.info(f"apply_country_filter: super_admin, admin_countries={admin_countries}")
        if admin_countries:
            try:
                countries = json.loads(admin_countries) if isinstance(admin_countries, str) else admin_countries
                if countries:
                    logger.info(f"apply_country_filter: 超管限制在 {countries}")
                    return query.in_(table_alias, countries)
            except Exception as e:
                logger.error(f"解析失败: {e}")
        logger.info("apply_country_filter: 超管无限制")
        return query
    
    # 管理员：限制在权限范围或自己所在国家
    if role == 'admin':
        admin_countries = session.get('admin_countries')
        logger.info(f"apply_country_filter: admin, admin_countries={admin_countries}")
        
        # 优先使用权限范围
        if admin_countries:
            try:
                countries = json.loads(admin_countries) if isinstance(admin_countries, str) else admin_countries
                if countries:
                    logger.info(f"apply_country_filter: 管理员权限范围 {countries}")
                    return query.in_(table_alias, countries)
            except Exception as e:
                logger.error(f"解析失败: {e}")
        
        # 没有权限范围，使用自己所在国家
        user_country = session.get('user_country')
        logger.info(f"apply_country_filter: 使用默认国家 user_country={user_country}")
        if user_country:
            return query.eq(table_alias, user_country)
        
        # 连国家都没有，返回空
        logger.warning("apply_country_filter: 管理员没有国家信息，返回空")
        return query.eq(table_alias, '__NONEXISTENT__')
    
    # 普通用户：只能看自己
    user_id = session.get('user_id')
    logger.info(f"apply_country_filter: 普通用户只能看自己 {user_id}")
    return query.eq('id', user_id)