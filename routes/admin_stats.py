# routes/admin_stats.py - 新建文件，专门处理统计相关逻辑

import logging
from datetime import datetime, timezone, date
from flask import session
from services.db import get_supabase
from utils.permissions import get_admin_allowed_countries, is_developer
from routes.helpers import parse_exam_countries, can_access_exam
from utils.status import get_exam_status

logger = logging.getLogger(__name__)


def get_user_stats(allowed_countries):
    """
    获取用户统计数据
    返回: (registered_count, imported_count)
    """
    db = get_supabase()
    
    registered_query = db.table("users").select("id", count="exact")\
        .eq("user_status", "registered")\
        .is_("deleted_at", "null")
    
    imported_query = db.table("users").select("id", count="exact")\
        .eq("user_status", "imported")\
        .is_("deleted_at", "null")
    
    if allowed_countries is not None:
        if not allowed_countries:
            return 0, 0
        registered_query = registered_query.in_("country", allowed_countries)
        imported_query = imported_query.in_("country", allowed_countries)
        registered_count = registered_query.execute().count or 0
        imported_count = imported_query.execute().count or 0
    else:
        registered_count = registered_query.execute().count or 0
        imported_count = imported_query.execute().count or 0
    
    logger.info(f"用户统计: 已注册={registered_count}, 已导入={imported_count}")
    return registered_count, imported_count


def get_exam_stats(allowed_countries):
    """
    获取考试统计数据
    返回: (exams_total, exams_completed, exam_stats, filtered_exams, allowed_user_ids)
    """
    db = get_supabase()
    is_dev = is_developer()
    
    exams_query = db.table("exams").select("*").is_("deleted_at", "null")
    
    filtered_exams = []
    allowed_user_ids = []
    allowed_exam_ids = set()
    
    if allowed_countries is not None and allowed_countries:
        # 获取允许国家下的用户ID
        users_in_allowed = db.table("users").select("id").in_("country", allowed_countries).execute()
        allowed_user_ids = [u['id'] for u in (users_in_allowed.data or [])] if users_in_allowed.data else []
        
        # 查询分配了允许国家考生的考试ID
        if allowed_user_ids:
            assign_res = db.table("exam_assignments").select("exam_id").in_("user_id", allowed_user_ids).execute()
            allowed_exam_ids = {a['exam_id'] for a in (assign_res.data or [])}
        
        all_exams = exams_query.execute().data or []
        
        for exam in all_exams:
            exam_countries = parse_exam_countries(exam)
            has_intersection = any(c in allowed_countries for c in exam_countries)
            
            if has_intersection or exam['id'] in allowed_exam_ids:
                filtered_exams.append(exam)
    else:
        filtered_exams = exams_query.execute().data or []
    
    # 统计考试状态
    exam_stats = {'draft': 0, 'created': 0, 'active': 0, 'closed': 0}
    for exam in filtered_exams:
        status = get_exam_status(exam)
        if status in exam_stats:
            exam_stats[status] += 1
    
    exams_total = len(filtered_exams)
    
    # 统计已完成考试数量
    completed_query = db.table("exam_results").select("exam_id", count="exact").execute()
    completed_exam_ids = set([r['exam_id'] for r in (completed_query.data or [])])
    exams_completed = len([e for e in filtered_exams if e['id'] in completed_exam_ids])
    
    logger.info(f"考试统计: 总数={exams_total}, 已完成={exams_completed}, "
                f"草稿={exam_stats['draft']}, 进行中={exam_stats['active']}, 已关闭={exam_stats['closed']}")
    
    return exams_total, exams_completed, exam_stats, filtered_exams, allowed_user_ids, allowed_exam_ids


