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
    获取用户统计数据（复用用户列表的查询逻辑）
    返回: (registered_count, imported_count)
    """
    users = get_filtered_users_for_stats()
    
    # 统计
    registered_count = 0
    imported_count = 0
    
    for user in users:
        user_status = user.get('user_status')
        if user_status == 'registered':
            registered_count += 1
        elif user_status == 'imported':
            imported_count += 1
    
    logger.info(f"用户统计(复用列表): 已注册={registered_count}, 已导入={imported_count}, 总计={len(users)}")
    return registered_count, imported_count

def get_filtered_users_for_stats():
    """
    获取过滤后的用户列表（用于统计，复用列表查询逻辑）
    支持超管和管理员的不同权限逻辑
    """
    db = get_supabase()
    current_user_id = session.get('user_id')
    current_role = session.get('role')
    is_dev = is_developer()
    allowed_countries = get_admin_allowed_countries()
    
    # ========== 1. 基础查询 ==========
    query = db.table("users").select("*").is_("deleted_at", "null")
    
    if not is_dev:
        query = query.eq("is_protected", False)
    
    # 非超管角色排除超管和开发者（超管可以看到所有角色）
    if current_role != 'super_admin' and not is_dev:
        query = query.neq("role", "super_admin").neq("role", "developer")
    
    # 执行查询
    all_res = query.execute()
    all_users = all_res.data or []
    
    # 调试日志
    logger.info(f"基础查询到 {len(all_users)} 条用户")
    
    # ========== 2. 获取创建人信息（用于导入用户权限判断）==========
    creator_ids = list(set([u.get('created_by') for u in all_users if u.get('created_by')]))
    creator_info = {}
    if creator_ids:
        creator_res = db.table("users").select("id, country, name_en").in_("id", creator_ids).execute()
        for c in (creator_res.data or []):
            creator_info[c['id']] = c
    
    # ========== 3. 权限过滤 ==========
    filtered_users = []
    
    for user in all_users:
        user_country = user.get('country') or ''
        user_status = user.get('user_status', '')
        user_role = user.get('role', '')
        created_by = user.get('created_by')
        
        # 角色过滤（超管可以看到所有角色，管理员不能看到超管和开发者）
        if user_role in ['super_admin', 'developer'] and current_role != 'super_admin' and not is_dev:
            continue
        
        # ========== 超管逻辑 ==========
        if current_role == 'super_admin':
            # 超管有权限范围限制
            if allowed_countries is not None and allowed_countries:
                # ✅ 修复：用户国家在权限范围内
                if user_country and user_country in allowed_countries:
                    filtered_users.append(user)
                    logger.debug(f"超管: 用户 {user.get('name_en')} 国家 {user_country} 在权限范围内")
                    continue
                
                # 国家为空的用户，检查创建者的国家
                if not user_country:
                    creator = creator_info.get(created_by, {})
                    creator_country = creator.get('country', '')
                    if creator_country in allowed_countries:
                        filtered_users.append(user)
                        logger.debug(f"超管: 空国家用户 {user.get('name_en')} 的创建者国家 {creator_country} 在权限范围内")
                        continue
                # 不满足条件，跳过
                logger.debug(f"超管: 用户 {user.get('name_en')} 被过滤，国家={user_country}")
            else:
                # 超管无权限范围，可以看到所有
                filtered_users.append(user)
                logger.debug(f"超管(无限制): 用户 {user.get('name_en')} 通过")
            continue
        
        # ========== 管理员逻辑 ==========
        if current_role == 'admin':
            # 已注册用户：按国家过滤
            if user_status == 'registered':
                if allowed_countries is not None and allowed_countries:
                    if user_country and user_country in allowed_countries:
                        filtered_users.append(user)
                        logger.debug(f"管理员: 已注册用户 {user.get('name_en')} 国家 {user_country} 在权限范围内")
                else:
                    # 无权限范围时，使用用户注册国家
                    user_session_country = session.get('user_country')
                    if user_country == user_session_country:
                        filtered_users.append(user)
            
            # 已导入用户：按创建者权限过滤
            elif user_status == 'imported':
                # 开发者可以看到所有
                if is_dev:
                    filtered_users.append(user)
                    continue
                
                # 自己创建的
                if created_by == current_user_id:
                    filtered_users.append(user)
                    logger.debug(f"管理员: 导入用户 {user.get('name_en')} 是自己创建的")
                    continue
                
                # 创建者同国家
                creator = creator_info.get(created_by, {})
                creator_country = creator.get('country', '')
                
                if allowed_countries is not None and allowed_countries:
                    if creator_country in allowed_countries:
                        filtered_users.append(user)
                        logger.debug(f"管理员: 导入用户 {user.get('name_en')} 的创建者国家 {creator_country} 在权限范围内")
                else:
                    user_session_country = session.get('user_country')
                    if creator_country == user_session_country:
                        filtered_users.append(user)
            
            # 其他状态
            else:
                if allowed_countries is not None and allowed_countries:
                    if user_country and user_country in allowed_countries:
                        filtered_users.append(user)
                else:
                    user_session_country = session.get('user_country')
                    if user_country == user_session_country:
                        filtered_users.append(user)
            continue
        
        # ========== 其他角色（不应该出现）==========
        filtered_users.append(user)
    
    logger.info(f"权限过滤后: {len(filtered_users)} 条用户")
    return filtered_users

'''
def get_exam_stats(allowed_countries):
    """
    获取考试统计数据（与渲染列表使用完全相同的过滤逻辑）
    返回: (exams_total, exams_completed, exam_stats, filtered_exams, allowed_user_ids, allowed_exam_ids)
    """
    db = get_supabase()
    is_dev = is_developer()
    current_role = session.get('role')
    
    logger.info("-" * 40)
    logger.info(f"get_exam_stats 被调用")
    logger.info(f"当前角色: {current_role}")
    logger.info(f"权限范围 allowed_countries: {allowed_countries}")
    logger.info(f"是否开发者: {is_dev}")
    
    exams_query = db.table("exams").select("*").is_("deleted_at", "null")
    
    filtered_exams = []
    allowed_user_ids = []
    allowed_exam_ids = set()
    
    if allowed_countries is not None and allowed_countries:
        logger.info(f"开始权限过滤，allowed_countries={allowed_countries}")
        # 获取允许国家下的用户ID
        users_in_allowed = db.table("users").select("id").in_("country", allowed_countries).execute()
        allowed_user_ids = [u['id'] for u in (users_in_allowed.data or [])] if users_in_allowed.data else []
        
        # 查询分配了允许国家考生的考试ID
        if allowed_user_ids:
            assign_res = db.table("exam_assignments").select("exam_id").in_("user_id", allowed_user_ids).execute()
            allowed_exam_ids = {a['exam_id'] for a in (assign_res.data or [])}
            logger.info(f"允许国家下的用户ID数量: {len(allowed_user_ids)}")
        
        all_exams = exams_query.execute().data or []
        logger.info(f"数据库中总考试数: {len(all_exams)}")
        
        for exam in all_exams:
            exam_countries = parse_exam_countries(exam)
            # 检查是否有交集（考试国家在权限范围内）
            has_intersection = any(c in allowed_countries for c in exam_countries)

            logger.debug(f"考试 {exam.get('id')}: countries={exam_countries}, has_intersection={has_intersection}, in_allowed_exam_ids={exam['id'] in allowed_exam_ids}")
 
            if has_intersection or exam['id'] in allowed_exam_ids:
                filtered_exams.append(exam)
        logger.info(f"权限过滤后考试数: {len(filtered_exams)}")
    else:
        filtered_exams = exams_query.execute().data or []
        logger.info(f"无权限限制，返回所有考试: {len(filtered_exams)}")
    
    # ✅ 注意：这里不统计状态，只返回过滤后的考试列表
    # 状态统计应该在调用方根据实际渲染结果进行
    
    exams_total = len(filtered_exams)
    
    # 统计已完成考试数量（有成绩记录）
    completed_exam_ids = set()
    if filtered_exams:
        exam_ids = [e['id'] for e in filtered_exams]
        completed_res = db.table("exam_results").select("exam_id").in_("exam_id", exam_ids).execute()
        completed_exam_ids = {r['exam_id'] for r in (completed_res.data or [])}
    exams_completed = len([e for e in filtered_exams if e['id'] in completed_exam_ids])
    
    # 返回状态统计（基于当前过滤结果）
    exam_stats = {'draft': 0, 'created': 0, 'active': 0, 'closed': 0}
    for exam in filtered_exams:
        status = get_exam_status(exam)
        if status in exam_stats:
            exam_stats[status] += 1
    
    logger.info(f"考试统计: 总数={exams_total}, 已完成={exams_completed}, "
                f"草稿={exam_stats['draft']}, 进行中={exam_stats['active']}, 已关闭={exam_stats['closed']}")

    logger.info(f"get_exam_stats 结果: 总数={exams_total}, 已完成={exams_completed}, 状态={exam_stats}")
    logger.info("-" * 40)
    
    return exams_total, exams_completed, exam_stats, filtered_exams, allowed_user_ids, allowed_exam_ids
'''
# routes/admin_stats.py - 修复 get_exam_stats

def get_exam_stats(allowed_countries):
    """
    获取考试统计数据（只使用国家匹配进行权限过滤）
    返回: (exams_total, exams_completed, exam_stats, filtered_exams, allowed_user_ids, allowed_exam_ids)
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
        
        all_exams = exams_query.execute().data or []
        
        for exam in all_exams:
            exam_countries = parse_exam_countries(exam)
            if not exam_countries and exam.get('country'):
                exam_countries = [exam.get('country')]
            
            # ✅ 关键修复：只使用国家匹配进行过滤
            has_intersection = any(c in allowed_countries for c in exam_countries)
            
            if has_intersection:
                filtered_exams.append(exam)
            else:
                logger.debug(f"考试 {exam.get('id')} 被过滤: countries={exam_countries}, allowed={allowed_countries}")
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
    completed_exam_ids = set()
    if filtered_exams:
        exam_ids = [e['id'] for e in filtered_exams]
        completed_res = db.table("exam_results").select("exam_id").in_("exam_id", exam_ids).execute()
        completed_exam_ids = {r['exam_id'] for r in (completed_res.data or [])}
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

    logger.info(f"get_exams_for_display 输入: filtered_exams数量={len(filtered_exams)}")

    # 获取所有已开始但未提交的考试
    started_exams = set()
    status_res = db.table("user_exam_status").select("exam_id").eq("is_submitted", False).not_.is_("started_at", "null").execute()
    for s in (status_res.data or []):
        started_exams.add(s['exam_id'])
    logger.info(f"已开始但未提交的考试ID: {started_exams}")
    
    # ✅ 确保 filtered_exams 已经是权限过滤后的
    for exam in filtered_exams:
        exam_id = exam['id']
        
        # ✅ 双重检查：再次验证考试是否在权限范围内
        exam_countries = parse_exam_countries(exam)
        if allowed_countries is not None and allowed_countries:
            # 检查是否有交集
            if not any(c in allowed_countries for c in exam_countries):
                logger.debug(f"考试 {exam_id} 被双重过滤跳过: countries={exam_countries}, allowed={allowed_countries}")
                continue  # 跳过不在权限范围内的考试
        
        has_started = exam_id in started_exams
        status = get_exam_status(exam, has_started=has_started)
        exam['status'] = status

        logger.debug(f"考试 {exam_id}: status={status}, has_started={has_started}")
        
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
            logger.debug(f"考试 {exam_id} 添加到 exams_for_table")
        
        # 考生考试状态下拉框显示：进行中（去重）
        if status == "active":
            if exam_id not in seen_selector_ids:
                seen_selector_ids.add(exam_id)
                exams_for_selector.append({
                    "id": exam['id'],
                    "title": exam['title']
                })
                logger.debug(f"考试 {exam_id} 添加到 exams_for_selector")
    
    logger.info(f"get_exams_for_display 输出: exams_for_table={len(exams_for_table)}, exams_for_selector={len(exams_for_selector)}")
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
