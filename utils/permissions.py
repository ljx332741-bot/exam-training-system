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
    user_id = session.get('user_id')
    
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
        
        # 超管没有设置权限范围时，从数据库获取
        if user_id:
            try:
                db = get_supabase()
                res = db.table("users").select("admin_countries").eq("id", user_id).maybe_single().execute()
                if res.data:
                    admin_countries = res.data.get('admin_countries')
                    if admin_countries:
                        try:
                            countries = json.loads(admin_countries) if isinstance(admin_countries, str) else admin_countries
                            if countries and len(countries) > 0:
                                session['admin_countries'] = admin_countries
                                logger.debug(f"get_allowed_countries: 从数据库获取超管权限范围 {countries}")
                                return countries
                        except:
                            pass
            except Exception as e:
                logger.error(f"从数据库获取超管权限范围失败: {e}")
        
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

        # 从数据库获取
        if user_id:
            try:
                db = get_supabase()
                res = db.table("users").select("admin_countries, country").eq("id", user_id).maybe_single().execute()
                if res.data:
                    admin_countries = res.data.get('admin_countries')
                    if admin_countries:
                        try:
                            countries = json.loads(admin_countries) if isinstance(admin_countries, str) else admin_countries
                            if countries and len(countries) > 0:
                                session['admin_countries'] = admin_countries
                                logger.debug(f"get_allowed_countries: 从数据库获取管理员权限范围 {countries}")
                                return countries
                        except:
                            pass
                    
                    # 没有权限范围，使用自己所在国家
                    user_country = res.data.get('country')
                    if user_country:
                        logger.debug(f"get_allowed_countries: 管理员使用默认国家 {user_country}")
                        return [user_country]
            except Exception as e:
                logger.error(f"从数据库获取管理员权限范围失败: {e}")
        
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

# utils/permissions.py

def check_country_permission(country_code=None, country_list=None):
    """
    检查当前用户是否有权管理指定的国家
    
    Args:
        country_code: 单个国家代码
        country_list: 国家代码列表
    
    Returns:
        bool: True 表示有权，False 表示无权
    """
    # ✅ developer 无限制
    if is_developer():
        return True
    
    allowed = get_admin_allowed_countries()
    
    # 如果没有权限范围限制，返回 True
    if allowed is None:
        return True

    # ✅ 修复：如果 allowed 为空列表，无权管理任何国家
    if not allowed:
        return False
 
    # 检查国家是否在权限范围内
    check_list = []
    if country_code:
        check_list = [country_code]
    elif country_list:
        check_list = country_list
    
    if not check_list:
        return True
    
    return any(c in allowed for c in check_list)
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
    
    # ========== 超管逻辑 ==========
    if current_role == 'super_admin':
        allowed_countries = get_allowed_countries()
        
        # 无权限范围限制，可以查看所有
        if allowed_countries is None:
            return True

        # ✅ 修复：如果 allowed_countries 为空列表，无权查看任何用户
        if not allowed_countries:
            return False
        
        # 有权限范围限制
        if allowed_countries:
            user_country = target_user.get('country')
            user_status = target_user.get('user_status')
            created_by = target_user.get('created_by')
            
            # ✅ 已导入用户：根据创建者的国家判断
            if user_status == 'imported':
                # 查询创建者的国家
                creator_country = _get_user_country(created_by)
                if creator_country and creator_country in allowed_countries:
                    return True
                # 创建者国家不在权限范围内，不可见
                return False
            
            # 已注册用户：需要国家在权限范围内
            if user_country and user_country in allowed_countries:
                return True
            
            # 无国家用户：只有创建者可以查看
            if not user_country:
                if target_user.get('created_by') == current_user_id:
                    return True
            
            return False
        
        return True
    
    # ========== 管理员逻辑 ==========
    if current_role == 'admin':
        allowed_countries = get_allowed_countries()

        # ✅ 修复：如果 allowed_countries 为空列表，无权查看任何用户
        if allowed_countries is None:
            # 无权限范围，使用用户注册国家
            user_session_country = session.get('user_country')
            user_country = target_user.get('country')
            if user_country and user_country == user_session_country:
                return True
            return False
   
        if allowed_countries:
            user_country = target_user.get('country')
            if user_country:
                if user_country in allowed_countries:
                    return True
            else:
                # 无国家用户：只有创建者可以查看
                if target_user.get('created_by') == current_user_id:
                    return True
        else:
            # 无权限范围，使用用户注册国家
            user_session_country = session.get('user_country')
            user_country = target_user.get('country')
            if user_country and user_country == user_session_country:
                return True
        
        return False
    
    return True


