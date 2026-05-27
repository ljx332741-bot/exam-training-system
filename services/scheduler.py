# services/scheduler.py
import logging
from datetime import datetime, timezone
from apscheduler.schedulers.background import BackgroundScheduler
from services.db import get_supabase
from services import exam

logger = logging.getLogger(__name__)

scheduler = BackgroundScheduler()

def auto_submit_timeout_exams(app):
    with app.app_context():
        db = get_supabase()
        now = datetime.now(timezone.utc)
        status_res = db.table("user_exam_status").select("*").eq("is_submitted", False).execute()
        if not status_res.data: return
        for status in status_res.data:
            user_id = status['user_id']
            exam_id = status['exam_id']
            started_at_str = status.get('started_at')
            if not started_at_str: continue
            exam_info = db.table("exams").select("duration").eq("id", exam_id).maybe_single().execute()
            duration_minutes = exam_info.data.get("duration", 60) if exam_info.data else 60
            total_seconds = duration_minutes * 60
            try:
                start_dt = datetime.fromisoformat(started_at_str.replace('Z', '+00:00'))
                elapsed = (now - start_dt).total_seconds()
            except: continue
            if elapsed < total_seconds: continue
            
            logger.info(f"⏰ 检测到超时考试：用户 {user_id}，考试 {exam_id}，超时 {int(elapsed - total_seconds)} 秒")
            answers = {}
            draft_res = db.table("user_exam_drafts").select("answers").eq("user_id", user_id).eq("exam_id", exam_id).maybe_single().execute()
            if draft_res and draft_res.data and isinstance(draft_res.data, dict) and draft_res.data.get('answers'):
                raw = draft_res.data['answers']
                try: answers = raw if isinstance(raw, dict) else __import__('json').loads(raw)
                except: pass
            
            try:
                grade = exam.auto_grade(answers, exam_id)
                exam.save_result(user_id, exam_id, answers, grade['total'], grade['details'], {})
                existing = db.table("user_exam_status").select("id").eq("user_id", user_id).eq("exam_id", exam_id).maybe_single().execute()
                update_data = {"is_submitted": True, "submitted_at": now.isoformat(), "reset_at": None}
                if existing and existing.data:
                    db.table("user_exam_status").update(update_data).eq("id", existing.data['id']).execute()
                else:
                    update_data.update({"user_id": user_id, "exam_id": exam_id, "started_at": started_at_str})
                    db.table("user_exam_status").insert(update_data).execute()
                db.table("user_exam_drafts").delete().eq("user_id", user_id).eq("exam_id", exam_id).execute()
            except Exception as e:
                logger.error(f"自动提交失败: {e}", exc_info=True)

def init_scheduler(app):
    from config import Config
    scan_interval = int(__import__('os').environ.get('EXAM_SCAN_INTERVAL', 60))
    scheduler.add_job(func=auto_submit_timeout_exams, trigger="interval", seconds=scan_interval, args=[app])
    scheduler.start()