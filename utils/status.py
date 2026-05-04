# utils/status.py
from datetime import datetime, timezone

def get_exam_status(exam):
    """
    根据考试记录计算当前状态
    返回: 'draft', 'created', 'active', 'closed', 'deleted'
    """
    # 1. 软删除优先
    if exam.get('deleted_at'):
        return 'deleted'
    # 2. 没有 start_time 或 end_time 视为草稿
    start_time = exam.get('start_time')
    end_time = exam.get('end_time')
    if not start_time or not end_time:
        return 'draft'
    # 3. 解析时间
    now = datetime.now(timezone.utc)
    start_dt = datetime.fromisoformat(start_time)
    end_dt = datetime.fromisoformat(end_time)
    if now < start_dt:
        return 'created'
    elif now > end_dt:
        return 'closed'
    else:
        return 'active'