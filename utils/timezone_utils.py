# utils/timezone_utils.py
"""
统一时区处理工具
- 所有时间存储使用 UTC
- 所有时间显示转换为用户本地时区
"""

import pytz
from datetime import datetime, timezone
from flask import session, request
import logging

logger = logging.getLogger(__name__)

# 默认时区（当无法获取用户时区时使用）
DEFAULT_TIMEZONE = 'Asia/Shanghai'


def get_user_timezone():
    """
    获取当前用户的时区
    优先级：
    1. session 中存储的用户时区
    2. 请求头中的 Timezone
    3. 浏览器 accept-language 推断（简化）
    4. 默认时区
    """
    # 1. 从 session 获取
    user_tz = session.get('user_timezone')
    if user_tz:
        return user_tz
    
    # 2. 从请求头获取
    user_tz = request.headers.get('X-Timezone')
    if user_tz:
        try:
            pytz.timezone(user_tz)
            session['user_timezone'] = user_tz
            return user_tz
        except:
            pass
    
    # 3. 从浏览器语言推断（简化版）
    accept_language = request.headers.get('Accept-Language', '')
    if 'zh-CN' in accept_language or 'zh' in accept_language:
        # 中国用户默认北京时间
        return 'Asia/Shanghai'
    elif 'en-US' in accept_language or 'en' in accept_language:
        # 美国用户默认纽约时间
        return 'America/New_York'
    
    # 4. 返回默认时区
    return DEFAULT_TIMEZONE


def set_user_timezone(timezone_str):
    """设置用户的时区（由前端调用）"""
    try:
        pytz.timezone(timezone_str)
        session['user_timezone'] = timezone_str
        return True
    except Exception as e:
        logger.error(f"设置时区失败: {e}")
        return False


def utc_to_local(dt, user_timezone=None):
    """
    将 UTC datetime 对象转换为用户本地时间
    
    Args:
        dt: datetime 对象（UTC 时区）
        user_timezone: 用户时区，不传则自动获取
    
    Returns:
        带用户时区的 datetime 对象
    """
    if dt is None:
        return None
    
    # 确保 dt 有时区信息
    if dt.tzinfo is None:
        dt = pytz.UTC.localize(dt)
    
    # 获取用户时区
    tz_str = user_timezone or get_user_timezone()
    try:
        local_tz = pytz.timezone(tz_str)
        return dt.astimezone(local_tz)
    except Exception as e:
        logger.error(f"时区转换失败: {e}")
        return dt


def utc_string_to_local(utc_string, user_timezone=None, format_str='%Y-%m-%d %H:%M:%S'):
    """
    将 UTC 时间字符串转换为用户本地时间字符串
    
    Args:
        utc_string: UTC 时间字符串，如 "2026-05-29T18:34:04.997391+00:00"
        user_timezone: 用户时区
        format_str: 输出格式，默认 '%Y-%m-%d %H:%M:%S'
    
    Returns:
        格式化后的本地时间字符串
    """
    if not utc_string:
        return ''
    
    try:
        # 解析时间字符串
        if utc_string.endswith('Z'):
            s = utc_string.replace('Z', '+00:00')
        else:
            s = utc_string
        
        dt = datetime.fromisoformat(s)
        
        # 转换为本地时间
        local_dt = utc_to_local(dt, user_timezone)
        
        return local_dt.strftime(format_str)
    except Exception as e:
        logger.error(f"时间转换失败: {utc_string}, {e}")
        return utc_string


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
    