# routes/admin_inspection.py
import logging
import json
from datetime import datetime, timezone, timedelta
from flask import request, jsonify, render_template, session, flash, redirect, url_for
from . import admin_inspection_bp
from services.db import get_supabase, get_supabase_admin
from utils.common import get_reviewer_by_country
from routes.helpers import login_required, admin_required, random_pick_questions, get_allowed_countries, parse_exam_countries, can_access_exam
from utils.permissions import get_admin_allowed_countries, is_developer


logger = logging.getLogger(__name__)

@admin_inspection_bp.route('/admin/interviews')
@login_required
@admin_required
def admin_interviews_page():
    return render_template('admin/list_inspection.html')

@admin_inspection_bp.route('/api/admin/interviews', methods=['GET', 'POST', 'PUT'])
@login_required
@admin_required
def api_admin_interviews():
    """访谈列表查询、创建、更新"""
    print("=" * 60)
    print("🔥 api_admin_interviews 被调用！")

    db = get_supabase()
    if request.method == 'GET':
        name = request.args.get('name', '')
        country = request.args.get('country', '')
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 20, type=int)

        allowed = get_allowed_countries()

        query = db.table("interviews").select("*", count="exact").is_("deleted_at", "null")
        if name:
            query = query.ilike("title", f"%{name}%")
        query = query.order("created_at", desc=True)

        res = query.execute()
        all_interviews = res.data or []

        # 根据权限过滤访谈
        if allowed is not None and allowed:
            exam_ids = set()
            for inv in all_interviews:
                if inv.get('exam_id'):
                    exam_ids.add(inv['exam_id'])

            exam_country_map = {}
            if exam_ids:
                exams_res = db.table("exams").select("id, country, countries").in_("id", list(exam_ids)).execute()
                for exam in (exams_res.data or []):
                    exam_country_map[exam['id']] = parse_exam_countries(exam)

            filtered = []
            for inv in all_interviews:
                exam_countries = exam_country_map.get(inv.get('exam_id'), [])
                if any(c in allowed for c in exam_countries):
                    filtered.append(inv)
            all_interviews = filtered

        total = len(all_interviews)
        start = (page - 1) * per_page
        end = start + per_page
        interviews = all_interviews[start:end]

        # 收集用户ID用于统计
        all_user_ids = set()
        for inv in interviews:
            user_res = db.table("interview_results").select("user_id").eq("interview_id", inv['id']).execute()
            all_user_ids.update(r['user_id'] for r in (user_res.data or []))

        user_country_map = {}
        if allowed is not None and all_user_ids:
            users_res = db.table("users").select("id, country").in_("id", list(all_user_ids)).execute()
            user_country_map = {u['id']: u.get('country') for u in (users_res.data or [])}

        now = datetime.now(timezone.utc)
        for inv in interviews:
            # 计算状态
            start_time = inv.get('start_time')
            end_time = inv.get('end_time')
            if not start_time or not end_time:
                inv['status'] = 'draft'
            else:
                try:
                    s = datetime.fromisoformat(start_time)
                    e = datetime.fromisoformat(end_time)
                    if now < s:
                        inv['status'] = 'created'
                    elif now > e:
                        inv['status'] = 'closed'
                    else:
                        inv['status'] = 'active'
                except:
                    inv['status'] = 'draft'

            # 统计人数
            user_res = db.table("interview_results").select("user_id").eq("interview_id", inv['id']).execute()
            user_ids = [r['user_id'] for r in (user_res.data or [])]

            if user_ids:
                active_users = db.table("users").select("id").in_("id", user_ids).eq("is_resign", False).execute()
                active_user_ids = [u['id'] for u in (active_users.data or [])]
            else:
                active_user_ids = []

            if allowed is not None:
                filtered_ids = [uid for uid in active_user_ids if user_country_map.get(uid) in allowed]
                inv['interviewee_count'] = len(set(filtered_ids))
            else:
                inv['interviewee_count'] = len(set(active_user_ids))

            # 附加考试信息 - 使用 parse_exam_countries 解析国家
            if inv.get('exam_id'):
                exam_res = db.table("exams").select("title, country, countries").eq("id", inv['exam_id']).maybe_single().execute()
                if exam_res.data:
                    inv['exam_title'] = exam_res.data.get('title', '')

                    # 使用 parse_exam_countries 解析多国家字段
                    exam_countries = parse_exam_countries(exam_res.data)

                    # 添加调试日志
                    logger.info(f"访谈 {inv['id']}: exam_id={inv['exam_id']}, exam_countries={exam_countries}")

                    # 根据管理员权限过滤国家显示
                    if allowed is not None and allowed:
                        filtered_countries = [c for c in exam_countries if c in allowed]
                        inv['country'] = ', '.join(filtered_countries) if filtered_countries else ''
                    else:
                        inv['country'] = ', '.join(exam_countries) if exam_countries else ''

                    inv['exam_countries'] = exam_countries

        return jsonify({"data": interviews, "total": total, "page": page, "per_page": per_page})

    elif request.method == 'POST':
        data = request.json
        exam_id = data.get('exam_id')
        title = data.get('title', '')
        if not title:
            exam_title = ''
            if exam_id:
                exam_res = db.table("exams").select("title").eq("id", exam_id).maybe_single().execute()
                if exam_res.data:
                    exam_title = exam_res.data['title']
            title = f"Interview-{exam_title}" if exam_title else "未命名访谈"
        question_count = data.get('question_count', 5)
        reviewer = data.get('reviewer', '')
        is_draft = data.get('is_draft', False)
        feedback = data.get('feedback', '')
        start_time = data.get('start_time')
        end_time = data.get('end_time')

        # 将本地时间转为 UTC 存储
        start_time_utc = start_time if start_time else None
        end_time_utc = end_time if end_time else None

        status = 'draft' if is_draft else 'active'
        if start_time and end_time:
            status = 'active'  # 简单处理，后续根据时间自动判定

        user_ids = data.get('user_ids', [])
        if user_ids:
            # 校验所有用户均为已注册
            valid_users = db.table("users").select("id").in_("id", user_ids).eq("user_status", "registered").execute()
            if len(valid_users.data or []) != len(user_ids):
                return jsonify({"success": False, "message": "所选考生中包含未注册的用户"}), 400

        interviewee_count = len(user_ids)
        interview_insert = db.table("interviews").insert({
            "title": title,
            "exam_id": exam_id,
            "created_by": session['user_id'],
            "reviewer": reviewer,
            "feedback": feedback,
            "question_count": question_count,
            "status": status,
            "start_time": start_time_utc,
            "end_time": end_time_utc,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "interviewee_count": interviewee_count
        }).execute()
        if not interview_insert.data:
            return jsonify({"success": False, "message": "创建失败"}), 500
        new_id = interview_insert.data[0]['id']

        # 为选中的学员抽取题目
        user_ids = data.get('user_ids', [])
        for uid in user_ids:
            questions = random_pick_questions(exam_id, question_count)
            for q in questions:
                db.table("interview_results").insert({
                    "interview_id": new_id,
                    "user_id": uid,
                    "question_id": q['id']
                }).execute()
        return jsonify({"success": True, "id": new_id})

    elif request.method == 'PUT':
        data = request.json
        inv_id = data.get('id')
        db = get_supabase()
        # 获取原访谈
        orig_res = db.table("interviews").select("*").eq("id", inv_id).maybe_single().execute()
        if not orig_res.data:
            return jsonify({"success": False, "message": "访谈不存在"}), 404
        orig = orig_res.data

        # 更新基本字段
        update_data = {}
        for field in ['start_time', 'end_time', 'reviewer', 'feedback', 'exam_id', 'question_count', 'title']:
            if field in data:
                val = data[field]
                if field in ('start_time', 'end_time') and val:
                    val = val
                update_data[field] = val
        if update_data:
            db.table("interviews").update(update_data).eq("id", inv_id).execute()

        # 重新抽题（无论状态，只要提供了 user_ids 就更新人员题目）
        if 'user_ids' in data:
            # 删除该访谈的所有现有题目
            db.table("interview_results").delete().eq("interview_id", inv_id).execute()
            # 使用最新的 exam_id 和 question_count
            exam_id = data.get('exam_id', orig['exam_id'])
            question_count = data.get('question_count', orig['question_count'])
            for uid in data['user_ids']:
                questions = random_pick_questions(exam_id, question_count)
                for q in questions:
                    db.table("interview_results").insert({
                        "interview_id": inv_id,
                        "user_id": uid,
                        "question_id": q['id']
                    }).execute()
        
        return jsonify({"success": True})