def get_training_stats(allowed_countries, allowed_user_ids):
    """
    获取培训统计数据
    返回: (trainings_count, total_attendances, signins_today)
    """
    db = get_supabase()
    
    trainings_query = db.table("trainings").select("*").is_("deleted_at", "null")
    
    if allowed_countries is not None and allowed_countries:
        # 查询存在允许国家学员签到的培训ID
        allowed_training_ids = set()
        if allowed_user_ids:
            attend_res = db.table("training_attendances").select("training_id").in_("user_id", allowed_user_ids).execute()
            allowed_training_ids = {a['training_id'] for a in (attend_res.data or [])}
        
        all_trainings = trainings_query.execute().data or []
        filtered_trainings = []
        for training in all_trainings:
            if training.get('country') in allowed_countries:
                filtered_trainings.append(training)
            elif training['id'] in allowed_training_ids:
                filtered_trainings.append(training)
    else:
        filtered_trainings = trainings_query.execute().data or []
    
    trainings_count = len(filtered_trainings)
    
    # 统计签到总人次
    total_attendances = 0
    if allowed_countries is not None and allowed_countries:
        if allowed_user_ids:
            attend_count = db.table("training_attendances").select("id", count="exact").in_("user_id", allowed_user_ids).execute()
            total_attendances = attend_count.count or 0
    else:
        attend_count = db.table("training_attendances").select("id", count="exact").execute()
        total_attendances = attend_count.count or 0
    
    # 今日签到数量
    today = date.today().isoformat()
    if allowed_countries is not None and allowed_countries:
        if allowed_user_ids:
            signins_today = db.table("training_attendances").select("id", count="exact")\
                .in_("user_id", allowed_user_ids)\
                .gte("sign_time", today).execute().count or 0
        else:
            signins_today = 0
    else:
        signins_today = db.table("training_attendances").select("id", count="exact")\
            .gte("sign_time", today).execute().count or 0
    
    logger.info(f"培训统计: 培训数={trainings_count}, 签到总人次={total_attendances}, 今日签到={signins_today}")
    
    return trainings_count, total_attendances, signins_today


def get_interview_stats(allowed_countries):
    """
    获取访谈统计数据
    返回: interviewee_count
    """
    db = get_supabase()
    
    interviews_query = db.table("interviews").select("*").is_("deleted_at", "null")
    
    if allowed_countries is not None and allowed_countries:
        # 获取允许国家下的用户ID
        users_in_allowed = db.table("users").select("id").in_("country", allowed_countries).execute()
        allowed_user_ids = [u['id'] for u in (users_in_allowed.data or [])] if users_in_allowed.data else []
        
        # 查询存在允许国家学员的访谈ID
        allowed_interview_ids = set()
        if allowed_user_ids:
            interview_res = db.table("interview_results").select("interview_id").in_("user_id", allowed_user_ids).execute()
            allowed_interview_ids = {i['interview_id'] for i in (interview_res.data or [])}
        
        all_interviews = interviews_query.execute().data or []
        filtered_interviews = []
        for interview in all_interviews:
            exam_id = interview.get('exam_id')
            if exam_id:
                exam_res = db.table("exams").select("countries, country").eq("id", exam_id).maybe_single().execute()
                if exam_res.data:
                    exam_countries = parse_exam_countries(exam_res.data)
                    if any(c in allowed_countries for c in exam_countries):
                        filtered_interviews.append(interview)
                    elif interview['id'] in allowed_interview_ids:
                        filtered_interviews.append(interview)
            elif interview['id'] in allowed_interview_ids:
                filtered_interviews.append(interview)
    else:
        filtered_interviews = interviews_query.execute().data or []
    
    interviewee_count = len(filtered_interviews)
    logger.info(f"访谈统计: 访谈数={interviewee_count}")
    
    return interviewee_count


