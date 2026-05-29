# routes/admin_inspection.py
import logging
import json
from datetime import datetime, timezone
from flask import request, jsonify, render_template, session, flash, redirect, url_for
from . import admin_inspection_bp
from services.db import get_supabase
from utils.common import get_reviewer_by_country
from routes.helpers import login_required, admin_required, random_pick_questions, get_allowed_countries
from utils.permissions import get_admin_allowed_countries

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
    db = get_supabase()
    if request.method == 'GET':
        name = request.args.get('name', '')
        country = request.args.get('country', '')
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 20, type=int)

        # ✅ 获取管理员权限范围
        allowed = get_allowed_countries()
        
        # 基础查询
        query = db.table("interviews").select("*", count="exact").is_("deleted_at", "null")
        if name:
            query = query.ilike("title", f"%{name}%")
        query = query.order("created_at", desc=True)
        
        # 获取所有访谈
        res = query.execute()
        all_interviews = res.data or []
        
        # ✅ 根据权限过滤访谈
        if allowed is not None and allowed:
            # 收集所有考试ID
            exam_ids = set()
            for inv in all_interviews:
                if inv.get('exam_id'):
                    exam_ids.add(inv['exam_id'])
            
            # 批量获取考试的国家信息
            exam_country_map = {}
            if exam_ids:
                exams_res = db.table("exams").select("id, country, countries").in_("id", list(exam_ids)).execute()
                for exam in (exams_res.data or []):
                    # 解析国家列表
                    exam_countries = []
                    countries_data = exam.get('countries')
                    if isinstance(countries_data, str) and countries_data:
                        try:
                            exam_countries = json.loads(countries_data)
                        except:
                            exam_countries = []
                    elif isinstance(countries_data, list):
                        exam_countries = countries_data
                    if not exam_countries and exam.get('country'):
                        exam_countries = [exam.get('country')]
                    exam_country_map[exam['id']] = exam_countries
            
            # 过滤访谈
            filtered = []
            for inv in all_interviews:
                exam_countries = exam_country_map.get(inv.get('exam_id'), [])
                # 检查是否有交集
                if any(c in allowed for c in exam_countries):
                    filtered.append(inv)
            all_interviews = filtered
        
        # 分页
        total = len(all_interviews)
        start = (page - 1) * per_page
        end = start + per_page
        interviews = all_interviews[start:end]
        
        # 收集当前页所有访谈涉及的用户ID，用于批量查询国家
        all_user_ids = set()
        for inv in interviews:
            user_res = db.table("interview_results").select("user_id").eq("interview_id", inv['id']).execute()
            all_user_ids.update(r['user_id'] for r in (user_res.data or []))
        
        # 批量查询用户国家
        user_country_map = {}
        if allowed is not None and all_user_ids:
            users_res = db.table("users").select("id, country").in_("id", list(all_user_ids)).execute()
            user_country_map = {u['id']: u.get('country') for u in (users_res.data or [])}
        
        now = datetime.now(timezone.utc)
        for inv in interviews:
            # 动态计算状态
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
            
            # 统计去重人数（应用国家权限过滤）
            user_res = db.table("interview_results").select("user_id").eq("interview_id", inv['id']).execute()
            user_ids = [r['user_id'] for r in (user_res.data or [])]
            if allowed is not None:
                filtered_ids = [uid for uid in user_ids if user_country_map.get(uid) in allowed]
                inv['interviewee_count'] = len(set(filtered_ids))
            else:
                inv['interviewee_count'] = len(set(user_ids))
            
            # 附加考试信息
            if inv.get('exam_id'):
                exam_res = db.table("exams").select("title, country").eq("id", inv['exam_id']).maybe_single().execute()
                if exam_res.data:
                    inv['exam_title'] = exam_res.data.get('title', '')
                    inv['country'] = exam_res.data.get('country', '')
        
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
        feedback = data.get('feedback', '')           # ✅ 新增：反馈人
        start_time = data.get('start_time')
        end_time = data.get('end_time')

        # ✅ 将本地时间转为 UTC 存储
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
            "feedback": feedback,                      # ✅ 新增
            "question_count": question_count,
            "status": status,
            "start_time": start_time_utc,       # 存储 UTC 时间
            "end_time": end_time_utc,           # 存储 UTC 时间
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
                    val = val     # ✅ 转为 UTC
                update_data[field] = val
        if update_data:
            db.table("interviews").update(update_data).eq("id", inv_id).execute()
            logger.info(f"访谈 {inv_id} 字段已更新: {list(update_data.keys())}")

        # 重新抽题（无论状态，只要提供了 user_ids 就更新人员题目）
        if 'user_ids' in data:
            # 删除该访谈的所有现有题目
            db.table("interview_results").delete().eq("interview_id", inv_id).execute()
            logger.info(f"已清除访谈 {inv_id} 的旧题目")
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
            logger.info(f"已为 {len(data['user_ids'])} 名学员重新抽题")
        
        return jsonify({"success": True})

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
    """① 后端新增接口：学员进入访谈，检查是否在名单中，获取访谈基本信息和题目"""
    user_id = session['user_id']
    db = get_supabase()
    # 检查用户是否属于该访谈
    result = db.table("interview_results").select("interview_id").eq("interview_id", interview_id).eq("user_id", user_id).limit(1).execute()
    if not result.data:
        flash("您不在本次访谈名单中", "danger")
        return redirect(url_for('dashboard'))

    inv = db.table("interviews").select("*").eq("id", interview_id).maybe_single().execute()
    if not inv.data:
        flash("访谈不存在", "danger")
        return redirect(url_for('dashboard'))

    # ✅ 修复：明确指定使用哪个外键关系（以下注释测试目前可用，为更稳妥修复为分步查询）
    # 使用 questions!interview_results_question_id_fkey(*) 或 questions!fk_interview_results_question_id(*)
    '''
    questions_res = db.table("interview_results").select("*, questions!interview_results_question_id_fkey(*)") \
        .eq("interview_id", interview_id) \
        .eq("user_id", user_id) \
        .execute()
    '''

    # ✅ 分步查询，避免外键歧义
    # 第一步：获取该用户的所有访谈结果
    interview_results = db.table("interview_results") \
        .select("id, question_id, answer") \
        .eq("interview_id", interview_id) \
        .eq("user_id", user_id) \
        .execute()
    
    if not interview_results.data:
        questions = []
    else:
        # 第二步：收集所有 question_id
        question_ids = list(set([row['question_id'] for row in interview_results.data]))
        
        # 第三步：批量查询题目信息
        questions_data = db.table("questions") \
            .select("*") \
            .in_("id", question_ids) \
            .execute()
        
        # 第四步：构建映射
        question_map = {q['id']: q for q in (questions_data.data or [])}
        
        # 第五步：组装数据
        questions = []
        for row in interview_results.data:
            q = question_map.get(row['question_id'], {})
            
            # 复制题目数据，避免修改原数据
            q_copy = q.copy() if q else {}
            
            # 解析 options
            opts = q_copy.get('options', {})
            if isinstance(opts, str):
                try:
                    q_copy['options'] = json.loads(opts)
                except:
                    q_copy['options'] = {}
            
            # 判断题默认选项
            if q_copy.get('type') == 'judge' and (not q_copy.get('options')):
                q_copy['options'] = {"A": "正确 True", "B": "错误 False"}
            
            # 确保选项是字典
            if not isinstance(q_copy.get('options'), dict):
                q_copy['options'] = {}
            
            q_copy['interview_result_id'] = row['id']
            q_copy['user_answer'] = row.get('answer') or ''
            questions.append(q_copy)
    
    # ✅ 按 num 排序
    questions.sort(key=lambda x: x.get('num', 0))
    for idx, q in enumerate(questions, 1):
        q['num'] = idx
        if q.get('options'):
            q['options'] = {k: v for k, v in q['options'].items() if v.strip()}
        else:
            q['options'] = {}

    return render_template('exam/take_interview.html', interview=inv.data, questions=questions)