@admin_inspection_bp.route('/api/admin/interview/<int:interview_id>/detail')
@login_required
@admin_required
def api_get_interview_detail(interview_id):
    db = get_supabase()
    
    inv_res = db.table("interviews").select("*").eq("id", interview_id).maybe_single().execute()
    if not inv_res.data:
        return jsonify({"success": False, "message": "访谈不存在"}), 404
    
    interview = inv_res.data
    
    # 计算状态（与列表接口保持一致）
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    start_time = interview.get('start_time')
    end_time = interview.get('end_time')
    
    if not start_time or not end_time:
        status = 'draft'
    else:
        try:
            s = datetime.fromisoformat(start_time)
            e = datetime.fromisoformat(end_time)
            if now < s:
                status = 'created'
            elif now > e:
                status = 'closed'
            else:
                status = 'active'
        except:
            status = 'draft'
    
    interview['status'] = status
    
    # 获取关联的考试标题
    exam_title = ''
    if interview.get('exam_id'):
        exam_res = db.table("exams").select("title").eq("id", interview['exam_id']).maybe_single().execute()
        if exam_res.data:
            exam_title = exam_res.data['title']
    
    # 获取已分配的学员ID
    user_ids_res = db.table("interview_results").select("user_id").eq("interview_id", interview_id).execute()
    user_ids = list(set([r['user_id'] for r in (user_ids_res.data or [])]))
    
    return jsonify({
        "success": True,
        "data": {
            **interview,
            "exam_title": exam_title,
            "user_ids": user_ids
        }
    })

@admin_inspection_bp.route('/api/admin/interview/<int:interview_id>', methods=['GET'])
@login_required
@admin_required
def api_get_interview(interview_id):
    """获取某个访谈的详细信息"""
    db = get_supabase()
    inv = db.table("interviews").select("*").eq("id", interview_id).maybe_single().execute()
    if not inv.data:
        return jsonify({"error": "访谈不存在"}), 404
    # 获取已分配的学员ID
    user_ids_res = db.table("interview_results").select("user_id").eq("interview_id", interview_id).execute()
    user_ids = list(set([r['user_id'] for r in (user_ids_res.data or [])]))
    inv.data['user_ids'] = user_ids
    return jsonify(inv.data)

@admin_inspection_bp.route('/api/admin/interview/<int:interview_id>/user_ids')
@login_required
@admin_required
def get_interview_user_ids(interview_id):
    """获取某个访谈的所有用户ID"""
    db = get_supabase()
    res = db.table("interview_results").select("user_id").eq("interview_id", interview_id).execute()
    ids = list(set(r['user_id'] for r in (res.data or [])))
    return jsonify({"user_ids": ids})

@admin_inspection_bp.route('/admin/interview/<int:interview_id>')
@login_required
@admin_required
def admin_interview_detail_page(interview_id):
    """3. 访谈二级菜单数据接口 访谈详情页面""" 
    return render_template('admin/list_inspection_details.html', interview_id=interview_id)

