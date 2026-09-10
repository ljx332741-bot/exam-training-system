# utils/user_info_change_logs.py
import json
import logging
from datetime import datetime, timezone
from services.db import get_supabase_admin

logger = logging.getLogger(__name__)

TRACKED_FIELDS = [
    'email', 'name_en', 'name_cn', 'company', 'department',
    'employee_id', 'birthday', 'country', 'phone', 'role',
    'admin_countries', 'user_status', 'is_partner', 'wh_type',
    'wh_id', 'wh_name_en', 'is_resign', 'is_rehire', 'is_active',
    'resigned_at', 'rehire_at', 'remark'
]


def _normalize_value(field: str, value):
    """字段值归一化，避免格式差异导致误判变更"""
    if value is None:
        return None
    if isinstance(value, str) and value.strip() in ('', 'null', 'None', 'undefined'):
        return None

    if field == 'admin_countries':
        if isinstance(value, str):
            try:
                return json.dumps(json.loads(value), sort_keys=True)
            except Exception:
                return value.strip()
        if isinstance(value, (list, dict)):
            return json.dumps(value, sort_keys=True)
        return str(value)

    if field in ('resigned_at', 'rehire_at'):
        try:
            if hasattr(value, 'replace') and hasattr(value, 'isoformat'):
                dt = value
                if dt.tzinfo:
                    dt = dt.astimezone(timezone.utc)
                return dt.replace(microsecond=0).isoformat()
            s = str(value).strip()
            if 'T' in s:
                # 去掉微秒
                if '.' in s:
                    main, rest = s.split('.', 1)
                    if '+' in rest:
                        tz = rest[rest.index('+'):]
                    elif 'Z' in rest:
                        tz = 'Z'
                    else:
                        tz = ''
                    return main + tz
            return s
        except Exception:
            return str(value)

    if field == 'birthday':
        try:
            if hasattr(value, 'isoformat'):
                return value.isoformat()[:10]
            return str(value)[:10]
        except Exception:
            return str(value)

    if field in ('is_resign', 'is_rehire', 'is_active', 'is_partner'):
        if isinstance(value, str):
            return value.lower() in ('true', '1', 'y', 'yes')
        return bool(value)

    return str(value).strip()


def log_user_change(
    user_id: str,
    changed_by: str,
    field_name: str,
    old_value,
    new_value,
    operation_type: str = 'update',
    request_ip: str = None,
    user_agent: str = None
):
    """记录单条字段变更日志"""
    
    # ✅ 二次防御：归一化比较，无变化直接跳过
    old_norm = _normalize_value(field_name, old_value)
    new_norm = _normalize_value(field_name, new_value)
    
    if old_norm == new_norm:
        logger.debug(f"跳过无变化字段: user={user_id}, field={field_name}")
        return
    
    # 转为字符串存储
    old_str = None if old_norm is None else str(old_norm)
    new_str = None if new_norm is None else str(new_norm)

    db = get_supabase_admin()
    try:
        db.table("user_info_change_logs").insert({
            "user_id": user_id,
            "field_name": field_name,
            "old_value": old_str,
            "new_value": new_str,
            "changed_by": changed_by,
            "changed_at": datetime.now(timezone.utc).isoformat(),
            "operation_type": operation_type,
            "request_ip": request_ip,
            "user_agent": user_agent
        }).execute()
        logger.info(f"审计日志已记录: user={user_id}, field={field_name}, old={old_str}, new={new_str}")
    except Exception as e:
        logger.error(f"审计日志记录失败: user_id={user_id}, field={field_name}, error={e}", exc_info=True)


def log_user_update(user_id, changed_by, old_data, new_data, **kwargs):
    """比较两个用户对象，记录所有变更"""
    if not old_data or not new_data:
        logger.warning("log_user_update: old_data 或 new_data 为空，跳过记录")
        return

    change_count = 0
    for field in TRACKED_FIELDS:
        old_val = _normalize_value(field, old_data.get(field))
        new_val = _normalize_value(field, new_data.get(field))

        if old_val != new_val:
            log_user_change(
                user_id=user_id,
                changed_by=changed_by,
                field_name=field,
                old_value=old_val,
                new_value=new_val,
                **kwargs
            )
            change_count += 1

    if change_count == 0:
        logger.info(f"log_user_update: 用户 {user_id} 无字段变化，未记录日志")
    else:
        logger.info(f"log_user_update: 用户 {user_id} 共记录 {change_count} 条变更")