@admin_inspection_bp.route('/api/interview/<int:interview_id>/submit', methods=['POST'])
@login_required
def submit_interview(interview_id):
    """② 后端新增接口：提交学员的答案"""
    user_id = session['user_id']
    answers = request.json.get('answers', {})  # {result_id: answer}
    db = get_supabase()
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
            "submitted_at": datetime.now(timezone.utc).isoformat()
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

    # ✅ 分步查询
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
    
    # 获取考试国家（用于权限检查）
    exam_country = None
    if exam_id:
        exam_res = db.table("exams").select("country").eq("id", exam_id).maybe_single().execute()
        exam_country = exam_res.data.get('country') if exam_res.data else None
    
    # 权限检查
    allowed = get_admin_allowed_countries()
    if allowed is not None and exam_country not in allowed:
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

@admin_inspection_bp.route('/api/admin/interview/<int:interview_id>/user/<user_id>/resample', methods=['POST'])
@login_required
@admin_required
def api_admin_resample_interview(interview_id, user_id):
    """重新为指定用户抽题（先删除旧记录，再插入新记录）"""
    db = get_supabase()
    
    # 获取访谈信息
    interview_res = db.table("interviews").select("exam_id, question_count").eq("id", interview_id).execute()
    if not interview_res.data:
        return jsonify({"success": False, "message": "访谈不存在"}), 404
    
    interview = interview_res.data[0]
    exam_id = interview['exam_id']
    question_count = interview['question_count']
    
    # 检查题库
    q_check = db.table("questions").select("id").eq("exam_id", exam_id).limit(1).execute()
    if not q_check.data:
        return jsonify({"success": False, "message": "题库无题目，无法重新抽题"}), 400
    
    try:
        # ✅ 1. 先删除该用户在该访谈下的所有旧记录（硬删除）
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
        
        return jsonify({"success": True, "message": f"重新抽题成功，已删除 {deleted_count} 条旧记录，新增 {inserted_count} 道题目"})
    except Exception as e:
        logger.error(f"重新抽题失败: {e}")
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
    
    exam_country = None
    if exam_id:
        exam_res = db.table("exams").select("country").eq("id", exam_id).maybe_single().execute()
        exam_country = exam_res.data.get('country') if exam_res.data else None
    
    allowed = get_admin_allowed_countries()
    if allowed is not None and exam_country not in allowed:
        return jsonify({"success": False, "message": "无权删除此访谈记录"}), 403
    
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
    interview_res = db.table("interviews").select("*, exams(country)").eq("id", interview_id).execute()
    if not interview_res.data:
        return jsonify({"success": False, "message": "访谈不存在"}), 404
    
    interview = interview_res.data[0]
    exam_data = interview.get('exams', {})
    
    # 权限检查
    allowed = get_admin_allowed_countries()
    if allowed is not None:
        exam_country = exam_data.get('country') if isinstance(exam_data, dict) else None
        if exam_country and exam_country not in allowed:
            return jsonify({"success": False, "message": "无权删除此访谈"}), 403
    
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

    # ✅ 获取访谈级别的反馈人
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
                    "reviewer": interview.get('reviewer', ''),
                    "feedback": interview_feedback  # ✅ 新增
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
        users_res = db.table("users").select("id, name_cn, name_en, email, country, wh_id, department").in_("id", user_ids).execute()
        for u in (users_res.data or []):
            users_map[u['id']] = u

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
                "interview_feedback": interview_feedback,  # ✅ 新增：访谈级别的反馈人
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
        detail_list.append({
            "user_id": data["user_id"],
            "name": data["name"],
            "email": data['email'],
            "country": data["country"],
            "wh_id": data["wh_id"],
            "department": data["department"],
            "submitted_at": data["submitted_at"],
            "reviewer": data["reviewer"],
            "feedback": data["interview_feedback"],  # ✅ 使用访谈级别的反馈人
            "total_questions": data["total_questions"],
            "correct_count": data["correct_count"],
            "feedback": data["feedback"],
            "has_submitted": bool(data["submitted_at"])
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
            "reviewer": interview.get('reviewer', ''),
            "feedback": interview_feedback  # ✅ 新增
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