@admin_inspection_bp.route('/api/admin/interview/<int:interview_id>/results')
@login_required
@admin_required
def api_interview_results(interview_id):
    """获取某个访谈的所有结果，支持筛选"""
    db = get_supabase()
    search = request.args.get('search', '')
    country = request.args.get('country', '')
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 20, type=int)

    # 基本信息
    inv_res = db.table("interviews").select("*").eq("id", interview_id).maybe_single().execute()
    if not inv_res.data:
        return jsonify({"error": "访谈不存在"}), 404
    interview = inv_res.data

    # 查询访谈结果并关联用户信息
    query = db.table("interview_results").select("*, users(name_cn, name_en, email, country, wh_id)").eq("interview_id", interview_id)
    if country:
        query = query.eq("users.country", country)
    if search:
        query = query.or_(f"users.name_cn.ilike.%{search}%,users.name_en.ilike.%{search}%")
    
    # 分页
    start = (page - 1) * per_page
    end = start + per_page - 1
    res = query.range(start, end).order("user_id").execute()
    total = res.count if hasattr(res, 'count') else len(res.data or [])

    # 组装数据：按用户聚合
    user_results = {}
    for row in (res.data or []):
        uid = row['user_id']
        if uid not in user_results:
            user_info = row.get('users', {})
            user_results[uid] = {
                "user_id": uid,
                "name": user_info.get('name_cn') or user_info.get('name_en', ''),
                "email": user_info.get('email', ''), 
                "country": user_info.get('country', ''),
                "wh_id": user_info.get('wh_id', ''),
                "results": [],
                "submitted_at": None
            }
        user_results[uid]["results"].append({
            "question_id": row['question_id'],
            "answer": row['answer'],
            "is_correct": row['is_correct'],
            "feedback": row['feedback']
        })
        # 提交时间取该用户最近一条有答案的记录时间
        if row.get('submitted_at') and (not user_results[uid]["submitted_at"] or row['submitted_at'] > user_results[uid]["submitted_at"]):
            user_results[uid]["submitted_at"] = row['submitted_at']

    # 转换为列表并计算答对数量
    result_list = []
    for uid, data in user_results.items():
        correct_count = sum(1 for r in data['results'] if r['is_correct'])
        total_questions = len(data['results'])
        result_list.append({
            "user_id": uid,
            "name": data['name'],
            "email": data['email'],
            "country": data['country'],
            "wh_id": data['wh_id'],
            "total_questions": total_questions,
            "correct_count": correct_count,
            "submitted_at": data['submitted_at'],
            "feedback": "",  # 可后续合并
            "results": data['results']
        })

    return jsonify({
        "interview": interview,
        "results": result_list,
        "total": total,
        "page": page,
        "per_page": per_page
    })

@admin_inspection_bp.route('/api/admin/interview/<int:interview_id>/resample/<user_id>', methods=['POST'])
@login_required
@admin_required
def resample_interview(interview_id, user_id):
    """4. 重新访谈接口 重新为指定用户抽题，保留历史记录"""
    db = get_supabase()
    inv_res = db.table("interviews").select("exam_id, question_count").eq("id", interview_id).maybe_single().execute()
    if not inv_res.data:
        return jsonify({"success": False, "message": "访谈不存在"}), 404
    exam_id = inv_res.data['exam_id']
    count = inv_res.data['question_count']
    
    # 删除该用户在此访谈中的旧题目（仅删除未作答的？根据需求保留历史记录，这里简单起见先删除所有旧记录再插入新题）
    db.table("interview_results").delete().eq("interview_id", interview_id).eq("user_id", user_id).execute()
    
    questions = random_pick_questions(exam_id, count)
    for q in questions:
        db.table("interview_results").insert({
            "interview_id": interview_id,
            "user_id": user_id,
            "question_id": q['id']
        }).execute()
    return jsonify({"success": True})

@admin_inspection_bp.route('/api/admin/interview/preview', methods=['POST'])
@login_required
@admin_required
def interview_preview():
    """5. 预览接口（用于模态框 → 预览页） 预览访谈：返回每个被选中学员的抽题情况"""
    db = get_supabase()
    data = request.json
    exam_id = data.get('exam_id')
    
    # 检查题库是否存在
    q_check = db.table("questions").select("id").eq("exam_id", exam_id).limit(1).execute()
    if not q_check.data:
        return jsonify({"error": "该考试没有题目"}), 400

    user_ids = data.get('user_ids', [])
    question_count = data.get('question_count', 5)

    if not exam_id:
        return jsonify({"error": "exam_id 不能为空"}), 400

    logger.info(f"预览访谈: exam_id={exam_id}, users={len(user_ids)}, count={question_count}")
    
    exam_res = db.table("exams").select("title").eq("id", exam_id).maybe_single().execute()
    exam_title = exam_res.data['title'] if exam_res.data else ''
    
    preview = []
    for uid in user_ids:
        user_res = db.table("users").select("name_cn, name_en").eq("id", uid).maybe_single().execute()
        user_name = ''
        if user_res.data:
            user_name = user_res.data.get('name_cn') or user_res.data.get('name_en', '')
        # 随机抽取题目
        questions = random_pick_questions(exam_id, question_count)
        # 处理题目数据，确保 options 为字典，并筛选必要字段
        questions_light = []
        for q in questions:
            # 解析 options（可能为字符串 JSON）
            opts = q.get('options', {})
            if isinstance(opts, str):
                try:
                    opts = json.loads(opts)
                except:
                    opts = {}
            # 过滤空选项
            if opts:
                opts = {k: v for k, v in opts.items() if v and v.strip()}
            questions_light.append({
                'num': q.get('num'),
                'content': q.get('content_cn') or q.get('content') or q.get('content_raw', '无题目内容'),
                'type': q.get('type', 'single'),
                'options': opts   # 前端可能需要展示选项
            })
        preview.append({
            "user_id": uid,
            "user_name": user_name,
            "questions": questions_light
        })
    
    return jsonify({"exam_title": exam_title, "preview": preview})

