# services/scheduler.py
import os
import json
import atexit
import logging
import time
from functools import wraps
from datetime import datetime, timezone
from services.db import get_supabase, safe_table, retry_on_timeout, batch_query
from services import exam
from routes.helpers import safe_parse_datetime
from utils.training_helpers import is_draft_time, DRAFT_PLACEHOLDER
from apscheduler.schedulers.background import BackgroundScheduler
import httpx

logger = logging.getLogger(__name__)

# 全局调度器实例
_scheduler = None

# ============================================================
# 1. 重试装饰器（如果 db.py 已有，可直接导入）
# ============================================================
# ============================================================
# 2. 批量获取考试时长（优化版）
# ============================================================
def get_exam_durations_batch(db, exam_ids):
    """
    批量获取考试时长（使用优化查询）
    
    Args:
        db: 数据库连接（保留兼容）
        exam_ids: 考试ID列表
    
    Returns:
        dict: {exam_id: duration}
    """
    if not exam_ids:
        return {}
    
    try:
        # 使用安全查询
        result = safe_table('exams').select('id, duration').in_('id', exam_ids).execute()
        exam_durations = {}
        for row in (result.data or []):
            exam_durations[row['id']] = row.get('duration', 60)
        return exam_durations
    except Exception as e:
        logger.error(f"批量获取考试时长失败: {e}")
        return {}


# ============================================================
# 3. 自动提交单个考试（带重试）
# ============================================================
@retry_on_timeout(max_retries=3, delay=2)
def auto_submit_single_exam(db, user_id, exam_id, started_at_str, now):
    """执行单个考试的自动提交（带重试）"""
    try:
        time_used = None
        if started_at_str:
            try:
                start_dt = safe_parse_datetime(started_at_str)
                if start_dt:
                    time_used = int((now - start_dt).total_seconds())
                    logger.info(f"自动提交 - 考试用时: {time_used} 秒")
            except Exception as e:
                logger.warning(f"计算用时失败: {e}")
        
        # 正确的链式调用
        draft_res = safe_table('user_exam_drafts') \
            .select('answers') \
            .eq('user_id', user_id) \
            .eq('exam_id', exam_id) \
            .maybe_single() \
            .execute()
        
        answers = {}
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
        
        # 正确的链式调用
        existing = safe_table('user_exam_status') \
            .select('id') \
            .eq('user_id', user_id) \
            .eq('exam_id', exam_id) \
            .maybe_single() \
            .execute()
        
        update_data = {
            "is_submitted": True,
            "submitted_at": now.isoformat(),
            "reset_at": None
        }
        
        if existing and existing.data:
            safe_table('user_exam_status') \
                .update(update_data) \
                .eq('id', existing.data['id']) \
                .execute()
        else:
            update_data.update({
                "user_id": user_id,
                "exam_id": exam_id,
                "started_at": started_at_str
            })
            safe_table('user_exam_status').insert(update_data).execute()
        
        # 清理草稿
        safe_table('user_exam_drafts') \
            .delete() \
            .eq('user_id', user_id) \
            .eq('exam_id', exam_id) \
            .execute()
        
        logger.info(f"✅ 自动提交成功: user={user_id}, exam={exam_id}, score={grade['total']}, time_used={time_used}")
        return True
        
    except Exception as e:
        logger.error(f"❌ 自动提交失败: {e}", exc_info=True)
        return False

