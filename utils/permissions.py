# utils/permissions.py
import json
import logging
import os
from functools import wraps
from flask import session, jsonify
from services.db import get_supabase

logger = logging.getLogger(__name__)

# ==================== 开发者账号配置 ====================
def is_developer(user_id=None):
    """判断是否为开发者账号"""
    dev_id = os.environ.get('DEVELOPER_USER_ID', '')
    if not dev_id:
        return False
    target_id = user_id or session.get('user_id')
    return target_id == dev_id

def get_developer_id():
    """获取开发者账号ID"""
    return os.environ.get('DEVELOPER_USER_ID', '')

# ==================== 权限范围获取 ====================
def get_allowed_countries():
    """
    获取当前管理员的允许国家列表
    返回:
        - None: 无限制（超管/开发者）
        - []: 无权限（普通用户）
        - list: 允许的国家代码列表（管理员）
    """
    role = session.get('role', 'user')
    
    # 开发者：无限制
    if is_developer():
        logger.debug("get_allowed_countries: 开发者，返回 None")
        return None
    
    # 超管：如果有权限范围则限制，否则无限制
    if role == 'super_admin':
        admin_countries = session.get('admin_countries')
        if admin_countries:
            try:
                countries = json.loads(admin_countries) if isinstance(admin_countries, str) else admin_countries
                if countries:
                    logger.debug(f"get_allowed_countries: 超管限制在 {countries}")
                    return countries
            except Exception as e:
                logger.error(f"解析超管权限范围失败: {e}")
        logger.debug("get_allowed_countries: 超管无限制")
        return None
    
    # 管理员
    if role == 'admin':
        admin_countries = session.get('admin_countries')
        
        # 优先使用权限范围
        if admin_countries:
            try:
                countries = json.loads(admin_countries) if isinstance(admin_countries, str) else admin_countries
                if countries:
                    logger.debug(f"get_allowed_countries: 管理员权限范围 {countries}")
                    return countries
            except Exception as e:
                logger.error(f"解析管理员权限范围失败: {e}")
        
        # 没有权限范围，使用自己所在国家
        user_country = session.get('user_country')
        if user_country:
            logger.debug(f"get_allowed_countries: 管理员使用默认国家 {user_country}")
            return [user_country]
        
        logger.warning("get_allowed_countries: 管理员没有国家信息，返回空列表")
        return []
    
    # 普通用户
    logger.debug("get_allowed_countries: 普通用户，返回空列表")
    return []


def get_admin_allowed_countries():
    """获取当前管理员的权限范围（别名）"""
    return get_allowed_countries()


def get_current_user_country():
    """获取当前登录用户的国家代码"""
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
        return country
    
    return None


def set_admin_allowed_countries(user_id, countries):
    """设置管理员/超管的允许国家列表"""
    db = get_supabase()
    countries_json = json.dumps(countries) if countries else None
    db.table("users").update({"admin_countries": countries_json}).eq("id", user_id).execute()
    
    if user_id == session.get('user_id'):
        session['admin_countries'] = countries_json


# ==================== 用户权限检查 ====================
def can_view_user(target_user):
    """检查当前用户是否可以查看目标用户"""
    current_role = session.get('role')
    current_user_id = session.get('user_id')
    dev_id = os.environ.get('DEVELOPER_USER_ID')
    
    # 开发者可以查看所有用户
    if dev_id and current_user_id == dev_id:
        return True
    
    # 不能查看受保护账号（除非是本人）
    if target_user.get('is_protected') and target_user.get('id') != current_user_id:
        return False
    
    # 非超管不能查看超管和开发者
    if current_role != 'super_admin':
        if target_user.get('role') in ('super_admin', 'developer'):
            return False
    
    # 如果是自己，总是可以查看
    if target_user.get('id') == current_user_id:
        return True
    
    # 管理员权限范围检查
    if current_role == 'admin':
        allowed_countries = get_allowed_countries()
        if allowed_countries:
            user_country = target_user.get('country')
            if user_country:
                if user_country not in allowed_countries:
                    return False
            else:
                # 无国家用户：只有创建者可以查看
                if target_user.get('created_by') != current_user_id:
                    return False
    
    return True


def can_modify_user(target_user, current_user, action='edit'):
    """检查当前用户是否有权限修改目标用户"""
    dev_id = os.environ.get('DEVELOPER_USER_ID')
    if dev_id and current_user['id'] == dev_id:
        return True
    if target_user.get('is_protected'):
        return False
    return True


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


# ==================== 用户列表过滤（带创建人姓名）====================
def filter_users_by_permission(users, allowed_countries=None, current_user_id=None):
    """
    根据权限过滤用户列表，并自动添加创建人姓名
    
    Args:
        users: 用户列表
        allowed_countries: 管理员允许的国家列表，None表示无限制
        current_user_id: 当前用户ID，用于判断自己创建的无国家用户
    
    Returns:
        过滤后的用户列表（已添加 created_by_name 字段）
    """
    if current_user_id is None:
        current_user_id = session.get('user_id')
    
    if allowed_countries is None:
        allowed_countries = get_allowed_countries()
    
    # 收集所有创建人ID
    creator_ids = list(set([u.get('created_by') for u in users if u.get('created_by')]))
    
    # 批量查询创建人姓名
    creator_name_map = {}
    if creator_ids:
        try:
            db = get_supabase()
            creators_res = db.table("users").select("id, name_en, name_cn").in_("id", creator_ids).execute()
            for creator in (creators_res.data or []):
                creator_name_map[creator['id']] = creator.get('name_en') or creator.get('name_cn', '')
        except Exception as e:
            logger.error(f"查询创建人姓名失败: {e}")
    
    # 过滤用户并添加创建人姓名
    filtered_users = []
    for user in users:
        if not can_view_user(user):
            continue
        
        # 添加创建人姓名
        user['created_by_name'] = creator_name_map.get(user.get('created_by'), '')
        filtered_users.append(user)
    
    return filtered_users


# ==================== 国家过滤（Supabase 查询）====================
def apply_country_filter(query, table_alias='country'):
    """
    应用国家权限过滤（Supabase 查询层）
    注意：此函数由于 SDK 限制，复杂条件建议使用 filter_users_by_permission
    """
    allowed = get_allowed_countries()
    
    if allowed is None:
        return query
    
    if not allowed:
        return query.filter("id", "eq", "no-permission")
    
    # 简单场景：只做 IN 过滤
    return query.in_(table_alias, allowed)


# ==================== 输入解析 ====================
def parse_countries_input(country_str):
    """
    解析权限范围输入
    支持格式: "NP", "NP,LK", "NP;LK", "尼泊尔,斯里兰卡"
    """
    if not country_str:
        return []
    
    import re
    
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


# ==================== 角色判断 ====================
def has_role(required_roles):
    """检查当前用户是否拥有指定角色"""
    if is_developer():
        return True
    user_role = session.get('role', 'user')
    if isinstance(required_roles, str):
        required_roles = [required_roles]
    return user_role in required_roles


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