@admin_inspection_bp.route('/interview/take/<int:interview_id>')
@login_required
def take_interview(interview_id):
    """学员进入访谈（支持普通访谈和强制访谈）"""
    user_id = session['user_id']
    db = get_supabase()
    admin_db = get_supabase_admin()
    now = datetime.now(timezone.utc)
    
    # ========== 1. 检查是否是强制访谈 ==========
    is_force = False
    force_data = None
    
    try:
        # 使用 execute() 代替 maybe_single()
        force_result = admin_db.table("user_interview_force_records").select("*")\
            .eq("original_interview_id", interview_id)\
            .eq("user_id", user_id)\
            .is_("deleted_at", "null")\
            .execute()
        
        # 检查 result.data 是否有数据
        if force_result.data and len(force_result.data) > 0:
            is_force = True
            force_data = force_result.data[0]
        else:
            is_force = False
            
    except Exception as e:
        logger.warning(f"查询强制访谈记录失败: {e}")
        is_force = False
    
    inv = None
    
    if is_force and force_data:
        # ========== 强制访谈逻辑 ==========
        start_time = force_data.get('start_time')
        end_time = force_data.get('end_time')
        
        # 检查有效期
        if start_time:
            try:
                start_dt = datetime.fromisoformat(start_time)
                if now < start_dt:
                    flash("强制访谈尚未开始", "warning")
                    return redirect(url_for('dashboard'))
            except:
                pass

        if end_time:
            try:
                if isinstance(end_time, str):
                    end_str = end_time.replace('Z', '+00:00')
                    end_dt = datetime.fromisoformat(end_str)
                else:
                    end_dt = end_time
                
                now_utc = datetime.now(timezone.utc)
                
                if end_dt.tzinfo is None:
                    end_dt = end_dt.replace(tzinfo=timezone.utc)
                
                logger.info(f"强制访谈时间检查: now={now_utc.isoformat()}, end={end_dt.isoformat()}")
                
                if now_utc > end_dt:
                    logger.warning(f"强制访谈已过期，删除记录 {force_data['id']}")
                    admin_db.table("user_interview_force_records").update({
                        "deleted_at": now_utc.isoformat(),
                        "deleted_by": user_id
                    }).eq("id", force_data['id']).execute()
                    flash("强制访谈已过期", "warning")
                    return redirect(url_for('dashboard'))
            except Exception as e:
                logger.error(f"解析时间失败: {e}")
        
        # 获取原访谈信息
        inv_res = db.table("interviews").select("*").eq("id", interview_id).maybe_single().execute()
        if not inv_res.data:
            flash("访谈不存在", "danger")
            return redirect(url_for('dashboard'))
        inv = inv_res.data
        
        # 检查用户是否属于该访谈（强制访谈也需要检查）
        result = db.table("interview_results").select("interview_id").eq("interview_id", interview_id).eq("user_id", user_id).limit(1).execute()
        if not result.data:
            flash("您不在本次访谈名单中", "danger")
            return redirect(url_for('dashboard'))
        
        logger.info(f"用户 {user_id} 进入强制访谈 {interview_id}，有效期至 {end_time}")
        
    else:
        # ========== 普通访谈逻辑 ==========
        # 检查用户是否属于该访谈
        result = db.table("interview_results").select("interview_id").eq("interview_id", interview_id).eq("user_id", user_id).limit(1).execute()
        if not result.data:
            flash("您不在本次访谈名单中", "danger")
            return redirect(url_for('dashboard'))

        inv = db.table("interviews").select("*").eq("id", interview_id).maybe_single().execute()
        if not inv.data:
            flash("访谈不存在", "danger")
            return redirect(url_for('dashboard'))
        
        inv = inv.data
        
        # 检查普通访谈有效期
        start_time = inv.get('start_time')
        end_time = inv.get('end_time')
        
        if start_time:
            try:
                start_dt = datetime.fromisoformat(start_time)
                if now < start_dt:
                    flash("访谈尚未开始", "warning")
                    return redirect(url_for('dashboard'))
            except:
                pass
        
        if end_time:
            try:
                end_dt = datetime.fromisoformat(end_time)
                if now > end_dt:
                    flash("访谈已结束", "warning")
                    return redirect(url_for('dashboard'))
            except:
                pass
    
    # ========== 2. 获取题目（普通访谈和强制访谈共用）==========
    # 分步查询，避免外键歧义
    interview_results = db.table("interview_results") \
        .select("id, question_id, answer") \
        .eq("interview_id", interview_id) \
        .eq("user_id", user_id) \
        .execute()
    
    if not interview_results.data:
        questions = []
    else:
        question_ids = list(set([row['question_id'] for row in interview_results.data]))
        
        questions_data = db.table("questions") \
            .select("*") \
            .in_("id", question_ids) \
            .execute()
        
        question_map = {q['id']: q for q in (questions_data.data or [])}
        
        questions = []
        for row in interview_results.data:
            q = question_map.get(row['question_id'], {})
            q_copy = q.copy() if q else {}
            
            opts = q_copy.get('options', {})
            if isinstance(opts, str):
                try:
                    q_copy['options'] = json.loads(opts)
                except:
                    q_copy['options'] = {}
            
            if q_copy.get('type') == 'judge' and (not q_copy.get('options')):
                q_copy['options'] = {"A": "正确 True", "B": "错误 False"}
            
            if not isinstance(q_copy.get('options'), dict):
                q_copy['options'] = {}
        
            q_copy['interview_result_id'] = row['id']
            q_copy['user_answer'] = row.get('answer') or ''
            questions.append(q_copy)
    
    questions.sort(key=lambda x: x.get('num', 0))
    for idx, q in enumerate(questions, 1):
        q['num'] = idx
        if q.get('options'):
            q['options'] = {k: v for k, v in q['options'].items() if v.strip()}
        else:
            q['options'] = {}
    
    # 为模板添加额外信息
    interview_data = inv if inv else {}
    if is_force and force_data:
        # 强制访谈：覆盖标题和有效期显示
        interview_data = interview_data.copy() if interview_data else {}
        interview_data['title'] = force_data.get('title', interview_data.get('title', '强制访谈'))
        interview_data['start_time'] = force_data.get('start_time')
        interview_data['end_time'] = force_data.get('end_time')
        interview_data['is_force'] = True

    return render_template('exam/take_interview.html', interview=interview_data, questions=questions)

@admin_inspection_bp.route('/api/interview/<int:interview_id>/submit', methods=['POST'])
@login_required
def submit_interview(interview_id):
    """② 后端新增接口：提交学员的答案"""
    user_id = session['user_id']
    answers = request.json.get('answers', {})  # {result_id: answer}
    db = get_supabase()

    # 获取访谈级别的 feedback
    interview_res = db.table("interviews").select("feedback").eq("id", interview_id).maybe_single().execute()
    interview_feedback = interview_res.data.get('feedback', '') if interview_res.data else ''

    for rid, ans in answers.items():
        # 获取关联题目 ID
        result = db.table("interview_results").select("question_id").eq("id", rid).eq("user_id", user_id).maybe_single().execute()
        if not result.data:
            continue
        qid = result.data['question_id']
        # 获取标准答案和题型
        q = db.table("questions").select("answer, type").eq("id", qid).maybe_single().execute()
        is_correct = False
        if q.data:
            correct_ans = q.data['answer'].upper()
            q_type = q.data['type']
            if q_type == 'multi':
                u_set = set(ans.upper().replace(' ', ''))
                c_set = set(correct_ans.replace(' ', ''))
                is_correct = (u_set == c_set)
            elif q_type == 'judge':
                norm = ans.upper()
                if norm in ('A', 'T', '√', '正确', '对'): norm = 'T'
                elif norm in ('B', 'F', '×', '错误', '错'): norm = 'F'
                correct_std = correct_ans.replace('√', 'T').replace('×', 'F')
                is_correct = (norm == correct_std)
            else:
                is_correct = (ans.strip().upper() == correct_ans)
        db.table("interview_results").update({
            "answer": ans,
            "is_correct": is_correct,
            "submitted_at": datetime.now(timezone.utc).isoformat(),
            "feedback": interview_feedback
        }).eq("id", rid).eq("user_id", user_id).execute()
    return jsonify({"success": True, "message": "jsonify_interview_has_been_submitted", "params": []})