# ============================================================
# 4. 自动提交超时考试（带超时保护和分批处理）
# ============================================================
@retry_on_timeout(max_retries=2, delay=3)
def auto_submit_timeout_exams(app):
    """自动提交超时考试（基于考试时长）- 带超时保护"""
    with app.app_context():
        db = get_supabase()
        now = datetime.now(timezone.utc)
        
        logger.info("🔄 开始扫描超时考试...")
        
        try:
            # 正确的链式调用方式
            status_res = safe_table('user_exam_status', timeout=120) \
                .select('*') \
                .eq('is_submitted', False) \
                .not_.is_('started_at', 'null') \
                .execute()
            
            if not status_res or not status_res.data:
                logger.info("没有需要检查的进行中考试")
                return
            
            logger.info(f"找到 {len(status_res.data)} 个进行中的考试记录")
            
            # 批量获取考试时长
            exam_ids = list(set([s['exam_id'] for s in status_res.data]))
            exam_durations = get_exam_durations_batch(db, exam_ids)
            
            submitted_count = 0
            failed_count = 0
            
            BATCH_SIZE = 20
            status_list = status_res.data
            
            for i in range(0, len(status_list), BATCH_SIZE):
                batch = status_list[i:i + BATCH_SIZE]
                
                for status in batch:
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
                        
                        if elapsed >= total_seconds:
                            logger.info(f"⏰ 考试超时自动提交: user={user_id}, exam={exam_id}, 已用时={int(elapsed)}秒")
                            success = auto_submit_single_exam(db, user_id, exam_id, started_at_str, now)
                            if success:
                                submitted_count += 1
                            else:
                                failed_count += 1
                                
                    except Exception as e:
                        logger.error(f"处理考试记录失败: user={user_id}, exam={exam_id}, error={e}")
                        failed_count += 1
                
                if i + BATCH_SIZE < len(status_list):
                    time.sleep(0.5)
            
            logger.info(f"✅ 超时考试扫描完成: 提交 {submitted_count} 个，失败 {failed_count} 个")
            
        except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.TimeoutException) as e:
            logger.error(f"⚠️ 扫描超时考试超时，将在下次扫描重试: {e}")
        except Exception as e:
            logger.error(f"❌ 自动提交超时考试失败: {e}", exc_info=True)

# ============================================================
# 5. 检查考试有效期（带重试）
# ============================================================
@retry_on_timeout(max_retries=2, delay=1)
def check_exam_end_time(exam_id, user_id, started_at_str):
    """
    检查考试有效期是否已过（仅用于通知，不强制提交）
    返回: (is_expired, end_time_str)
    """
    try:
        exam_res = safe_table('exams').select('end_time')\
            .eq('id', exam_id)\
            .maybe_single()\
            .execute()
        
        if not exam_res.data or not exam_res.data.get('end_time'):
            return False, None
        
        end_time_str = exam_res.data['end_time']
        now = datetime.now(timezone.utc)
        
        try:
            end_dt = datetime.fromisoformat(end_time_str.replace('Z', '+00:00'))
            is_expired = now > end_dt
            
            if is_expired and started_at_str:
                logger.warning(f"⚠️ 考试 {exam_id} 有效期已过，但考生 {user_id} 仍在进行中，"
                              f"将允许完成剩余考试时间")
            
            return is_expired, end_time_str
        except:
            return False, None
            
    except Exception as e:
        logger.warning(f"检查考试有效期失败: {e}")
        return False, None


# ============================================================
# 6. 初始化调度器（增加错误处理）
# ============================================================
def init_scheduler(app):
    """初始化定时调度器"""
    try:
        scheduler = BackgroundScheduler()

        # ========== 任务1: 自动提交超时考试 ==========
        scan_interval = int(os.environ.get('EXAM_SCAN_INTERVAL', 60))
        scheduler.add_job(
            func=auto_submit_timeout_exams,
            trigger="interval",
            seconds=scan_interval,
            args=[app],
            id="auto_submit_exam_job",
            replace_existing=True,
            max_instances=1,  # 防止任务重叠
            misfire_grace_time=30  # 允许 30 秒的延迟
        )
        logger.info(f"✅ 自动提交调度器已启动，扫描间隔: {scan_interval}秒")

        # ========== 任务2: 更新培训状态 ==========
        scheduler.add_job(
            func=update_all_training_statuses,
            trigger="interval",
            minutes=30,
            id="update_training_statuses",
            replace_existing=True,
            max_instances=1
        )
        logger.info("✅ 培训状态更新任务已注册，间隔: 30分钟")
        
        # ========== 任务3: 更新考试状态 ==========
        scheduler.add_job(
            func=update_all_exam_statuses,
            trigger="interval",
            minutes=30,
            id="update_exam_statuses",
            replace_existing=True,
            max_instances=1
        )
        logger.info("✅ 考试状态更新任务已注册，间隔: 30分钟")
        
        scheduler.start()
        logger.info("✅ 定时调度器已启动")

        # 立即执行一次，确保状态及时更新
        try:
            update_all_training_statuses()
            update_all_exam_statuses()
            logger.info("✅ 首次状态更新已执行")
        except Exception as e:
            logger.warning(f"首次状态更新失败（非致命）: {e}")
        
        # 确保程序退出时关闭调度器
        def shutdown_scheduler():
            try:
                scheduler.shutdown()
                logger.info("调度器已关闭")
            except Exception as e:
                logger.warning(f"关闭调度器时出错: {e}")
        
        atexit.register(shutdown_scheduler)
        
        return scheduler
        
    except Exception as e:
        logger.error(f"❌ 调度器启动失败: {e}")
        return None

