# utils/employment_history.py
from datetime import datetime, timezone
from services.db import get_supabase, get_supabase_admin
import logging

logger = logging.getLogger(__name__)


def add_employment_event(user_id: str, event_type: str, created_by: str = None, notes: str = None) -> bool:
    """
    添加离职/复职历史记录
    
    Args:
        user_id: 用户ID
        event_type: 'resign' 或 'rehire'
        created_by: 操作人ID
        notes: 备注
    """
    try:
        db = get_supabase_admin()
        now = datetime.now(timezone.utc).isoformat()
        
        db.table("user_employment_history").insert({
            "user_id": user_id,
            "event_type": event_type,
            "event_at": now,
            "created_at": now,
            "created_by": created_by,
            "notes": notes
        }).execute()
        
        logger.info(f"📝 添加就业事件: user={user_id}, event={event_type}")
        return True
        
    except Exception as e:
        logger.error(f"添加就业事件失败: {e}")
        return False


def get_employment_history(user_id: str, limit: int = None):
    """
    获取用户的离职/复职历史记录
    
    Args:
        user_id: 用户ID
        limit: 返回条数限制
    
    Returns:
        list: 历史记录列表，按时间倒序
    """
    try:
        db = get_supabase_admin()
        query = db.table("user_employment_history") \
            .select("*") \
            .eq("user_id", user_id) \
            .order("event_at", desc=True)
        
        if limit:
            query = query.limit(limit)
        
        result = query.execute()
        return result.data or []
        
    except Exception as e:
        logger.error(f"获取就业历史失败: {e}")
        return []


def get_latest_employment_status(user_id: str):
    """
    获取用户最新的就业状态（从历史记录推断）
    
    Returns:
        dict: {
            'is_resign': bool,  # 当前是否离职
            'is_rehire': bool,  # 是否曾复职
            'resigned_at': str,  # 最近离职时间
            'rehire_at': str,   # 最近复职时间
            'history_count': int  # 总记录数
        }
    """
    history = get_employment_history(user_id)
    
    if not history:
        return {
            'is_resign': False,
            'is_rehire': False,
            'resigned_at': None,
            'rehire_at': None,
            'history_count': 0
        }
    
    # 按时间倒序已排序，第一条是最新的
    latest = history[0]
    
    # 统计
    resign_count = sum(1 for h in history if h['event_type'] == 'resign')
    rehire_count = sum(1 for h in history if h['event_type'] == 'rehire')
    
    # 最新状态
    is_resign = latest['event_type'] == 'resign'
    
    # 最近离职时间（最新的 resign 事件）
    latest_resign = next((h for h in history if h['event_type'] == 'resign'), None)
    latest_rehire = next((h for h in history if h['event_type'] == 'rehire'), None)
    
    return {
        'is_resign': is_resign,
        'is_rehire': rehire_count > 0,
        'resigned_at': latest_resign['event_at'] if latest_resign else None,
        'rehire_at': latest_rehire['event_at'] if latest_rehire else None,
        'history_count': len(history),
        'resign_count': resign_count,
        'rehire_count': rehire_count,
        'latest_event': latest
    }


def get_employment_summary(user_id: str):
    """
    获取用户就业摘要（用于前端显示）
    """
    status = get_latest_employment_status(user_id)
    history = get_employment_history(user_id)
    
    # 构建轨迹描述
    trajectory = []
    for h in history:
        event_zh = '离职' if h['event_type'] == 'resign' else '复职'
        trajectory.append(f"{event_zh}({h['event_at'][:10]})")
    
    return {
        'current_status': '离职' if status['is_resign'] else '在职',
        'is_rehire': status['is_rehire'],
        'resign_count': status.get('resign_count', 0),
        'rehire_count': status.get('rehire_count', 0),
        'latest_resign_at': status.get('resigned_at'),
        'latest_rehire_at': status.get('rehire_at'),
        'trajectory': ' → '.join(trajectory) if trajectory else '无记录'
    }