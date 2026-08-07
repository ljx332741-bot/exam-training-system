# utils/timezone_utils.py - 完整修复版

"""
统一时区处理工具
- 所有时间存储使用 UTC
- 所有时间显示转换为用户本地时区
"""

import pytz, re
from datetime import datetime, timezone
from flask import session, request, g
import logging

logger = logging.getLogger(__name__)

def _parse_isostring(s):
    """
    健壮地解析 ISO 时间字符串
    支持多种格式：
    - 2026-08-07T06:01:17.18458+00:00 (5位微秒)
    - 2026-08-07T06:01:17.184580+00:00 (6位微秒)
    - 2026-08-07T06:01:17+00:00 (无微秒)
    - 2026-08-07T06:01:17.18458Z (Z结尾)
    """
    if not s:
        return None
    
    s = s.strip()
    
    # 处理 Z 结尾
    if s.endswith('Z'):
        s = s.replace('Z', '+00:00')
    
    # 匹配并修复微秒格式
    # 分组: (日期时间) . (微秒) (时区)
    match = re.match(
        r'^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})\.(\d+)([+-]\d{2}:\d{2})?$',
        s
    )
    if match:
        base, microseconds, tz = match.groups()
        # 确保微秒是6位（补零或截断）
        if len(microseconds) < 6:
            microseconds = microseconds.ljust(6, '0')
        elif len(microseconds) > 6:
            microseconds = microseconds[:6]
        tz_suffix = tz or '+00:00'
        s = f"{base}.{microseconds}{tz_suffix}"
    
    # 如果没有微秒部分，直接返回
    return datetime.fromisoformat(s)

def get_user_timezone():
    """
    获取当前用户的时区
    优先级：
    1. g 对象中的时区（当前请求）
    2. session 中存储的用户时区
    3. 请求头中的 X-Timezone
    4. Cookie 中的 user_timezone
    5. 返回 None（表示使用 UTC，不做转换）
    """
    # 1. 从 g 对象获取（当前请求）
    if hasattr(g, 'user_timezone') and g.user_timezone:
        return g.user_timezone
    
    # 2. 从 session 获取
    user_tz = session.get('user_timezone')
    if user_tz:
        try:
            pytz.timezone(user_tz)
            return user_tz
        except:
            pass
    
    # 3. 从请求头获取
    user_tz = request.headers.get('X-Timezone')
    if user_tz:
        try:
            pytz.timezone(user_tz)
            session['user_timezone'] = user_tz
            return user_tz
        except:
            pass
    
    # 4. 从 Cookie 获取
    user_tz = request.cookies.get('user_timezone')
    if user_tz:
        try:
            pytz.timezone(user_tz)
            session['user_timezone'] = user_tz
            return user_tz
        except:
            pass
    
    # 5. 没有任何时区信息时，返回 'UTC'
    # 这样所有时间会以 UTC 显示，前端再转换为浏览器时区
    return 'UTC'


def set_user_timezone(timezone_str):
    """设置用户的时区（由前端调用）"""
    try:
        pytz.timezone(timezone_str)
        session['user_timezone'] = timezone_str
        g.user_timezone = timezone_str  # 同时设置 g 对象
        logger.info(f"用户时区已设置: {timezone_str}")
        return True
    except Exception as e:
        logger.error(f"设置时区失败: {e}")
        return False


def utc_to_local(dt, user_timezone=None):
    """
    将 UTC datetime 对象转换为用户本地时间
    """
    if dt is None:
        return None
    
    # 确保 dt 有时区信息
    if dt.tzinfo is None:
        dt = pytz.UTC.localize(dt)
    
    # 获取用户时区
    tz_str = user_timezone or get_user_timezone()
    
    # 如果是 UTC，不需要转换
    if tz_str == 'UTC':
        return dt
    
    try:
        local_tz = pytz.timezone(tz_str)
        return dt.astimezone(local_tz)
    except Exception as e:
        logger.error(f"时区转换失败: {e}")
        return dt


def utc_string_to_local(utc_string, user_timezone=None, format_str='%Y-%m-%d %H:%M:%S'):
    """将 UTC 时间字符串转换为用户本地时间字符串"""
    if not utc_string:
        return ''
    
    try:
        # ✅ 使用 _parse_isostring 解析时间
        dt = _parse_isostring(utc_string)
        if dt is None:
            return utc_string
        
        # 转换为本地时间
        local_dt = utc_to_local(dt, user_timezone)
        
        return local_dt.strftime(format_str)
    except Exception as e:
        logger.error(f"时间转换失败: {utc_string}, {e}")
        # 降级方案：简单字符串处理
        try:
            # 去掉微秒部分
            if '.' in utc_string:
                parts = utc_string.split('.')
                base = parts[0]
                if 'T' in base:
                    return base.replace('T', ' ')
                return base
            return utc_string[:19].replace('T', ' ') if 'T' in utc_string else utc_string[:19]
        except:
            return utc_string[:19] if utc_string else ''


def format_datetime(utc_string, user_timezone=None):
    """格式化日期时间（完整格式）"""
    return utc_string_to_local(utc_string, user_timezone, '%Y-%m-%d %H:%M:%S')


def format_date(utc_string, user_timezone=None):
    """格式化日期（仅年月日）"""
    return utc_string_to_local(utc_string, user_timezone, '%Y-%m-%d')


def format_time(utc_string, user_timezone=None):
    """格式化时间（仅时分秒）"""
    return utc_string_to_local(utc_string, user_timezone, '%H:%M:%S')


def get_current_local_time(user_timezone=None):
    """获取当前用户的本地时间"""
    now_utc = datetime.now(timezone.utc)
    return utc_to_local(now_utc, user_timezone)


def format_datetime_24h(utc_string, user_timezone=None):
    """格式化时间为24小时制：YYYY-MM-DD HH:MM:SS"""
    return utc_string_to_local(utc_string, user_timezone, '%Y-%m-%d %H:%M:%S')


def format_datetime_24h_short(utc_string, user_timezone=None):
    """格式化时间为24小时制（简洁版）：YYYY-MM-DD HH:MM"""
    return utc_string_to_local(utc_string, user_timezone, '%Y-%m-%d %H:%M')