# ============================================================
# 7. 动态更新状态
# ============================================================
# services/scheduler.py

from utils.training_helpers import is_draft_time, DRAFT_PLACEHOLDER

def update_all_training_statuses():
    """定时更新所有培训的 dynamic_status（完善版）"""
    try:
        db = get_supabase()
        now = datetime.now(timezone.utc).isoformat()
        
        logger.info("🔄 开始更新培训状态...")
        
        # 1. 更新为 closed（已结束）- 排除草稿占位符
        result1 = db.table("trainings").update({
            "dynamic_status": "closed"
        }).lt("end_time", now)\
          .neq("end_time", DRAFT_PLACEHOLDER)\
          .execute()
        
        # 2. 更新为 active（进行中）- 排除草稿占位符
        result2 = db.table("trainings").update({
            "dynamic_status": "active",
            "is_active": True
        }).lte("start_time", now)\
          .gte("end_time", now)\
          .neq("start_time", DRAFT_PLACEHOLDER)\
          .neq("end_time", DRAFT_PLACEHOLDER)\
          .execute()
        
        # 3. 更新为 pending（未开始）- 排除草稿占位符
        result3 = db.table("trainings").update({
            "dynamic_status": "pending"
        }).gt("start_time", now)\
          .neq("start_time", DRAFT_PLACEHOLDER)\
          .execute()
        
        # 4. 更新为 draft（草稿，没有有效期）
        result4 = db.table("trainings").update({
            "dynamic_status": "draft",
            "is_active": False
        }).filter("start_time", "is", "null")\
          .filter("end_time", "is", "null")\
          .execute()
        
        # 5. 额外修复：将使用占位符的培训强制设为 draft
        result5 = db.table("trainings").update({
            "dynamic_status": "draft",
            "is_active": False
        }).eq("start_time", DRAFT_PLACEHOLDER)\
          .eq("end_time", DRAFT_PLACEHOLDER)\
          .execute()
        
        logger.info(f"✅ 培训状态更新完成: closed={len(result1.data or [])}, "
                   f"active={len(result2.data or [])}, "
                   f"pending={len(result3.data or [])}, "
                   f"draft={len(result4.data or [])}, "
                   f"草稿修复={len(result5.data or [])}")
        
    except Exception as e:
        logger.error(f"❌ 更新培训状态失败: {e}")

def update_all_exam_statuses():
    """定时更新所有考试的状态"""
    try:
        db = get_supabase()
        now = datetime.now(timezone.utc).isoformat()
        
        logger.info("🔄 开始更新考试状态...")
        
        # 1. 更新为 closed（已结束）
        result1 = db.table("exams").update({
            "status": "closed"
        }).lt("end_time", now).execute()
        
        # 2. 更新为 active（进行中）
        result2 = db.table("exams").update({
            "status": "active",
            "is_active": True
        }).lte("start_time", now).gte("end_time", now).execute()
        
        # 3. 更新为 created（未开始）
        result3 = db.table("exams").update({
            "status": "created"
        }).gt("start_time", now).execute()
        
        # 4. 更新为 draft（草稿，没有有效期）
        result4 = db.table("exams").update({
            "status": "draft",
            "is_active": False
        }).filter("start_time", "is", "null").filter("end_time", "is", "null").execute()
        
        logger.info(f"✅ 考试状态更新完成: closed={len(result1.data or [])}, "
                   f"active={len(result2.data or [])}, "
                   f"created={len(result3.data or [])}, "
                   f"draft={len(result4.data or [])}")
        
    except Exception as e:
        logger.error(f"❌ 更新考试状态失败: {e}")