@admin_inspection_bp.route('/api/admin/interview/<int:interview_id>/user/<user_id>/answers')
@login_required
@admin_required
def get_interview_user_answers(interview_id, user_id):
    """获取指定访谈中指定用户的答题详情"""
    db = get_supabase()
    # 获取访谈信息
    inv_res = db.table("interviews").select("id, title").eq("id", interview_id).maybe_single().execute()
    if not inv_res.data:
        return jsonify({"error": "访谈不存在"}), 404

    # 分步查询
    # 第一步：获取用户的所有访谈结果
    interview_results = db.table("interview_results") \
        .select("id, question_id, answer, is_correct") \
        .eq("interview_id", interview_id) \
        .eq("user_id", user_id) \
        .execute()
    
    if not interview_results.data:
        return jsonify({"answers": []})
    
    # 第二步：收集 question_id
    question_ids = list(set([row['question_id'] for row in interview_results.data]))
    
    # 第三步：批量查询题目信息
    questions_data = db.table("questions") \
        .select("*") \
        .in_("id", question_ids) \
        .execute()
    
    # 第四步：构建映射
    question_map = {q['id']: q for q in (questions_data.data or [])}
    
    # 第五步：组装返回数据
    data = []
    for row in interview_results.data:
        q = question_map.get(row['question_id'], {})
        
        # 解析 options
        opts = q.get('options', {})
        if isinstance(opts, str):
            try:
                q['options'] = json.loads(opts)
            except:
                q['options'] = {}

        if q.get('type') == 'judge' and not q['options']:
            q['options'] = {"A": "正确 True", "B": "错误 False"}

        data.append({
            "question_num": q.get('num'),
            "question_content": q.get('content_cn') or q.get('content') or q.get('content_raw', ''),
            "question_type": q.get('type'),
            "options": q.get('options', {}),
            "user_answer": row.get('answer') or '',
            "is_correct": row.get('is_correct'),
            "result_id": row['id']
        })
    # 按题目编号排序
    data.sort(key=lambda x: x.get('question_num', 0))
    return jsonify({"answers": data})

@admin_inspection_bp.route('/api/admin/interview/<int:interview_id>/user/<user_id>/delete', methods=['DELETE'])
@login_required
@admin_required
def api_admin_delete_interview_user_result(interview_id, user_id):
    """删除指定用户在指定访谈中的所有答题记录"""
    db = get_supabase()
    operator_id = session['user_id']
    
    # 获取访谈信息
    interview_res = db.table("interviews").select("*").eq("id", interview_id).execute()
    if not interview_res.data:
        return jsonify({"success": False, "message": "访谈不存在"}), 404
    
    interview = interview_res.data[0]
    exam_id = interview.get('exam_id')
    
    # 检查考试的国家权限（支持多国家）
    exam_data = None
    if exam_id:
        exam_res = db.table("exams").select("countries, country").eq("id", exam_id).maybe_single().execute()
        if exam_res.data:
            exam_data = exam_res.data
    
    # 权限检查
    is_dev = is_developer()
    current_role = session.get('role')
    
    if not is_dev:
        if exam_data:
            exam_countries = parse_exam_countries(exam_data)
            allowed = get_admin_allowed_countries()
            
            if current_role == 'super_admin':
                if allowed is not None:
                    if not any(c in allowed for c in exam_countries):
                        return jsonify({"success": False, "message": "无权删除此访谈记录"}), 403
            elif current_role == 'admin':
                if allowed:
                    if not any(c in allowed for c in exam_countries):
                        return jsonify({"success": False, "message": "无权删除此访谈记录"}), 403
                else:
                    user_country = session.get('user_country')
                    if user_country not in exam_countries:
                        return jsonify({"success": False, "message": "无权删除此访谈记录"}), 403
            else:
                return jsonify({"success": False, "message": "无权删除此访谈记录"}), 403
        else:
            allowed = get_admin_allowed_countries()
            if allowed is not None and not allowed:
                return jsonify({"success": False, "message": "无权删除此访谈记录"}), 403
    
    try:
        # 软删除该用户的所有访谈答题记录
        db.table("interview_results").update({
            "deleted_at": datetime.now(timezone.utc).isoformat(),
            "deleted_by": operator_id
        }).eq("interview_id", interview_id).eq("user_id", user_id).execute()
        
        logger.info(f"访谈记录已删除: interview_id={interview_id}, user_id={user_id}, 操作人={operator_id}")
        return jsonify({"success": True, "message": "访谈记录已删除"})
    except Exception as e:
        logger.error(f"删除访谈记录失败: {e}")
        return jsonify({"success": False, "message": str(e)}), 500