def get_questions_stats(allowed_countries, filtered_exams):
    """
    获取题库统计数据
    返回: questions_count
    """
    db = get_supabase()
    
    try:
        if allowed_countries is not None and allowed_countries:
            exams_in_allowed = [e['id'] for e in filtered_exams]
            if exams_in_allowed:
                questions_count = db.table("questions").select("id", count="exact")\
                    .in_("exam_id", exams_in_allowed).execute().count or 0
            else:
                questions_count = 0
        else:
            questions_count = db.table("questions").select("id", count="exact").execute().count or 0
    except:
        questions_count = 0
    
    logger.info(f"题库统计: 题目数={questions_count}")
    return questions_count

# routes/admin_stats.py - 修复 get_exams_for_display 函数

def get_exams_for_display(filtered_exams, allowed_countries, allowed_user_ids):
    """
    获取用于前端显示的考试列表（表格和下拉框）
    确保只显示权限范围内的考试
    """
    db = get_supabase()
    now = datetime.now(timezone.utc)
    exams_for_table = []
    exams_for_selector = []
    seen_selector_ids = set()  # 用于去重
    
    # 获取所有已开始但未提交的考试
    started_exams = set()
    status_res = db.table("user_exam_status").select("exam_id").eq("is_submitted", False).not_.is_("started_at", "null").execute()
    for s in (status_res.data or []):
        started_exams.add(s['exam_id'])
    
    # ✅ 确保 filtered_exams 已经是权限过滤后的
    for exam in filtered_exams:
        exam_id = exam['id']
        
        # ✅ 双重检查：再次验证考试是否在权限范围内
        exam_countries = parse_exam_countries(exam)
        if allowed_countries is not None and allowed_countries:
            # 检查是否有交集
            if not any(c in allowed_countries for c in exam_countries):
                continue  # 跳过不在权限范围内的考试
        
        has_started = exam_id in started_exams
        status = get_exam_status(exam, has_started=has_started)
        exam['status'] = status
        
        # 统计应考/实考人数
        assign_query = db.table("exam_assignments").select("user_id").eq("exam_id", exam_id)
        if allowed_countries is not None and allowed_countries:
            if allowed_user_ids:
                assign_query = assign_query.in_("user_id", allowed_user_ids)
                assigned_count = assign_query.execute().count or 0
            else:
                assigned_count = 0
        else:
            assigned_count = assign_query.execute().count or 0
        
        submitted_query = db.table("exam_results").select("user_id", count="exact").eq("exam_id", exam_id)
        if allowed_countries is not None and allowed_countries:
            if allowed_user_ids:
                submitted_query = submitted_query.in_("user_id", allowed_user_ids)
                submitted_count = submitted_query.execute().count or 0
            else:
                submitted_count = 0
        else:
            submitted_count = submitted_query.execute().count or 0
        
        exam['assigned_count'] = assigned_count
        exam['submitted_count'] = submitted_count
        
        # 解析并过滤国家列表
        exam_countries = parse_exam_countries(exam)
        if not exam_countries and exam.get('country'):
            exam_countries = [exam.get('country')]
        
        if allowed_countries is not None:
            filtered_countries = [c for c in exam_countries if c in allowed_countries]
        else:
            filtered_countries = exam_countries
        
        exam['countries_display'] = ', '.join(filtered_countries) if filtered_countries else '-'
        exam['countries_filtered'] = filtered_countries
        
        # 仪表盘表格显示：草稿、已创建、进行中
        if status in ["draft", "created", "active"]:
            exam['dynamic_status'] = status
            exams_for_table.append(exam)
        
        # 考生考试状态下拉框显示：进行中（去重）
        if status == "active":
            if exam_id not in seen_selector_ids:
                seen_selector_ids.add(exam_id)
                exams_for_selector.append({
                    "id": exam['id'],
                    "title": exam['title']
                })
    
    return exams_for_table, exams_for_selector

def get_sign_in_status():
    """获取培训签到开关状态"""
    db = get_supabase()
    try:
        config_res = db.table("system_config").select("value").eq("key", "training_open").execute()
        sign_in_open = config_res.data[0].get('value', 'false').lower() == 'true' if config_res.data else False
    except:
        sign_in_open = False
    return sign_in_open