def _get_user_country(user_id):
    """辅助函数：获取指定用户的国家（带缓存）"""
    if not user_id:
        return None
    
    # 尝试从缓存获取
    if hasattr(_get_user_country, 'cache') and user_id in _get_user_country.cache:
        return _get_user_country.cache[user_id]
    
    # 从数据库查询
    try:
        from services.db import get_supabase
        db = get_supabase()
        res = db.table("users").select("country").eq("id", user_id).maybe_single().execute()
        country = res.data.get('country') if res.data else None
        
        # 缓存结果
        if not hasattr(_get_user_country, 'cache'):
            _get_user_country.cache = {}
        _get_user_country.cache[user_id] = country
        
        return country
    except Exception as e:
        logging.error(f"获取用户 {user_id} 国家失败: {e}")
        return None

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
# utils/permissions.py

def filter_users_by_permission(users, allowed_countries=None, current_user_id=None):
    """
    根据权限过滤用户列表，并自动添加创建人姓名
    支持：管理员可以看到同级管理员（权限范围有交集）
    
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
    
    # 获取当前用户角色
    current_role = session.get('role')
    is_dev = is_developer()
    
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
    
    # 过滤用户
    filtered_users = []
    
    for user in users:
        user_role = user.get('role', 'user')
        user_id = user.get('id')
        user_country = user.get('country') or ''
        user_status = user.get('user_status', '')
        created_by = user.get('created_by')
        user_admin_countries = user.get('admin_countries')
        user_name = user.get('name_en', '')
        
        # 解析目标用户的权限范围
        target_admin_country_list = []
        if user_admin_countries:
            try:
                target_admin_country_list = json.loads(user_admin_countries) if isinstance(user_admin_countries, str) else user_admin_countries
            except:
                pass
        
        # ========== 开发者逻辑 ==========
        if is_dev:
            user['created_by_name'] = creator_name_map.get(created_by, '')
            filtered_users.append(user)
            continue
        
        # ========== 超管逻辑 ==========
        if current_role == 'super_admin':
            # 超管看不到开发者
            if user_role == 'developer':
                continue
            
            # 无权限范围限制，可以看到所有非开发者
            if allowed_countries is None:
                user['created_by_name'] = creator_name_map.get(created_by, '')
                filtered_users.append(user)
                continue
            
            # 有权限范围限制
            if allowed_countries:
                # 目标用户是超管或管理员：检查 admin_countries 交集
                if user_role in ['super_admin', 'admin']:
                    if target_admin_country_list:
                        if any(c in allowed_countries for c in target_admin_country_list):
                            user['created_by_name'] = creator_name_map.get(created_by, '')
                            filtered_users.append(user)
                            continue
                    # 目标超管没有设置权限范围，视为全局，可见
                    elif user_role == 'super_admin' and not target_admin_country_list:
                        user['created_by_name'] = creator_name_map.get(created_by, '')
                        filtered_users.append(user)
                        continue
                    continue
                
                # 目标用户是普通用户：检查 country
                if user_role == 'user':
                    if user_country and user_country in allowed_countries:
                        user['created_by_name'] = creator_name_map.get(created_by, '')
                        filtered_users.append(user)
                        continue
                continue
            continue
        
        # ========== ✅ 管理员逻辑（核心修复） ==========
        if current_role == 'admin':
            # 管理员看不到超管和开发者
            if user_role in ['super_admin', 'developer']:
                continue
            
            # 当前管理员的权限范围
            current_allowed = allowed_countries if allowed_countries else [session.get('user_country')]
            if not current_allowed:
                continue
            
            # 1. 如果是自己，允许
            if user_id == current_user_id:
                user['created_by_name'] = creator_name_map.get(created_by, '')
                filtered_users.append(user)
                continue
            
            # ============================================================
            # ✅ 2. 目标用户是管理员：检查 admin_countries 是否有交集
            # ============================================================
            if user_role == 'admin':
                # 如果目标管理员有权限范围，检查交集
                if target_admin_country_list:
                    if any(c in current_allowed for c in target_admin_country_list):
                        user['created_by_name'] = creator_name_map.get(created_by, '')
                        filtered_users.append(user)
                        logger.debug(f"管理员看到同国家管理员: {user_name}")
                        continue
                # 如果目标管理员没有设置权限范围，检查 country
                elif user_country and user_country in current_allowed:
                    user['created_by_name'] = creator_name_map.get(created_by, '')
                    filtered_users.append(user)
                    logger.debug(f"管理员看到同国家管理员(无权限范围): {user_name}")
                    continue
                # 不同权限范围的管理员不可见
                continue
            
            # 3. 目标用户是普通用户：检查 country
            if user_role == 'user':
                # 已注册用户
                if user_status == 'registered':
                    if user_country and user_country in current_allowed:
                        user['created_by_name'] = creator_name_map.get(created_by, '')
                        filtered_users.append(user)
                        continue
                # 已导入用户：按创建者权限过滤
                elif user_status == 'imported':
                    if created_by == current_user_id:
                        user['created_by_name'] = creator_name_map.get(created_by, '')
                        filtered_users.append(user)
                        continue
                    creator = creator_name_map.get(created_by, '')
                    # 检查创建者的国家是否在权限范围内
                    if creator:
                        # 获取创建者的国家
                        try:
                            db = get_supabase()
                            creator_res = db.table("users").select("country").eq("id", created_by).maybe_single().execute()
                            if creator_res.data:
                                creator_country = creator_res.data.get('country', '')
                                if creator_country and creator_country in current_allowed:
                                    user['created_by_name'] = creator_name_map.get(created_by, '')
                                    filtered_users.append(user)
                                    continue
                        except:
                            pass
                continue
            
            continue
        
        # ========== 普通用户：只能看到自己 ==========
        if user_id == current_user_id:
            user['created_by_name'] = creator_name_map.get(created_by, '')
            filtered_users.append(user)
    
    return filtered_users

def _can_view_user_with_allowed(target_user, allowed_countries, current_user_id):
    """内部函数：使用指定的权限范围检查用户可见性"""
    current_role = session.get('role')
    dev_id = os.environ.get('DEVELOPER_USER_ID', '')
    
    # 开发者可以查看所有用户
    if dev_id and current_user_id == dev_id:
        return True
    
    # 如果是自己，总是可以查看
    if target_user.get('id') == current_user_id:
        return True
    
    # 不能查看受保护账号（除非是本人）
    if target_user.get('is_protected') and target_user.get('id') != current_user_id:
        return False
    
    target_role = target_user.get('role', 'user')
    target_country = target_user.get('country')
    target_admin_countries = target_user.get('admin_countries')
    
    # ========== 解析目标用户的权限范围 ==========
    target_admin_country_list = []
    if target_admin_countries:
        try:
            target_admin_country_list = json.loads(target_admin_countries) if isinstance(target_admin_countries, str) else target_admin_countries
        except:
            pass
    
    # ========== 当前用户是超管 ==========
    if current_role == 'super_admin':
        # 超管看不到开发者
        if target_role == 'developer':
            return False
        
        # 如果当前超管没有权限范围限制，可以看到所有非开发者
        if allowed_countries is None:
            return True
        
        # 如果有权限范围限制，检查是否有交集
        if allowed_countries:
            # 目标用户是超管或管理员：检查 admin_countries 交集
            if target_role in ['super_admin', 'admin']:
                # 如果有共同的权限范围，可见
                if target_admin_country_list:
                    if any(c in allowed_countries for c in target_admin_country_list):
                        return True
                # 如果目标超管没有设置权限范围，视为全局，可见
                elif target_role == 'super_admin' and not target_admin_country_list:
                    return True
                return False
            
            # 目标用户是普通用户：检查 country 是否在权限范围内
            if target_role == 'user':
                return target_country in allowed_countries if target_country else False
        
        return False
    
    # ========== 当前用户是管理员 ==========
    if current_role == 'admin':
        # 管理员看不到超管和开发者
        if target_role in ['super_admin', 'developer']:
            return False
        
        # 如果当前管理员没有权限范围限制，使用自己的国家
        current_allowed = allowed_countries if allowed_countries else [session.get('user_country')]
        if not current_allowed:
            return False
        
        # 目标用户是管理员：检查 admin_countries 交集
        if target_role == 'admin':
            if target_admin_country_list:
                # 检查是否有共同的权限范围
                if any(c in current_allowed for c in target_admin_country_list):
                    return True
            # 如果目标管理员没有设置权限范围，检查 country
            elif target_country and target_country in current_allowed:
                return True
            return False
        
        # 目标用户是普通用户：检查 country 是否在权限范围内
        if target_role == 'user':
            return target_country in current_allowed if target_country else False
        
        return False
    
    # ========== 普通用户：只能看到自己 ==========
    return target_user.get('id') == current_user_id

def is_active_user(user):
    """
    判断用户是否处于活跃状态（未离职）
    返回: True=活跃用户, False=已离职
    """
    if not user:
        return False
    # 检查 is_resign 字段
    is_resign = user.get('is_resign', False)
    if isinstance(is_resign, str):
        is_resign = is_resign.upper() == 'Y' or is_resign.lower() == 'true'
    return not is_resign


def filter_active_users(users):
    """过滤出活跃用户（未离职）"""
    return [u for u in users if is_active_user(u)]


def exclude_resigned_users(query, table_alias='users'):
    """
    为 Supabase 查询添加离职过滤条件
    注意：由于 Supabase 查询限制，此函数主要用于提示，实际过滤建议在 Python 层进行
    """
    # Supabase 的布尔字段过滤
    return query.eq('is_resign', False)

# utils/permissions.py - 添加权限检查函数

def can_resign_user(target_user, current_user):
    """
    检查当前用户是否有权标记目标用户为离职
    """
    # 开发者可以操作任何人
    if is_developer():
        return True
    
    current_role = current_user.get('role', 'user')
    target_role = target_user.get('role', 'user')
    target_id = target_user.get('id')
    current_id = current_user.get('id')
    
    # 不能标记自己
    if target_id == current_id:
        return False
    
    # 不能标记受保护账号
    if target_user.get('is_protected'):
        return False
    
    # 超管不能标记开发者
    if target_role == 'developer':
        return False
    
    # 超管可以标记非开发者
    if current_role == 'super_admin':
        return True
    
    # 管理员只能标记普通用户
    if current_role == 'admin':
        return target_role == 'user'
    
    return False

def can_rehire_user(target_user, current_user):
    """检查当前用户是否有权复职目标用户"""
    # 逻辑与 can_resign_user 相同
    return can_resign_user(target_user, current_user)
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

def has_role(role):
    """检查当前用户是否具有指定角色或更高权限"""
    current_role = session.get('role', 'user')
    
    # 角色层级
    role_hierarchy = {
        'developer': 4,
        'super_admin': 3,
        'admin': 2,
        'user': 1
    }
    
    current_level = role_hierarchy.get(current_role, 0)
    target_level = role_hierarchy.get(role, 0)
    
    return current_level >= target_level

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


def admin_required_for_api(f):
    """API 管理员权限装饰器（开发者也可以访问）"""
    @wraps(f)
    def decorated(*args, **kwargs):
        role = session.get('role')
        if role not in ('admin', 'super_admin', 'developer'):
            return jsonify({"success": False, "message": "permission_denied"}), 403
        return f(*args, **kwargs)
    return decorated