@admin_inspection_bp.route('/api/admin/interview/<int:interview_id>/batch_delete', methods=['POST'])
@login_required
@admin_required
def api_admin_batch_delete_interview_results(interview_id):
    """批量删除访谈记录（支持软删除和永久删除）"""
    data = request.json
    user_ids = data.get('user_ids', [])
    delete_type = data.get('delete_type', 'soft')
    
    if not user_ids:
        return jsonify({"success": False, "message": "请选择要删除的访谈记录"}), 400
    
    db = get_supabase()
    operator_id = session['user_id']
    success_count = 0
    fail_count = 0
    errors = []
    
    # 获取访谈信息（权限检查）
    interview_res = db.table("interviews").select("*").eq("id", interview_id).execute()
    if not interview_res.data:
        return jsonify({"success": False, "message": "访谈不存在"}), 404
    
    interview = interview_res.data[0]
    exam_id = interview.get('exam_id')
    
    # 检查考试的国家权限（支持多国家）
    
    # 先获取考试信息
    exam_data = None
    if exam_id:
        exam_res = db.table("exams").select("countries, country").eq("id", exam_id).maybe_single().execute()
        if exam_res.data:
            exam_data = exam_res.data
    
    # 权限检查
    is_dev = is_developer()
    current_role = session.get('role')
    
    # 开发者可以删除任何记录
    if not is_dev:
        # 检查是否有权访问该考试
        if exam_data:
            exam_countries = parse_exam_countries(exam_data)
            allowed = get_admin_allowed_countries()
            
            if current_role == 'super_admin':
                if allowed is not None:
                    if not any(c in allowed for c in exam_countries):
                        return jsonify({"success": False, "message": "无权删除此访谈记录"}), 403
            elif current_role == 'admin':
                if allowed:
                    if not any(c in allowed for c in exam_countries):
                        return jsonify({"success": False, "message": "无权删除此访谈记录"}), 403
                else:
                    user_country = session.get('user_country')
                    if user_country not in exam_countries:
                        return jsonify({"success": False, "message": "无权删除此访谈记录"}), 403
            else:
                return jsonify({"success": False, "message": "无权删除此访谈记录"}), 403
        else:
            # 没有关联考试，需要检查其他权限逻辑
            allowed = get_admin_allowed_countries()
            if allowed is not None and not allowed:
                return jsonify({"success": False, "message": "无权删除此访谈记录"}), 403
    
    # 执行删除
    for user_id in user_ids:
        try:
            if delete_type == 'hard':
                db.table("interview_results").delete().eq("interview_id", interview_id).eq("user_id", user_id).execute()
            else:
                db.table("interview_results").update({
                    "deleted_at": datetime.now(timezone.utc).isoformat(),
                    "deleted_by": operator_id
                }).eq("interview_id", interview_id).eq("user_id", user_id).execute()
            success_count += 1
        except Exception as e:
            fail_count += 1
            errors.append(f"用户 {user_id}: {str(e)}")
    
    return jsonify({
        "success": True,
        "success_count": success_count,
        "fail_count": fail_count,
        "errors": errors[:10]
    })

@admin_inspection_bp.route('/api/admin/interviews/<int:interview_id>', methods=['DELETE'])
@login_required
@admin_required
def api_admin_delete_interview_by_id(interview_id):
    """删除访谈（软删除）- 供 list_inspection.html 调用"""
    db = get_supabase()
    operator_id = session['user_id']
    
    # 获取访谈信息（用于权限检查）
    interview_res = db.table("interviews").select("*, exams(country), created_by").eq("id", interview_id).execute()
    if not interview_res.data:
        return jsonify({"success": False, "message": "访谈不存在"}), 404
    
    interview = interview_res.data[0]
    exam_data = interview.get('exams', {})
    created_by = interview.get('created_by')
    
    # 权限检查
    allowed = get_admin_allowed_countries()
    if allowed is not None:
        exam_country = exam_data.get('country') if isinstance(exam_data, dict) else None
        if exam_country and exam_country not in allowed:
            return jsonify({"success": False, "message": "jsonify_no_authorith_delete_project", "params": []}), 403

    # 创建者检查（非超管/开发者）
    if not is_dev and current_role != 'super_admin':
        if created_by != current_user_id:
            return jsonify({"success": False, "message": "jsonify_no_permmission_delete_item_created_by_others", "params": []}), 403
    
    try:
        now_utc = datetime.now(timezone.utc).isoformat()
        
        # 软删除访谈本身
        db.table("interviews").update({
            "deleted_at": now_utc,
            "deleted_by": operator_id
        }).eq("id", interview_id).execute()
        
        # 同时软删除该访谈下的所有答题记录
        db.table("interview_results").update({
            "deleted_at": now_utc,
            "deleted_by": operator_id
        }).eq("interview_id", interview_id).execute()
        
        logger.info(f"访谈已删除: interview_id={interview_id}, 操作人={operator_id}")
        return jsonify({"success": True, "message": "访谈已删除"})
    except Exception as e:
        logger.error(f"删除访谈失败: {e}")
        return jsonify({"success": False, "message": str(e)}), 500

@admin_inspection_bp.route('/api/admin/interview/<int:interview_id>/user/<user_id>/resample', methods=['POST'])
@login_required
@admin_required
def api_admin_resample_interview(interview_id, user_id):
    """重新为指定用户抽题（先删除旧记录，再插入新记录）"""
    db = get_supabase()
    
    # 获取访谈信息
    interview_res = db.table("interviews").select("exam_id, question_count").eq("id", interview_id).execute()
    if not interview_res.data:
        return jsonify({"success": False, "message": "interview_not_found", "params": []}), 404
    
    interview = interview_res.data[0]
    exam_id = interview['exam_id']
    question_count = interview['question_count']
    
    # 检查题库
    q_check = db.table("questions").select("id").eq("exam_id", exam_id).limit(1).execute()
    if not q_check.data:
        return jsonify({"success": False, "message": "jsonify_no_questions_in_exam", "params": []}), 400
    
    try:
        # 1. 先删除该用户在该访谈下的所有旧记录（硬删除）
        delete_result = db.table("interview_results").delete() \
            .eq("interview_id", interview_id) \
            .eq("user_id", user_id) \
            .execute()
        
        deleted_count = len(delete_result.data) if delete_result.data else 0
        logger.info(f"已删除用户 {user_id} 的 {deleted_count} 条旧访谈记录")
        
        # 2. 重新抽题
        questions = random_pick_questions(exam_id, question_count)
        inserted_count = 0
        for q in questions:
            result = db.table("interview_results").insert({
                "interview_id": interview_id,
                "user_id": user_id,
                "question_id": q['id'],
                "created_at": datetime.now(timezone.utc).isoformat(),
                "answer": None,
                "is_correct": None,
                "submitted_at": None
            }).execute()
            if result.data:
                inserted_count += 1
        
        logger.info(f"已为用户 {user_id} 插入 {inserted_count} 条新访谈题目")
        
        # 使用翻译键名 + 参数 
        # return jsonify({"success": True, "message": f"重新抽题成功，已删除 {deleted_count} 条旧记录，新增 {inserted_count} 道题目"})
        return jsonify({
            "success": True, 
            "message": "jsonify_resample_success", "params": [deleted_count, inserted_count]
        })

    except Exception as e:
        logger.error(f"重新抽题失败: {e}")
        return jsonify({
            "success": False, 
            "message": "jsonify_resample_failed", "params": [str(e)]
        }), 500

