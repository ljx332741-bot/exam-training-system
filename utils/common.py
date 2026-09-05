# utils/common.py
import re
import os
import json
import pytz
from services.db import get_supabase
from datetime import datetime, timezone, timedelta

import logging
logger = logging.getLogger(__name__)

def match_country_code(input_text):
    """将用户输入的国家文本（中文、英文、代码）匹配为标准国家代码"""
    if not input_text:
        return None
    db = get_supabase()
    countries = db.table("countries").select("code, name_zh, name_en").execute().data or []
    input_lower = input_text.strip().lower()
    for c in countries:
        if (c['code'].lower() == input_lower or
            c['name_zh'].lower() == input_lower or
            c['name_en'].lower() == input_lower):
            return c['code']
    return None

def quarter_to_date_range(quarter_str):
    """
    将季度字符串（如 "2025Q1" 或 "25Q1"）转换为起始和结束 UTC 日期时间。
    返回 (start_iso, end_iso)
    """
    if not quarter_str:
        return None, None
    quarter_str = quarter_str.strip().upper()
    # 处理简写 "25Q1" -> "2025Q1"
    m = re.match(r'^(\d{2})(Q[1-4])$', quarter_str)
    if m:
        year_prefix = int(m.group(1))
        full_year = (2000 + year_prefix) if year_prefix < 30 else (1900 + year_prefix)
        quarter_str = f"{full_year}{m.group(2)}"
    # 标准格式 "2025Q1"
    if not (len(quarter_str) == 6 and quarter_str[4] == 'Q'):
        return None, None
    year = int(quarter_str[:4])
    q = int(quarter_str[5])
    if q < 1 or q > 4:
        return None, None
    start_month = (q - 1) * 3 + 1
    end_month = start_month + 2
    # 起始日期：季度第一天 00:00:00 UTC
    start_date = datetime(year, start_month, 1, 0, 0, 0, tzinfo=timezone.utc)
    # 结束日期：季度最后一天 23:59:59.999999 UTC
    if end_month == 12:
        end_date = datetime(year, 12, 31, 23, 59, 59, 999999, tzinfo=timezone.utc)
    else:
        end_date = datetime(year, end_month + 1, 1, 0, 0, 0, tzinfo=timezone.utc) - timedelta(microseconds=1)
    return start_date.isoformat(), end_date.isoformat()

def get_quarter_from_date(date_str):
    """从日期字符串提取季度（格式：2026Q2）"""
    if not date_str:
        return ''
    try:
        import re
        
        # 如果已经是 datetime 对象
        if hasattr(date_str, 'year'):
            date = date_str
        else:
            # 处理各种日期格式
            date_str = str(date_str)
            
            # 1. 处理 ISO 格式: 2026-09-04T10:30:00+00:00 或 2026-09-04T10:30:00Z
            if 'T' in date_str:
                # 移除时区信息
                date_str = re.sub(r'[+-]\d{2}:\d{2}$', '', date_str)
                date_str = date_str.replace('Z', '')
                date_str = date_str.split('T')[0]
            elif ' ' in date_str:
                # 处理 "2026-09-04 10:30:00" 格式
                date_str = date_str.split(' ')[0]
            
            # 2. 尝试解析
            try:
                date = datetime.fromisoformat(date_str)
            except ValueError:
                # 3. 尝试其他常见格式
                for fmt in ['%Y-%m-%d', '%Y/%m/%d', '%d-%m-%Y', '%d/%m/%Y']:
                    try:
                        date = datetime.strptime(date_str, fmt)
                        break
                    except ValueError:
                        continue
                else:
                    # 所有格式都失败
                    print(f"⚠️ 无法解析日期: {date_str}")
                    return ''
        
        year = date.year
        month = date.month
        quarter = (month - 1) // 3 + 1
        return f"{year}Q{quarter}"
        
    except Exception as e:
        print(f"⚠️ get_quarter_from_date 解析失败: {date_str}, 错误: {e}")
        return '-'

# 阅卷人配置文件路径（项目根目录）
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REVIEWER_CONFIG_FILE = os.path.join(PROJECT_ROOT, 'reviewers.json')

def load_reviewers_config():
    """加载阅卷人配置文件"""
    if os.path.exists(REVIEWER_CONFIG_FILE):
        try:
            with open(REVIEWER_CONFIG_FILE, 'r', encoding='utf-8') as f:
                config = json.load(f)
                logger.info(f"[阅卷人] 配置文件加载成功: {list(config.keys())}")
                return config
        except (json.JSONDecodeError, IOError) as e:
            logger.info(f"加载阅卷人配置文件失败: {e}")
            return {}
    else:
        logger.info(f"[DEBUG] 文件不存在: {REVIEWER_CONFIG_FILE}")
    return {}

def get_reviewer_by_country(user_country=None, exam_reviewer=None, url_reviewer=None):
    """
    获取阅卷人（多级优先级）
    
    优先级:
    1. URL 参数 reviewer (前端传递)
    2. 考试表中的 reviewer 字段 (管理员推送时设置)
    3. reviewers.json 中匹配考生国家的配置
    4. reviewers.json 中的 default 配置
    5. 环境变量 DEFAULT_REVIEWER
    6. 硬编码默认值 "管理员"
    
    参数:
        user_country: 考生国家代码 (如 "NP", "LK")
        exam_reviewer: 考试表中存储的阅卷人
        url_reviewer: URL 参数传递的阅卷人
    
    返回:
        str: 阅卷人字符串
    """
    def is_valid(value):
        return value and value.strip() and value != "None"

    # 优先级1：URL 参数
    if is_valid(url_reviewer):
        return url_reviewer.strip()
    
    # 优先级2：考试表中的 reviewer
    if is_valid(exam_reviewer):
        return exam_reviewer.strip()

    # 优先级3：从配置文件读取
    config = load_reviewers_config()

    if config:
        # 匹配考生国家
        if user_country and user_country in config:
            result = config[user_country]
            return config[user_country]
        # 默认配置
        if 'default' in config:
            result = config['default']
            return result
    else:
        logger.warning(f"[阅卷人] 配置文件为空或不存在")
    
    # 优先级4：环境变量
    env_reviewer = os.environ.get('DEFAULT_REVIEWER')
    if env_reviewer:
        return env_reviewer
    
    # 优先级5：硬编码默认值
    return "Administrator"

