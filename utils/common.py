# utils/common.py
from datetime import datetime, timezone, timedelta
import re
from services.db import get_supabase

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

def get_quarter_from_date(date_iso):
    """从 ISO 日期字符串提取季度，返回 'YYYYQN' 或 None"""
    if not date_iso:
        return None
    try:
        dt = datetime.fromisoformat(date_iso)
        quarter = (dt.month - 1) // 3 + 1
        return f"{dt.year}Q{quarter}"
    except:
        return None