@admin_inspection_bp.route('/api/admin/interviewee/stats')
@login_required
@admin_required
def admin_interviewee_stats():
    """访谈统计页面入口（二级菜单）"""
    # 此函数仅渲染模板，实际数据由前端 AJAX 请求 /api/admin/interview/<id>/details 获取
    return render_template('admin/list_inspection.html')

@admin_inspection_bp.route('/api/admin/interview/<int:interview_id>/details')
@login_required
@admin_required
def api_interview_details(interview_id):
    """获取访谈详情数据（支持筛选和分页）"""
    db = get_supabase()
    search = request.args.get('search', '')
    country = request.args.get('country', '')
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 20, type=int)

    # 获取访谈信息
    inv_res = db.table("interviews").select("*").eq("id", interview_id).maybe_single().execute()
    if not inv_res.data:
        return jsonify({"error": "访谈不存在"}), 404
    interview = inv_res.data

    # 获取考试标题
    exam_title = ''
    if interview.get('exam_id'):
        exam_res = db.table("exams").select("title").eq("id", interview['exam_id']).maybe_single().execute()
        if exam_res.data:
            exam_title = exam_res.data['title']

    # 获取访谈级别的反馈人
    interview_feedback = interview.get('feedback', '')

    # 查询该访谈的所有答题记录（按用户聚合）
    query = db.table("interview_results").select("*").eq("interview_id", interview_id)
    # 先获取所有记录，然后在 Python 中处理筛选和聚合
    all_res = query.execute()
    all_data = all_res.data or []

    # ===== 国家权限过滤 =====
    allowed = get_allowed_countries()
    if allowed is not None:
        if not allowed:
            # 没有允许的国家，直接返回空
            return jsonify({
                "interview": {
                    "id": interview_id,
                    "title": interview.get('title'),
                    "exam_title": exam_title,
                    "exam_id": interview.get('exam_id'),
                    "reviewer": interview.get('reviewer', ''),
                    "feedback": interview_feedback
                },
                "data": [],
                "total": 0,
                "page": page,
                "per_page": per_page
            })
        # 收集所有 user_id
        all_user_ids = list(set(r['user_id'] for r in all_data))
        # 查询这些用户的国家
        users_country_res = db.table("users").select("id, country").in_("id", all_user_ids).execute()
        allowed_ids = {u['id'] for u in (users_country_res.data or []) if u.get('country') in allowed}
        # 过滤数据
        all_data = [r for r in all_data if r['user_id'] in allowed_ids]
    # ======================
    # 批量获取用户信息
    user_ids = list(set(r['user_id'] for r in all_data))
    users_map = {}
    if user_ids:
        users_res = db.table("users").select("id, name_cn, name_en, email, country, wh_id, department, is_resign").in_("id", user_ids).eq("is_resign", False).execute()
        for u in (users_res.data or []):
            users_map[u['id']] = u

    # 过滤掉离职人员的记录
    all_data = [r for r in all_data if r['user_id'] in users_map]

    # 按用户聚合
    user_results = {}
    for row in all_data:
        uid = row['user_id']
        user_info = users_map.get(uid, {})
        # 筛选：姓名搜索
        if search:
            name = user_info.get('name_cn') or user_info.get('name_en', '')
            if search.lower() not in name.lower():
                continue
        # 筛选：国家
        if country and user_info.get('country') != country:
            continue

        if uid not in user_results:
            user_results[uid] = {
                "user_id": uid,
                "name": user_info.get('name_cn') or user_info.get('name_en', ''),
                "email": user_info.get('email'),
                "country": user_info.get('country', ''),
                "wh_id": user_info.get('wh_id', ''),
                "department": user_info.get('department', ''),
                "submitted_at": None,
                "total_questions": 0,
                "correct_count": 0,
                "feedback": "",   # 可暂合并所有 feedback
                "reviewer": interview.get('reviewer', ''),
                "interview_feedback": interview_feedback,
                "results": []
            }
        user_results[uid]["total_questions"] += 1
        if row.get('is_correct'):
            user_results[uid]["correct_count"] += 1
        if row.get('submitted_at') and (not user_results[uid]["submitted_at"] or row['submitted_at'] > user_results[uid]["submitted_at"]):
            user_results[uid]["submitted_at"] = row['submitted_at']

    # 转换为列表
    detail_list = []
    for uid, data in user_results.items():
        # 计算学员状态
        has_submitted = bool(data.get("submitted_at"))
        total_questions = data.get("total_questions", 0)
        answered_count = len([r for r in data.get("results", []) if r.get('answer')])
        
        if has_submitted or answered_count == total_questions:
            user_status = 'completed'
        else:
            # 检查访谈有效期
            start_time = interview.get('start_time')
            end_time = interview.get('end_time')
            now = datetime.now(timezone.utc).isoformat()
            
            if end_time and now > end_time:
                user_status = 'unfinished'  # 已过有效期未完成
            elif start_time and now < start_time:
                user_status = 'pending'     # 未开始
            else:
                user_status = 'ongoing'      # 进行中

        detail_list.append({
            "user_id": data["user_id"],
            "name": data["name"],
            "email": data['email'],
            "country": data["country"],
            "wh_id": data["wh_id"],
            "department": data["department"],
            "submitted_at": data["submitted_at"],
            "reviewer": data["reviewer"],
            "feedback": data["interview_feedback"],
            "total_questions": data["total_questions"],
            "correct_count": data["correct_count"],
            "feedback": data["feedback"],
            "has_submitted": bool(data["submitted_at"]),
            "user_status": user_status,
            "answered_count": answered_count,  # 已答题数量
            "can_force": user_status == 'unfinished'  # 是否可强制访谈
        })

    # 分页
    total = len(detail_list)
    start = (page - 1) * per_page
    end = start + per_page
    paginated = detail_list[start:end]

    return jsonify({
        "interview": {
            "id": interview_id,
            "title": interview.get('title'),
            "exam_title": exam_title,
            "exam_id": interview.get('exam_id'),
            "reviewer": interview.get('reviewer', ''),
            "feedback": interview_feedback
        },
        "data": paginated,
        "total": total,
        "page": page,
        "per_page": per_page
    })

