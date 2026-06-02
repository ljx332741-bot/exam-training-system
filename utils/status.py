# utils/status.py
from datetime import datetime, timezone

def get_exam_status(exam, has_started=False):
    """
    根据考试记录计算当前状态
    参数:
        exam: 考试记录
        has_started: 是否有考生已经开始考试（用于判断是否强制保持进行中状态）
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

    # ✅ 关键修改：如果有考生已经开始考试，即使有效期已过，也返回 active
    if has_started and now > end_dt:
        return 'active'  # 保持进行中状态
        
    # 4. 比较时间
    if now < start_dt:
        return 'created'
    elif now > end_dt:
        return 'closed'
    else:
        return 'active'