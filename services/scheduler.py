# services/scheduler.py
import os
import json
import atexit
import logging
from datetime import datetime, timezone
from services.db import get_supabase
from services import exam
from routes.helpers import safe_parse_datetime
from apscheduler.schedulers.background import BackgroundScheduler

logger = logging.getLogger(__name__)

def auto_submit_single_exam(db, user_id, exam_id, started_at_str, now):
    """执行单个考试的自动提交"""
    try:
        # 计算用时
        time_used = None
        if started_at_str:
            try:
                start_dt = safe_parse_datetime(started_at_str)
                if start_dt:
                    time_used = int((now - start_dt).total_seconds())
                    logger.info(f"自动提交 - 考试用时: {time_used} 秒")
            except Exception as e:
                logger.warning(f"计算用时失败: {e}")
        
        # 获取答案
        answers = {}
        draft_res = db.table("user_exam_drafts").select("answers").eq("user_id", user_id).eq("exam_id", exam_id).maybe_single().execute()
        if draft_res and draft_res.data:
            raw = draft_res.data.get('answers')
            if raw:
                try:
                    answers = json.loads(raw) if isinstance(raw, str) else raw
                except:
                    answers = {}
        
        # 评分
        grade = exam.auto_grade(answers, exam_id)
        
        # 保存成绩
        exam.save_result(
            user_id, exam_id, answers, grade['total'], grade['details'], {},
            submit_method='auto',
            time_used=time_used
        )
        
        # 更新状态
        existing = db.table("user_exam_status").select("id").eq("user_id", user_id).eq("exam_id", exam_id).maybe_single().execute()
        update_data = {
            "is_submitted": True,
            "submitted_at": now.isoformat(),
            "reset_at": None
        }
        if existing and existing.data:
            db.table("user_exam_status").update(update_data).eq("id", existing.data['id']).execute()
        else:
            update_data.update({
                "user_id": user_id,
                "exam_id": exam_id,
                "started_at": started_at_str
            })
            db.table("user_exam_status").insert(update_data).execute()
        
        # 清理草稿
        db.table("user_exam_drafts").delete().eq("user_id", user_id).eq("exam_id", exam_id).execute()
        
        logger.info(f"✅ 自动提交成功: user={user_id}, exam={exam_id}, score={grade['total']}, time_used={time_used}")
        return True
        
    except Exception as e:
        logger.error(f"❌ 自动提交失败: {e}", exc_info=True)
        return False

def auto_submit_timeout_exams(app):
    """自动提交超时考试（基于考试时长）"""
    with app.app_context():
        db = get_supabase()
        now = datetime.now(timezone.utc)
        
        # 获取所有已开始但未提交的考试
        status_res = db.table("user_exam_status") \
            .select("*") \
            .eq("is_submitted", False) \
            .not_.is_("started_at", "null") \
            .execute()
        
        if not status_res.data:
            return
        
        # 批量获取考试时长
        exam_ids = list(set([s['exam_id'] for s in status_res.data]))
        exam_durations = {}
        for exam_id in exam_ids:
            exam_res = db.table("exams").select("duration").eq("id", exam_id).maybe_single().execute()
            if exam_res.data:
                exam_durations[exam_id] = exam_res.data.get('duration', 60)
        
        for status in status_res.data:
            user_id = status['user_id']
            exam_id = status['exam_id']
            started_at_str = status.get('started_at')
            
            if not started_at_str:
                continue
            
            duration_minutes = exam_durations.get(exam_id, 60)
            total_seconds = duration_minutes * 60
            
            try:
                start_dt = safe_parse_datetime(started_at_str)
                if not start_dt:
                    continue
                
                elapsed = (now - start_dt).total_seconds()
                
                # ✅ 基于考试时长判断是否超时，而非有效期
                if elapsed >= total_seconds:
                    logger.info(f"⏰ 考试超时自动提交: user={user_id}, exam={exam_id}, 已用时={int(elapsed)}秒")
                    auto_submit_single_exam(db, user_id, exam_id, started_at_str, now)
                    
            except Exception as e:
                logger.error(f"处理考试记录失败: {e}")

def check_exam_end_time(exam_id, user_id, started_at_str):
    """
    检查考试有效期是否已过（仅用于通知，不强制提交）
    返回: (is_expired, end_time_str)
    """
    db = get_supabase()
    exam_res = db.table("exams").select("end_time").eq("id", exam_id).maybe_single().execute()
    
    if not exam_res.data or not exam_res.data.get('end_time'):
        return False, None
    
    end_time_str = exam_res.data['end_time']
    now = datetime.now(timezone.utc)
    
    try:
        end_dt = datetime.fromisoformat(end_time_str.replace('Z', '+00:00'))
        is_expired = now > end_dt
        
        if is_expired and started_at_str:
            # 考试已到期但考生正在进行，发送通知（可选）
            logger.warning(f"⚠️ 考试 {exam_id} 有效期已过，但考生 {user_id} 仍在进行中，"
                          f"将允许完成剩余考试时间")
        
        return is_expired, end_time_str
    except:
        return False, None

def init_scheduler(app):
    """初始化定时调度器"""
    scheduler = BackgroundScheduler()
    scan_interval = int(os.environ.get('EXAM_SCAN_INTERVAL', 60))
    
    # 添加定时任务
    scheduler.add_job(
        func=auto_submit_timeout_exams,
        trigger="interval",
        seconds=scan_interval,
        args=[app],
        id="auto_submit_exam_job",
        replace_existing=True
    )
    
    scheduler.start()
    logger.info(f"自动提交调度器已启动，扫描间隔: {scan_interval}秒")
    
    # 确保程序退出时关闭调度器
    atexit.register(lambda: scheduler.shutdown())
    
    return scheduler