@admin_inspection_bp.route('/admin/interview/<int:interview_id>/details')
@login_required
@admin_required
def admin_interview_details_page(interview_id):
    """访谈详情页面（渲染HTML）"""
    return render_template('admin/list_inspection_details.html', interview_id=interview_id)

@admin_inspection_bp.route('/api/admin/interview/<int:interview_id>/force_resample', methods=['POST'])
@login_required
@admin_required
def force_resample_interview(interview_id):
    """
    强制访谈：重新为指定用户抽题，设置2小时有效期
    """
    data = request.json
    user_ids = data.get('user_ids', [])
    
    if not user_ids:
        return jsonify({"success": False, "message": "please_select_users_for_force_interview", "params": []}), 400
    
    db = get_supabase()
    operator_id = session['user_id']
    
    # 获取原访谈信息
    interview_res = db.table("interviews").select("*").eq("id", interview_id).execute()
    if not interview_res.data:
        return jsonify({"success": False, "message": "interview_not_found", "params": []}), 404
    
    original_interview = interview_res.data[0]
    exam_id = original_interview.get('exam_id')
    question_count = original_interview.get('question_count', 5)
    reviewer = original_interview.get('reviewer', '')
    feedback = original_interview.get('feedback', '')
    title = original_interview.get('title', '访谈')
    
    # 检查 exam_id 是否存在
    if not exam_id:
        return jsonify({"success": False, "message": "interview_no_have_related_exam", "params": []}), 400
    
    # 检查题目是否存在
    q_check = db.table("questions").select("id").eq("exam_id", exam_id).limit(1).execute()
    if not q_check.data:
        return jsonify({"success": False, "message": "exam_no_have_questions", "params": []}), 400
    
    # 权限检查
    from utils.permissions import get_admin_allowed_countries
    allowed = get_admin_allowed_countries()
    if allowed is not None:
        exam_res = db.table("exams").select("countries, country").eq("id", exam_id).maybe_single().execute()
        if exam_res.data:

            exam_countries = parse_exam_countries(exam_res.data)
            if not any(c in allowed for c in exam_countries):
                return jsonify({"success": False, "message": "no_permission_for_interview", "params": []}), 403
    
    success_count = 0
    failed_count = 0
    skipped_count = 0
    errors = []
    
    # 设置强制访谈有效期：当前时间 + 2小时
    now = datetime.now(timezone.utc)
    force_start_time = now.isoformat()
    force_end_time = (now + timedelta(hours=2)).isoformat()
    
    for user_id in user_ids:
        try:
            logger.info(f"处理用户 {user_id}")
            
            # 1. 检查用户是否存在
            user_check = db.table("users").select("id, name_en").eq("id", user_id).maybe_single().execute()
            if not user_check.data:
                logger.error(f"用户 {user_id} 不存在")
                failed_count += 1
                errors.append(f"用户 {user_id}: 用户不存在")
                continue
            
            # 2. 检查用户是否已有答题记录（已完成）
            existing_results = db.table("interview_results").select("id, answer").eq("interview_id", interview_id).eq("user_id", user_id).execute()
            
            # 检查是否已完成答题
            if existing_results.data:
                all_answered = all(r.get('answer') for r in existing_results.data)
                if all_answered:
                    skipped_count += 1
                    errors.append(f"用户 {user_id} 已完成访谈，跳过")
                    logger.info(f"用户 {user_id} 已完成，跳过")
                    continue
            
            # 3. 先删除该用户的所有旧强制访谈记录（包括已软删除的）
            # 彻底清理，避免新旧记录冲突
            delete_result = db.table("user_interview_force_records").delete() \
                .eq("user_id", user_id) \
                .eq("original_interview_id", interview_id) \
                .execute()
            logger.info(f"用户 {user_id} 删除旧强制访谈记录，删除数量: {len(delete_result.data or []) if delete_result.data else 0}")
            
            # 4. 删除该用户的旧访谈结果记录
            db.table("interview_results").delete().eq("interview_id", interview_id).eq("user_id", user_id).execute()
            logger.info(f"用户 {user_id} 删除旧访谈结果记录")
            
            # 5. 重新抽题
            from routes.helpers import random_pick_questions
            questions = random_pick_questions(exam_id, question_count)
            logger.info(f"用户 {user_id} 抽取到 {len(questions)} 道题目")
            
            if not questions:
                logger.error(f"用户 {user_id} 抽题失败，没有题目")
                failed_count += 1
                errors.append(f"用户 {user_id}: 抽题失败，没有题目")
                continue
            
            for q in questions:
                db.table("interview_results").insert({
                    "interview_id": interview_id,
                    "user_id": user_id,
                    "question_id": q['id'],
                    "created_at": now.isoformat(),
                    "feedback": feedback
                }).execute()
                logger.debug(f"插入题目 {q['id']} 成功")
            
            # 6. 创建全新的强制访谈记录
            db.table("user_interview_force_records").insert({
                "user_id": user_id,
                "original_interview_id": interview_id,
                "exam_id": exam_id,
                "title": f"{title} (强制访谈)",
                "question_count": question_count,
                "reviewer": reviewer,
                "feedback": feedback,
                "start_time": force_start_time,
                "end_time": force_end_time,
                "created_at": now.isoformat(),
                "created_by": operator_id
            }).execute()
            logger.info(f"用户 {user_id} 创建新的强制访谈记录")
            
            success_count += 1
            
        except Exception as e:
            failed_count += 1
            error_msg = str(e)
            errors.append(f"用户 {user_id}: {error_msg}")
            logger.error(f"用户 {user_id} 强制访谈失败: {error_msg}", exc_info=True)
    
    logger.info(f"强制访谈完成: interview={interview_id}, 成功={success_count}, 跳过={skipped_count}, 失败={failed_count}")
    
    return jsonify({
        "success": True,
        "success_count": success_count,
        "skipped_count": skipped_count,
        "failed_count": failed_count,
        # "message": f"强制访谈完成: 成功 {success_count} 人，跳过已完成 {skipped_count} 人，失败 {failed_count} 人，有效期2小时",
        "message": "force_interview_complete", "params": [success_count, skipped_count, failed_count],
        "errors": errors[:10]
    })