def format_admin_countries_display(admin_countries_json):
    """
    将 admin_countries JSON 字符串格式化为可读的显示文本
    """
    if not admin_countries_json:
        return '无限制'
    
    try:
        # 解析 JSON
        if isinstance(admin_countries_json, str):
            country_codes = json.loads(admin_countries_json)
        else:
            country_codes = admin_countries_json
        
        if not country_codes or len(country_codes) == 0:
            return '无限制'
        
        # 获取国家名称映射
        db = get_supabase()
        countries_res = db.table("countries").select("code, name_zh, name_en").execute()
        country_map = {c['code']: c for c in (countries_res.data or [])}
        
        # 转换为显示名称（优先使用中文名）
        display_names = []
        for code in country_codes:
            if code in country_map:
                # 可以根据需要选择语言
                name = country_map[code].get('name_zh') or country_map[code].get('name_en')
                display_names.append(name or code)
            else:
                display_names.append(code)
        
        return ', '.join(display_names)
        
    except (json.JSONDecodeError, TypeError, Exception):
        return '无限制'

def format_countries_display(countries_data, use_name=False, lang='zh'):
    """
    格式化考试目标国家列表用于显示
    支持多国家 JSON 数组格式
    
    Args:
        countries_data: 可以是以下格式:
            - JSON 字符串: '["NP", "LK"]'
            - Python 列表: ["NP", "LK"]
            - 单个国家字符串: "NP"
            - None 或 空值
        use_name: 是否显示国家名称（而非代码），默认 False 显示代码
        lang: 语言 'zh' 或 'en'（仅当 use_name=True 时生效）
    
    Returns:
        str: 格式化后的显示字符串
    """
    if not countries_data:
        return '-'
    
    # 解析为国家代码列表
    country_codes = []
    
    if isinstance(countries_data, str):
        # 尝试解析 JSON
        try:
            parsed = json.loads(countries_data)
            if isinstance(parsed, list):
                country_codes = [c for c in parsed if c and c.strip()]
            elif isinstance(parsed, str) and parsed.strip():
                country_codes = [parsed.strip()]
        except json.JSONDecodeError:
            # 不是 JSON，可能是单个国家或逗号分隔
            if ',' in countries_data:
                country_codes = [c.strip() for c in countries_data.split(',') if c.strip()]
            elif countries_data.strip():
                country_codes = [countries_data.strip()]
    elif isinstance(countries_data, list):
        country_codes = [c for c in countries_data if c and c.strip()]
    else:
        return str(countries_data) if countries_data else '-'
    
    if not country_codes:
        return '-'
    
    # 如果需要显示名称
    if use_name:
        try:
            db = get_supabase()
            res = db.table("countries").select("code, name_zh, name_en").execute()
            country_map = {c['code']: c for c in (res.data or [])}
            
            names = []
            for code in country_codes:
                if code in country_map:
                    name = country_map[code].get(f'name_{lang}') or country_map[code].get('name_zh') or code
                    names.append(name)
                else:
                    names.append(code)
            
            if len(names) == 1:
                return names[0]
            return ', '.join(names)
        except Exception as e:
            logger.warning(f"获取国家名称失败: {e}")
            return ', '.join(country_codes)
    
    # 显示代码
    if len(country_codes) == 1:
        return country_codes[0]
    return ', '.join(country_codes)

def format_single_country_display(country_code, lang='zh'):
    """
    格式化单个国家显示（兼容旧数据）
    
    Args:
        country_code: 国家代码字符串
        lang: 语言 'zh' 或 'en'
    
    Returns:
        str: 格式化后的显示字符串
    """
    if not country_code:
        return '-'
    
    # 如果是 JSON 数组格式，调用上面的函数处理
    if country_code.startswith('['):
        return format_countries_display(country_code, use_name=True, lang=lang)
    
    try:
        db = get_supabase()
        res = db.table("countries").select(f"name_{lang}, name_zh, name_en").eq("code", country_code).maybe_single().execute()
        if res.data:
            name = res.data.get(f'name_{lang}') or res.data.get('name_zh') or country_code
            return name
        return country_code
    except Exception:
        return country_code

def utc_to_local(utc_string, timezone_str=None):
    """
    将 UTC 时间字符串转换为本地时间字符串
    如果不指定时区，使用用户 session 中的时区
    """
    if not utc_string:
        return ''
    
    # 如果没有指定时区，从 session 获取
    if timezone_str is None:
        from flask import session
        timezone_str = session.get('user_timezone', 'UTC')
    
    try:
        if utc_string.endswith('Z'):
            s = utc_string.replace('Z', '+00:00')
        else:
            s = utc_string
        dt = datetime.fromisoformat(s)
        
        if dt.tzinfo is None:
            dt = pytz.UTC.localize(dt)
        
        local_tz = pytz.timezone(timezone_str)
        return dt.astimezone(local_tz).strftime('%Y-%m-%d %H:%M')
    except Exception as e:
        return utc_string

def format_datetime_local(utc_string, timezone_str='Asia/Shanghai'):
    """格式化本地时间（与 utc_to_local 相同）"""
    return utc_to_local(utc_string, timezone_str)
