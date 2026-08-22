# routes/api_exam.py
import logging
import json
from . import exam_bp
import os
import re
from dateutil import parser
from datetime import datetime, timezone, timedelta
from flask import (
    Flask, render_template, request, redirect, url_for, 
    session, flash, jsonify, send_file
)
from utils.common import get_reviewer_by_country
from routes.helpers import login_required, admin_required, robust_parse_json, get_default_reviewer_by_country, safe_parse_datetime
from services.db import get_supabase, get_supabase_admin
from services import auth, exam, export
from utils.status import get_exam_status
from utils.manage_messages import log_user_login
from utils.training_helpers import parse_training_countries
from utils.permissions import is_developer
logger = logging.getLogger(__name__)

@exam_bp.route('/dashboard')
@login_required
def dashboard():
    db = get_supabase()
    admin_db = get_supabase_admin()
    
    user_id = session['user_id']
    now = datetime.now(timezone.utc)

    # 获取当前用户的国家
    user_info = db.table("users").select("country").eq("id", user_id).single().execute()
    user_country = user_info.data.get('country') if user_info.data else None

    # 初始化变量
    force_exam_ids = {}
    
    # 查询强制重推记录
    try:
        force_records = admin_db.table("user_exam_force_records").select("*")\
            .eq("user_id", user_id)\
            .is_("deleted_at", "null")\
            .execute()
        
        logger.info(f"用户 {user_id} 强制重推记录查询结果: {force_records.data}")
        
        for fr in (force_records.data or []):
            original_exam_id = fr.get('original_exam_id')
            if original_exam_id:
                end_time = fr.get('end_time')
                if end_time:
                    try:
                        end_dt = datetime.fromisoformat(end_time.replace('Z', '+00:00'))
                        if now < end_dt:
                            force_exam_ids[original_exam_id] = fr
                            logger.info(f"有效强制重推考试: {original_exam_id}")
                        else:
                            # 过期，删除
                            admin_db.table("user_exam_force_records").update({
                                "deleted_at": now.isoformat(),
                                "deleted_by": user_id
                            }).eq("id", fr['id']).execute()
                            logger.info(f"过期强制重推考试已删除: {original_exam_id}")
                    except Exception as e:
                        logger.warning(f"解析强制重推时间失败: {e}")
    except Exception as e:
        if 'timeout' in str(e).lower() or 'timed out' in str(e).lower():
            logger.warning(f"查询强制重推记录超时，跳过: {e}")
        else:
            raise e
        force_exam_ids = {}
    
    logger.info(f"最终 force_exam_ids: {list(force_exam_ids.keys())}")
    
    try:
        # 获取分配关系
        assign_res = db.table("exam_assignments").select("exam_id").eq("user_id", user_id).execute()
        assigned_exam_ids = {a['exam_id'] for a in assign_res.data} if assign_res.data else set()
        
        # 一次性获取所有考试状态
        all_status_res = db.table("user_exam_status").select("*").eq("user_id", user_id).execute()
        status_map = {}
        for s in (all_status_res.data or []):
            status_map[s['exam_id']] = s
        
        # 获取所有已开始且未提交的考试
        started_exams = set()
        for exam_id, status in status_map.items():
            if status.get('started_at') and not status.get('is_submitted'):
                started_exams.add(exam_id)
        
        # 获取所有考试
        exams_res = db.table("exams").select("*").execute()
        exams = []

        for ex in exams_res.data or []:
            exam_id = ex['id']

            # 检查是否有任何分配记录
            any_assign = db.table("exam_assignments").select("id").eq("exam_id", exam_id).limit(1).execute()
            if any_assign.data and exam_id not in assigned_exam_ids:
                continue

            # 关键修复：绑定模式的考试处理
            is_binding_exam = ex.get('is_binding_exam', False)

            if is_binding_exam:
                # 绑定模式的考试：只有被分配到 exam_assignments 的学员才能看到
                # 如果用户没有被分配，即使考试是 active 状态，也不显示
                if exam_id not in assigned_exam_ids:
                    continue
                # 如果被分配了，强制设置为 active 状态（如果尚未激活）
                if not ex.get('is_active'):
                    db.table("exams").update({"is_active": True, "status": "active"}).eq("id", exam_id).execute()
                    ex['is_active'] = True
                    ex['status'] = 'active'
            
            # 获取该用户的考试状态
            user_status = status_map.get(exam_id, {})
            
            # 已提交的考试直接跳过
            if user_status.get('is_submitted', False):
                continue

            # ========== 关键：强制重推考试强制显示 ==========
            is_force_exam = exam_id in force_exam_ids

            # 国家过滤（强制重推考试跳过国家过滤）
            if not is_force_exam:
                exam_countries = []
                countries_data = ex.get('countries') or ex.get('country', '')
                if isinstance(countries_data, str):
                    try:
                        exam_countries = json.loads(countries_data)
                    except:
                        exam_countries = [countries_data] if countries_data else []
                elif isinstance(countries_data, list):
                    exam_countries = countries_data
                else:
                    exam_countries = []
                
                if exam_countries and user_country:
                    if user_country not in exam_countries:
                        continue
            
            # 获取考试状态
            has_started = exam_id in started_exams
            status = get_exam_status(ex, has_started=has_started)
            
            # 强制重推考试忽略原状态
            if not is_force_exam:
                if status not in ('created', 'active'):
                    continue
            else:
                logger.info(f"用户 {user_id} 有强制重推考试 {exam_id}，强制显示")
                status = 'active'
                
            # 获取题目数量
            q_count = db.table("questions").select("*", count="exact").eq("exam_id", exam_id).execute()
            ex['questions_count'] = q_count.count if hasattr(q_count, 'count') else len(q_count.data or [])
            
            # 构建用户状态
            ex['user_status'] = {
                'is_submitted': False,
                'started': bool(user_status.get('started_at')),
                'remaining': None
            }
            
            # 计算剩余时间
            if user_status.get('started_at') and not user_status.get('is_submitted'):
                try:
                    start_dt = datetime.fromisoformat(user_status['started_at'])
                    elapsed = (now - start_dt).total_seconds()
                    
                    if is_force_exam:
                        force_data = force_exam_ids[exam_id]
                        total_seconds = force_data.get('duration', ex.get('duration', 60)) * 60
                    else:
                        total_seconds = ex.get('duration', 60) * 60
                    
                    remaining = max(0, total_seconds - int(elapsed))
                    ex['user_status']['remaining'] = remaining
                except Exception as e:
                    logger.warning(f"计算剩余时间失败: {e}")
            
            # 判断是否可进入
            can_enter = True if has_started else False
            if not can_enter and ex.get('start_time'):
                try:
                    start_dt = datetime.fromisoformat(ex['start_time'])
                    can_enter = now >= start_dt
                except Exception as e:
                    logger.warning(f"解析开始时间失败: {e}")
                    can_enter = False

            # 强制重推考试强制允许进入
            if is_force_exam:
                can_enter = True
                if not ex.get('start_time'):
                    ex['start_time'] = now.isoformat()
                if not ex.get('end_time'):
                    ex['end_time'] = (now + timedelta(hours=2)).isoformat()
            
            if has_started:
                can_enter = True
            
            ex['can_enter'] = can_enter
            
            # 计算总分
            try:
                questions = db.table("questions").select("score").eq("exam_id", exam_id).execute()
                total_score = sum(q.get('score', 0) for q in (questions.data or []))
                ex['total_score'] = total_score if total_score else 100
            except Exception as e:
                logger.warning(f"计算总分失败: {e}")
                ex['total_score'] = 100
            
            exams.append(ex)
            
        # ============================================================
        # 🔥 获取最近10条成绩记录（增强版：支持重考状态和备注）
        # ============================================================
        results_res = db.table("exam_results").select("*").eq("user_id", user_id).order("created_at", desc=True).limit(10).execute()
        results = []
        if results_res.data:
            # 获取考试信息
            valid_exams = db.table("exams").select("id, title, pass_score, max_retake, status").in_("id", [r['exam_id'] for r in results_res.data]).is_("deleted_at", "null").execute()
            valid_map = {e['id']: e for e in valid_exams.data or []}
            
            for r in results_res.data:
                exam = valid_map.get(r['exam_id'])
                if exam:
                    # ✅ 使用字符串标识符，前端翻译
                    r['exam_title'] = exam.get('title', '')
                    # 添加一个标识符，让前端知道使用哪个翻译键
                    r['exam_title_key'] = 'exam_name_unknown' if not exam.get('title') else None
                    
                    pass_score = exam.get('pass_score', 85)
                    max_retake = exam.get('max_retake', 3)
                    total_score = r.get('total_score', 0)
                    exam_status = exam.get('status', '')
                    
                    # ============================================================
                    # 🔥 计算重考状态（返回状态码 + 参数，前端翻译）
                    # ============================================================
                    
                    # 1. 获取该考试的所有成绩记录（按时间升序）
                    all_results_res = db.table("exam_results")\
                        .select("id, total_score, retake_number, remark")\
                        .eq("user_id", user_id)\
                        .eq("exam_id", r['exam_id'])\
                        .is_("deleted_at", "null")\
                        .order("created_at", desc=False)\
                        .execute()
                    all_results = all_results_res.data or []
                    
                    # 2. 计算已提交次数
                    submit_count = len(all_results)
                    retake_count = max(0, submit_count - 1)
                    
                    # 3. 判断是否及格（用最新一条记录判断）
                    latest_result = all_results[-1] if all_results else None
                    is_passed = latest_result and latest_result.get('total_score', 0) >= pass_score
                    
                    # 4. 判断是否可以重考
                    is_exam_available = exam_status in ['active', 'created']
                    can_retake = not is_passed and retake_count < max_retake and is_exam_available
                    
                    # 5. ✅ 重考状态：使用状态码 + 参数（前端翻译）
                    if is_passed and retake_count > 0:
                        retake_status_code = 'passed_after_retake'
                        retake_status_class = 'success'
                        retake_params = {'count': retake_count}  # 用于前端显示 "重考{N}次"
                    elif retake_count >= max_retake and not is_passed:
                        retake_status_code = 'exhausted_failed'
                        retake_status_class = 'danger'
                        retake_params = {'count': retake_count, 'max': max_retake}
                    elif not is_passed and retake_count > 0:
                        retake_status_code = 'retake_failed'
                        retake_status_class = 'warning'
                        retake_params = {'count': retake_count}
                    elif not is_passed and retake_count == 0:
                        retake_status_code = 'failed'
                        retake_status_class = 'danger'
                        retake_params = {}
                    else:
                        retake_status_code = 'passed'
                        retake_status_class = 'success'
                        retake_params = {}

                    # 6. 获取备注信息
                    remark = r.get('remark', '')
                    retake_number = r.get('retake_number', 0)

                    # 7. ✅ 备注类型：使用标识符
                    if retake_number == 1:
                        remark_type = 'first'
                    elif retake_number > 1:
                        remark_type = 'retake'
                    else:
                        remark_type = 'unknown'

                    # 检查是否为强制推送（从 remark 字段判断）
                    is_force = r.get('remark', '').startswith('🔥') if r.get('remark') else False

                    # 8. ✅ 构造重考序号显示（使用标识符 + 参数）
                    # ✅ 初始化默认值，避免 UnboundLocalError
                    retake_display = ''
                    retake_display_key = None
                    retake_display_params = None
                    
                    if retake_number == 1:
                        # 首次考试，不需要显示
                        retake_display = ''
                        retake_display_key = None
                        retake_display_params = None
                    elif retake_number > 1:
                        retake_display_key = 'retake_label'
                        retake_display_params = {'count': retake_number - 1}
                        retake_display = f'重考{retake_number - 1}'  # 保留向后兼容
                    
                    # 强制推送特殊处理
                    if is_force and retake_number > 1:
                        retake_display_key = 'force_retake_label'
                        retake_display_params = {'count': retake_number - 1}
                        retake_display = f'🔥 强制-重考{retake_number - 1}'  # 保留向后兼容
                    
                    r['is_passed'] = is_passed
                    r['pass_score'] = pass_score
                    r['max_retake'] = max_retake
                    r['retake_count'] = retake_count
                    r['can_retake'] = can_retake
                    r['exam_status'] = exam_status
                    r['exam_available'] = is_exam_available
                    r['retake_status_code'] = retake_status_code
                    r['retake_status_class'] = retake_status_class
                    r['retake_params'] = retake_params if retake_params else None
                    r['remark_type'] = remark_type
                    r['retake_number'] = retake_number
                    r['is_force'] = is_force
                    r['remark'] = remark
                    r['retake_display'] = retake_display
                    r['retake_display_key'] = retake_display_key
                    r['retake_display_params'] = retake_display_params
                    r['score_title_params'] = {'score': total_score, 'pass': pass_score}
                    
                    results.append(r)
        
        # 获取用户名称
        user_info_res = db.table("users").select("name_cn, name_en, email").eq("id", user_id).single().execute()
        user_info_data = user_info_res.data if user_info_res.data else {}
        user_name = user_info_data.get('name_cn') or user_info_data.get('name_en')
        
        return render_template(
            'exam/dashboard.html',
            exams=exams,
            results=results,
            training_open=True,
            user_signed_in=False,
            user_name=user_name
        )
        
    except Exception as e:
        logger.error(f"dashboard 异常: {e}", exc_info=True)
        return render_template('exam/dashboard.html', exams=[], results=[], training_open=True, user_signed_in=False)

@exam_bp.route('/exam/take/<int:exam_id>')
@login_required
def take_exam(exam_id):
    try:
        from services.db import get_supabase, get_supabase_admin
        
        db = get_supabase()
        admin_db = get_supabase_admin()
        user_id = session['user_id']
        now = datetime.now(timezone.utc)
        
        # ========== 1. 获取考试基本信息 ==========
        exam_info = db.table("exams").select("start_time, end_time, duration, title, max_retake").eq("id", exam_id).maybe_single().execute()
        if not exam_info.data:
            flash("考试不存在", "danger")
            return redirect(url_for('exam.dashboard'))
        
        exam = exam_info.data
        
        # ========== 2. 检查强制重推记录 ==========
        force_record = admin_db.table("user_exam_force_records").select("*")\
            .eq("user_id", user_id)\
            .eq("original_exam_id", exam_id)\
            .is_("deleted_at", "null")\
            .execute()
        
        is_force = force_record and force_record.data
        
        # ========== 3. 设置有效期和时长（强制重推优先）==========
        if is_force:
            force_data = force_record.data[0]
            effective_start_time = force_data.get('start_time')
            effective_end_time = force_data.get('end_time')
            # 优先使用原考试时长，如果没有则使用记录中的时长
            duration_minutes = exam.get('duration', force_data.get('duration', 60))
        else:
            effective_start_time = exam.get('start_time')
            effective_end_time = exam.get('end_time')
            duration_minutes = exam.get('duration', 60)
        
        exam_title = exam.get('title', '考试')

        # 4. 获取用户考试状态
        status = db.table("user_exam_status").select("*").eq("user_id", user_id).eq("exam_id", exam_id).maybe_single().execute()
        
        # 检查是否已交卷
        if status and status.data and status.data.get("is_submitted"):
            flash("您已完成本场考试，无法再次进入。", "warning")
            return redirect(url_for('exam.dashboard'))
        
        started_at = None
        if status and status.data:
            started_at = status.data.get("started_at")
        
        # ========== 5. 首次进入时检查有效期 ==========
        is_first_entry = (started_at is None)
        
        if is_first_entry:
            # 检查强制重推或普通考试的有效期
            if effective_end_time:
                try:
                    end_dt = safe_parse_datetime(effective_end_time)
                    if end_dt and now > end_dt:
                        flash({'msg': 'exam_ended', 'params': []}, 'warning')
                        return redirect(url_for('exam.dashboard'))
                except ValueError as e:
                    logger.warning(f"解析 end_time 失败: {e}")
            
            if effective_start_time:
                try:
                    start_dt = safe_parse_datetime(effective_start_time)
                    if start_dt and now < start_dt:
                        flash({'msg': 'exam_not_started', 'params': []}, 'warning')
                        return redirect(url_for('exam.dashboard'))
                except ValueError as e:
                    logger.warning(f"解析 start_time 失败: {e}")
        else:
            logger.info(f"用户 {user_id} 再次进入考试 {exam_id}，跳过有效期检查")
        
        total_seconds = duration_minutes * 60
        
        # 6. 处理开始时间和重置标志
        reset_timer = False
        reset_token = ''
        
        if status and status.data:
            submitted_at = status.data.get("submitted_at")
            reset_at = status.data.get("reset_at")
            
            if reset_at and (not submitted_at or reset_at > submitted_at):
                reset_timer = True
                reset_token = reset_at
            
            if not started_at or reset_timer:
                started_at = now.isoformat()
                db.table("user_exam_status").update({
                    "started_at": started_at,
                    "reset_at": None
                }).eq("user_id", user_id).eq("exam_id", exam_id).execute()
        else:
            started_at = now.isoformat()
            db.table("user_exam_status").insert({
                "user_id": user_id,
                "exam_id": exam_id,
                "started_at": started_at,
                "is_submitted": False
            }).execute()
        
        # 7. 计算剩余时间
        remaining = total_seconds
        if started_at:
            try:
                start_dt = safe_parse_datetime(started_at)
                if start_dt:
                    elapsed = (now - start_dt).total_seconds()
                    remaining = max(0, total_seconds - int(elapsed))
                else:
                    remaining = total_seconds
            except Exception as e:
                logger.warning(f"计算剩余时间失败: {e}")
                remaining = total_seconds
        
        # 8. 超时处理
        if remaining <= 0:
            if not (status and status.data and status.data.get("is_submitted")):
                try:
                    answers = {}
                    draft = db.table("user_exam_drafts").select("answers").eq("user_id", user_id).eq("exam_id", exam_id).maybe_single().execute()
                    
                    if draft and draft.data:
                        answers_data = draft.data.get('answers')
                        if answers_data:
                            if isinstance(answers_data, str):
                                answers = json.loads(answers_data)
                            else:
                                answers = answers_data
                    
                    grade = exam.auto_grade(answers, exam_id)
                    
                    time_used = None
                    if started_at:
                        try:
                            start_dt = safe_parse_datetime(started_at)
                            if start_dt:
                                time_used = int((datetime.now(timezone.utc) - start_dt).total_seconds())
                        except:
                            pass
                    
                    exam.save_result(
                        user_id, exam_id, answers, grade['total'], grade['details'], 
                        customs={}, 
                        submit_method='auto', 
                        time_used=time_used
                    )
                    
                    existing = db.table("user_exam_status").select("id").eq("user_id", user_id).eq("exam_id", exam_id).maybe_single().execute()
                    update_data = {
                        "is_submitted": True,
                        "submitted_at": datetime.now(timezone.utc).isoformat(),
                        "reset_at": None
                    }
                    if existing and existing.data:
                        db.table("user_exam_status").update(update_data).eq("id", existing.data['id']).execute()
                    else:
                        update_data.update({"user_id": user_id, "exam_id": exam_id, "started_at": started_at})
                        db.table("user_exam_status").insert(update_data).execute()
                    
                    db.table("user_exam_drafts").delete().eq("user_id", user_id).eq("exam_id", exam_id).execute()
                    
                    logger.info(f"超时自动提交完成，得分 {grade['total']}")
                    flash({'msg': 'flash_time_expired_exam_automatically', 'params': []}, "info")
                    
                except Exception as e:
                    logger.error(f"超时自动提交失败: {e}", exc_info=True)
                    flash({'msg': 'flash_time_expired_exam_automatic_failed', 'params': []}, "danger")
            
            return redirect(url_for('exam.dashboard'))
        
        # 9. 查询题目
        qs = db.table("questions").select("*").eq("exam_id", exam_id).order("num").execute()
        questions = qs.data or []
        for q in questions:
            if isinstance(q.get('options'), str):
                try:
                    q['options'] = json.loads(q['options'])
                except:
                    q['options'] = {}
        
        # 10. 加载草稿答案
        saved_answers = {}
        try:
            draft = db.table("user_exam_drafts").select("answers").eq("user_id", user_id).eq("exam_id", exam_id).maybe_single().execute()
            
            if draft and draft.data:
                answers_data = draft.data.get('answers')
                if answers_data:
                    if isinstance(answers_data, str):
                        saved_answers = json.loads(answers_data)
                    elif isinstance(answers_data, dict):
                        saved_answers = answers_data
                    logger.info(f"从草稿表加载成功，共 {len(saved_answers)} 条")
        except Exception as e:
            logger.error(f"加载草稿失败: {e}", exc_info=True)
            saved_answers = {}

        # 11. 获取用户信息
        user_info_res = db.table("users").select("name_en, email").eq("id", user_id).single().execute()
        user_info = user_info_res.data if user_info_res.data else {}
        user_display_name = f"{user_info.get('name_en', '')} ({session.get('user_email', '')})" if user_info.get('name_en') else session.get('user_email', 'User')
        
        logger.info(f"考试 {exam_id} 用户 {user_id} 进入，剩余 {remaining} 秒，强制重推={is_force}")

        # 12. 渲染模板
        return render_template(
            'exam/take.html',
            exam_id=exam_id,
            exam_title=exam_title,
            questions=questions,
            duration_minutes=duration_minutes,
            server_remaining_seconds=remaining,
            reset_timer=reset_timer,
            reset_token=reset_token,
            user_id=user_id,
            user_display_name=user_display_name,
            saved_answers=json.dumps(saved_answers),
            max_retake=exam.get('max_retake', 3)
        )
        
    except Exception as e:
        logger.error(f"take_exam 发生异常: {e}", exc_info=True)
        flash({'msg': 'flash_loading_failed_try_later', 'params': []}, "danger")
        return redirect(url_for('exam.dashboard'))

@exam_bp.route('/exam/retake/<int:exam_id>', methods=['POST'])
@login_required
def retake_exam(exam_id):
    """
    学员重考接口（仅限未通过的考试）
    重置考试状态 + 自动延长有效期
    """
    db = get_supabase()
    user_id = session['user_id']
    now = datetime.now(timezone.utc)
    
    # 1. 检查用户是否有该考试的提交记录
    result_res = db.table("exam_results")\
        .select("*")\
        .eq("user_id", user_id)\
        .eq("exam_id", exam_id)\
        .is_("deleted_at", "null")\
        .order("created_at", desc=True)\
        .limit(1)\
        .execute()
    
    if not result_res.data:
        return jsonify({
            "success": False, 
            "message": "没有找到考试成绩记录，无法重考"
        }), 400
    
    last_result = result_res.data[0]
    
    # 2. 检查上次考试是否未通过
    exam_info = db.table("exams").select("pass_score, max_retake, title, duration").eq("id", exam_id).maybe_single().execute()
    if not exam_info.data:
        return jsonify({"success": False, "message": "考试不存在"}), 404
    
    pass_score = exam_info.data.get('pass_score', 85)
    max_retake = exam_info.data.get('max_retake', 3)
    exam_title = exam_info.data.get('title', '考试')
    duration = exam_info.data.get('duration', 60)
    
    if last_result.get('total_score', 0) >= pass_score:
        return jsonify({
            "success": False, 
            "message": "该考试已通过，无需重考"
        }), 400
    
    # 3. 检查重考次数是否超限
    history_res = db.table("exam_results")\
        .select("id", count="exact")\
        .eq("user_id", user_id)\
        .eq("exam_id", exam_id)\
        .is_("deleted_at", "null")\
        .execute()
    
    current_submit_count = history_res.count if hasattr(history_res, 'count') else len(history_res.data or [])
    retake_count = current_submit_count - 1
    
    if retake_count >= max_retake:
        return jsonify({
            "success": False, 
            "message": f"已达到最大重考次数（{max_retake}次），请联系管理员",
            "retake_count": retake_count,
            "max_retake": max_retake
        }), 400
    
    # ============================================================
    # 🔥 新增：自动延长考试有效期（当前时间 + 7天）
    # ============================================================
    new_start_time = now.isoformat()
    new_end_time = (now + timedelta(days=7)).isoformat()
    
    # 更新考试有效期
    db.table("exams").update({
        "start_time": new_start_time,
        "end_time": new_end_time,
        "status": "active",      # 设置为进行中
        "is_active": True
    }).eq("id", exam_id).execute()
    
    logger.info(f"📅 重考自动延长有效期: exam={exam_id}, end={new_end_time}")
    
    # 4. 重置考试状态
    status_res = db.table("user_exam_status")\
        .select("id")\
        .eq("user_id", user_id)\
        .eq("exam_id", exam_id)\
        .maybe_single()\
        .execute()
    
    reset_at = now.isoformat()
    
    if status_res and status_res.data:
        db.table("user_exam_status").update({
            "is_submitted": False,
            "started_at": None,
            "submitted_at": None,
            "reset_at": reset_at
        }).eq("id", status_res.data['id']).execute()
    else:
        db.table("user_exam_status").insert({
            "user_id": user_id,
            "exam_id": exam_id,
            "is_submitted": False,
            "reset_at": reset_at
        }).execute()
    
    # 5. 删除旧的草稿
    db.table("user_exam_drafts")\
        .delete()\
        .eq("user_id", user_id)\
        .eq("exam_id", exam_id)\
        .execute()
    
    logger.info(f"🔄 学员重考: user={user_id}, exam={exam_id}, 第{retake_count + 1}次重考")
    
    return jsonify({
        "success": True,
        "message": f"已重置考试状态并延长有效期至 {new_end_time[:10]}，可以重新参加考试（第{retake_count + 1}次重考）",
        "exam_id": exam_id,
        "exam_title": exam_title,
        "retake_count": retake_count + 1,
        "max_retake": max_retake,
        "reset_at": reset_at,
        "new_end_time": new_end_time
    })

@exam_bp.route('/exam/submit/<int:exam_id>', methods=['POST'])
@login_required
def submit_exam(exam_id):
    db = get_supabase()
    user_id = session['user_id']
    now = datetime.now(timezone.utc)
    
    logger.info(f"📥 收到交卷请求：用户 {user_id}，考试 {exam_id}")

    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
    
    # 获取开始时间和提交时间
    status = db.table("user_exam_status").select("*").eq("user_id", user_id).eq("exam_id", exam_id).maybe_single().execute()
    started_at_str = None
    if status and status.data:
        started_at_str = status.data.get('started_at')
        if status.data.get('is_submitted'):
            flash({'msg': 'already_submitted', 'params': []}, 'warning')
            return redirect(url_for('exam.dashboard'))
    
    # 计算用时
    time_used = None
    if started_at_str:
        try:
            start_str = started_at_str.replace('Z', '+00:00') if started_at_str.endswith('Z') else started_at_str
            start_dt = datetime.fromisoformat(start_str)
            time_used = int((now - start_dt).total_seconds())
            logger.info(f"📊 计算用时: {time_used} 秒")
        except Exception as e:
            logger.warning(f"计算用时失败: {e}")
    
    # 更新考试状态
    update_data = {
        "is_submitted": True,
        "submitted_at": now.isoformat(),
        "reset_at": None
    }
    
    if status and status.data:
        db.table("user_exam_status").update(update_data).eq("id", status.data['id']).execute()
        logger.info(f"✅ 更新考试状态成功")
    else:
        update_data.update({
            "user_id": user_id,
            "exam_id": exam_id,
            "started_at": started_at_str or now.isoformat()
        })
        db.table("user_exam_status").insert(update_data).execute()
        logger.info(f"✅ 插入考试状态成功")
    
    # 获取考试信息
    exam_info = db.table("exams").select("duration, pass_score, title").eq("id", exam_id).maybe_single().execute()
    duration_minutes = exam_info.data.get("duration", 60) if exam_info.data else 60
    pass_score = exam_info.data.get('pass_score', 85) if exam_info.data else 85
    exam_title = exam_info.data.get('title', '考试') if exam_info.data else '考试'
    total_seconds = duration_minutes * 60
    
    # 超时检查
    if started_at_str and time_used and time_used > total_seconds:
        if is_ajax:
            return jsonify({"success": False, "message": "exam_timeout", "params": []}), 400
        flash({'msg': 'exam_timeout', 'params': []}, 'danger')
        return redirect(url_for('exam.dashboard'))
    
    # 解析答案
    answers = {}
    for key, values in request.form.to_dict(flat=False).items():
        if key.startswith('q_'):
            if len(values) == 1:
                answers[key] = values[0]
            else:
                answers[key] = ''.join(sorted(values))
    
    logger.info(f"📥 考生提交答案：{answers}")
    
    # 评分
    try:
        grade = exam.auto_grade(answers, exam_id)
        logger.info(f"📊 评分结果：总分={grade['total']}")
    except Exception as e:
        logger.error(f"❌ 评分失败: {e}")
        if is_ajax:
            return jsonify({"success": False, "message": "grading_error", "params": []}), 500
        flash({'msg': 'grading_error', 'params': []}, 'danger')
        return redirect(url_for('exam.dashboard'))

    total_score = grade['total']
    is_passed = total_score >= pass_score

    # ========== 新增：统计重考次数 ==========
    # 查询该用户对该考试的历史成绩记录（排除刚提交的这条）
    history_res = db.table("exam_results")\
        .select("id", count="exact")\
        .eq("user_id", user_id)\
        .eq("exam_id", exam_id)\
        .is_("deleted_at", "null")\
        .execute()
    
    # 当前已提交的次数（包括这次）
    current_submit_count = history_res.count if hasattr(history_res, 'count') else len(history_res.data or [])
    # 重考次数 = 已提交次数 - 1（第一次不算重考）
    retake_count = max(0, current_submit_count - 1)
    
    # 最大重考次数（可配置，默认3次）
    max_retake = exam_info.data.get('max_retake', 3) if exam_info.data else 3
    can_retake = not is_passed and retake_count < max_retake

    # ========== 计算重考序号和备注 ==========
    # 重考序号 = 已有成绩数量 + 1（新记录）
    retake_number = current_submit_count + 1
    
    # 生成备注
    if retake_number == 1:
        remark = '首次考试'
    else:
        remark = f'第{retake_number - 1}次重考'
    
    # 检查是否为强制重推
    try:
        force_res = db.table("user_exam_force_records")\
            .select("id")\
            .eq("user_id", user_id)\
            .eq("original_exam_id", exam_id)\
            .is_("deleted_at", "null")\
            .execute()
        if force_res.data:
            remark = f'🔥 强制推送 - {remark}'
    except:
        pass
        
    logger.info(f"📊 重考统计: 已提交={current_submit_count}, 重考次数={retake_count}, "
                f"最大={max_retake}, 可重考={can_retake}, retake_number={retake_number}, remark={remark}")

    # 保存成绩
    customs = {f"c{i}": request.form.get(f"custom{i}", "") for i in range(1, 6)}
    try:
        exam.save_result(
            user_id, exam_id, answers, grade['total'], grade['details'], 
            customs, 
            submit_method='manual', 
            time_used=time_used,
            retake_number=retake_number,
            remark=remark
        )
        logger.info(f"💾 成绩保存成功，用时: {time_used}秒")
    except Exception as e:
        logger.error(f"❌ 成绩保存失败: {e}")
        if is_ajax:
            return jsonify({"success": False, "message": "save_score_failed", "params": []}), 500
        flash({'msg': 'save_score_failed', 'params': []}, 'danger')
        return redirect(url_for('exam.dashboard'))
    
    # ========== 关键修复：删除草稿 ==========
    try:
        delete_result = db.table("user_exam_drafts")\
            .delete()\
            .eq("user_id", user_id)\
            .eq("exam_id", int(exam_id))\
            .execute()
        if delete_result.data:
            logger.info(f"🗑️ 草稿已删除: user={user_id}, exam={exam_id}, 删除了 {len(delete_result.data)} 条记录")
        else:
            logger.info(f"🗑️ 无需删除草稿: user={user_id}, exam={exam_id} (不存在)")
    except Exception as e:
        # 删除草稿失败不影响主流程，只记录日志
        logger.warning(f"删除草稿失败（不影响主流程）: {e}")
    
    # 根据请求类型返回
    if is_ajax:
        return jsonify({
            "success": True,
            "score": total_score,
            "is_passed": is_passed,
            "pass_score": pass_score,
            "exam_title": exam_title,
            "retake_count": retake_count,
            "max_retake": max_retake,
            "can_retake": can_retake,
            "message": f'交卷成功！得分：{total_score}（{"及格" if is_passed else "不及格"}，及格线：{pass_score}分）'
        })
    else:
        if is_passed:
            flash(f'✅ 交卷成功！得分：{total_score}（及格，及格线：{pass_score}分）', 'success')
        else:
            flash(f'📝 交卷成功！得分：{total_score}（不及格，及格线：{pass_score}分）', 'warning')
    return redirect(url_for('exam.dashboard'))

@exam_bp.route('/exam/result/<int:result_id>')
@login_required
def exam_result_detail(result_id):
    """学员查看自己的考试详情"""
    db = get_supabase()
    # 获取成绩记录
    result_res = db.table("exam_results").select("*").eq("id", result_id).maybe_single().execute()
    if not result_res.data:
        #flash("成绩记录不存在", "danger")
        flash({'msg': 'result_not_found', 'params': []}, 'danger')
        return redirect(url_for('dashboard'))
    result = result_res.data
    if result['user_id'] != session['user_id']:
        #flash("无权访问", "danger")
        flash({'msg': 'access_denied', 'params': []}, 'danger')
        return redirect(url_for('dashboard'))
    
    exam_id = result['exam_id']
    user_id = result['user_id']
    
    # 获取用户信息
    user_res = db.table("users").select("email, name_en").eq("id", user_id).maybe_single().execute()
    user_info = user_res.data if user_res.data else {"email": "未知", "name_en": "未知"}
    
    # 获取考试信息
    exam_res = db.table("exams").select("title").eq("id", exam_id).maybe_single().execute()
    exam_title = exam_res.data.get("title", "未知考试") if exam_res.data else "未知考试"
    
    # 附加信息
    result['users'] = user_info
    result['exams'] = {"title": exam_title}
    
    # 获取题目列表
    questions = db.table("questions").select("*").eq("exam_id", exam_id).order("num").execute()
    
    # 解析 answers 和 details
    def deep_parse(val):
        if not val:
            return {}
        if isinstance(val, dict):
            return val
        if isinstance(val, str):
            try:
                parsed = json.loads(val)
                if isinstance(parsed, str):
                    return deep_parse(parsed)
                return parsed
            except:
                return {}
        return {}
    
    answers = deep_parse(result.get('answers'))
    details = deep_parse(result.get('details'))
    
    return render_template(
        'exam/result_detail.html',  # 可以复用 admin/result_detail.html，但需调整布局
        result=result,
        questions=questions.data or [],
        answers=answers,
        details=details
    )

@exam_bp.route('/exam/export_pdf/<int:result_id>')
@login_required
def exam_export_pdf(result_id):
    """学员导出自己的考试PDF"""
    db = get_supabase()
    # 获取成绩记录
    result_res = db.table("exam_results").select("*").eq("id", result_id).execute()
    if not result_res.data:
        #flash("成绩记录不存在", "danger")
        flash({'msg': 'result_not_found', 'params': []}, 'danger')
        return redirect(url_for('exam.dashboard'))
    result = result_res.data[0]
    if result['user_id'] != session['user_id']:
        #flash("无权访问", "danger")
        flash({'msg': 'access_denied', 'params': []}, 'danger')
        return redirect(url_for('exam.dashboard'))
    
    exam_id = result['exam_id']
    user_id = result['user_id']
    
    # 获取考试信息
    exam_res = db.table("exams").select("*").eq("id", exam_id).execute()
    exam_data = exam_res.data[0] if exam_res.data else {}
    
    # 获取考生信息
    user_res = db.table("users").select("*").eq("id", user_id).execute()
    user_data = user_res.data[0] if user_res.data else {}
    user_name=user_data.get('name_cn') or user_data.get('name_en', '未知考生')

    # 解析 answers 和 details
    def deep_parse(val):
        if not val:
            return {}
        if isinstance(val, dict):
            return val
        if isinstance(val, str):
            try:
                parsed = json.loads(val)
                if isinstance(parsed, str):
                    return deep_parse(parsed)
                return parsed
            except:
                return {}
        return {}
    
    answers = deep_parse(result.get('answers'))
    details = deep_parse(result.get('details'))
    
    # 获取题目列表
    questions_res = db.table("questions").select("*").eq("exam_id", exam_id).order("num").execute()
    questions = questions_res.data or []
    
    # ---------- 获取阅卷人（多级优先级）----------
    reviewer = get_reviewer_by_country(
        user_country=user_data.get('country'),
        exam_reviewer=exam_data.get('reviewer'),
        url_reviewer=request.args.get('reviewer')
    )
    
    try:
        pdf_buffer = export.generate_user_pdf(
            user_name=user_data.get('name_cn') or user_data.get('name_en', '未知考生'),
            user_email=user_data.get('email', ''),
            exam_title=exam_data.get('title', '未命名考试'),
            score=result.get('total_score', 0),
            questions=questions,
            answers=answers,
            details=details,
            submitted_at=result.get('created_at', ''),
            reviewer=reviewer
        )
    except Exception as e:
        logger.error(f"PDF生成失败: {e}")
        #flash("PDF生成失败", "danger")
        flash({'msg': 'pdf_generation_error', 'params': []}, 'danger')
        return redirect(url_for('exam.dashboard'))
    
    filename = f"Transcript_{user_name}_{exam_data.get('title', 'exam')}.pdf"
    return send_file(
        pdf_buffer,
        mimetype="application/pdf",
        as_attachment=True,
        download_name=filename
    )

@exam_bp.route('/api/my/interviews')
@login_required
def my_interviews():
    """获取学员的访谈列表（合并普通访谈和强制访谈）"""
    user_id = session['user_id']
    db = get_supabase()
    admin_db = get_supabase_admin()
    now = datetime.now(timezone.utc).isoformat()

    # 使用字典按 interview_id 去重
    result_map = {}
    
    # ========== 1. 查询普通访谈 ==========
    res = db.table("interview_results").select("interview_id").eq("user_id", user_id).is_("deleted_at", "null").execute()
    interview_ids = list(set(r['interview_id'] for r in (res.data or [])))
    
    if interview_ids:
        inv_res = db.table("interviews").select("*").in_("id", interview_ids).is_("deleted_at", "null").execute()
        
        # 获取关联的考试信息
        exam_ids = list(set([i.get('exam_id') for i in (inv_res.data or []) if i.get('exam_id')]))
        exam_country_map = {}
        if exam_ids:
            exams_res = db.table("exams").select("id, countries, country").in_("id", exam_ids).execute()
            for exam in (exams_res.data or []):
                countries_data = exam.get('countries')
                if countries_data:
                    if isinstance(countries_data, str):
                        try:
                            countries_data = json.loads(countries_data)
                        except:
                            countries_data = [exam.get('country', '')] if exam.get('country') else []
                    elif isinstance(countries_data, list):
                        pass
                    else:
                        countries_data = [exam.get('country', '')] if exam.get('country') else []
                else:
                    countries_data = [exam.get('country', '')] if exam.get('country') else []
                exam_country_map[exam['id']] = ', '.join(countries_data) if countries_data else ''
        
        for inv in (inv_res.data or []):
            interview_id = inv['id']
            start_time = inv.get('start_time')
            end_time = inv.get('end_time')
            exam_id = inv.get('exam_id')
            
            if not start_time or not end_time:
                continue
            
            # 只显示有效期内或已开始的访谈
            if now > end_time:
                continue
            
            is_future = now < start_time
            is_active = start_time <= now <= end_time
            
            # 检查普通访谈是否已完成（所有问题都有答案）
            answers = db.table("interview_results").select("submitted_at").eq("interview_id", interview_id).eq("user_id", user_id).is_("deleted_at", "null").execute()
            is_completed = any(row.get('submitted_at') for row in (answers.data or []))
            
            # 检查考试是否已完成
            exam_completed = False
            exam_total_score = 0
            if exam_id:
                exam_result = db.table("exam_results").select("total_score").eq("exam_id", exam_id).eq("user_id", user_id).is_("deleted_at", "null").execute()
                if exam_result.data:
                    exam_completed = True
                    exam_total_score = exam_result.data[0].get('total_score', 0)
            
            result_map[interview_id] = {
                "id": interview_id,
                "title": inv.get('title', ''),
                "start_time": start_time,
                "end_time": end_time,
                "question_count": inv.get('question_count', 0),
                "is_future": is_future,
                "is_active": is_active,
                "is_completed": is_completed,
                "exam_completed": exam_completed,
                "exam_total_score": exam_total_score,
                "country": exam_country_map.get(exam_id, ''),
                "is_force": False,
                "source": "normal"
            }
    
    # ========== 2. 查询强制访谈 ==========
    force_res = admin_db.table("user_interview_force_records").select("*").eq("user_id", user_id).is_("deleted_at", "null").execute()
    
    for force in (force_res.data or []):
        original_id = force.get('original_interview_id')
        force_id = force.get('id')
        start_time = force.get('start_time')
        end_time = force.get('end_time')
        exam_id = force.get('exam_id')
        
        if not start_time or not end_time:
            continue
        
        # 过期则删除
        if now > end_time:
            admin_db.table("user_interview_force_records").update({
                "deleted_at": now,
                "deleted_by": user_id
            }).eq("id", force_id).execute()
            continue
        
        is_future = now < start_time
        is_active = start_time <= now <= end_time
        
        # 检查强制访谈是否已完成（所有问题都有答案）
        answers = db.table("interview_results").select("submitted_at").eq("interview_id", original_id).eq("user_id", user_id).is_("deleted_at", "null").execute()
        is_completed = any(row.get('submitted_at') for row in (answers.data or []))
        
        # 检查考试是否已完成
        exam_completed = False
        exam_total_score = 0
        if exam_id:
            exam_result = db.table("exam_results").select("total_score").eq("exam_id", exam_id).eq("user_id", user_id).is_("deleted_at", "null").execute()
            if exam_result.data:
                exam_completed = True
                exam_total_score = exam_result.data[0].get('total_score', 0)
        
        # ✅ 关键逻辑：判断是否用强制访谈覆盖普通访谈
        should_use_force = True
        
        if original_id in result_map:
            normal = result_map[original_id]
            
            # 如果普通访谈已完成，用强制访谈覆盖（补考/重新作答）
            if normal.get('is_completed', False):
                logger.info(f"✅ 访谈 {original_id} 已完成，使用强制访谈记录覆盖（补考模式）")
                should_use_force = True
            else:
                # 普通访谈未完成，保留普通访谈，跳过强制访谈
                logger.info(f"⏭️ 访谈 {original_id} 未完成，保留普通访谈，跳过强制访谈")
                should_use_force = False
                # 删除这个强制访谈记录（已失效）
                admin_db.table("user_interview_force_records").update({
                    "deleted_at": now,
                    "deleted_by": user_id
                }).eq("id", force_id).execute()
                continue
        
        # 获取关联考试的国家信息
        country_display = ''
        if exam_id:
            exam_res = db.table("exams").select("countries, country").eq("id", exam_id).maybe_single().execute()
            if exam_res.data:
                countries_data = exam_res.data.get('countries')
                if countries_data:
                    if isinstance(countries_data, str):
                        try:
                            countries_data = json.loads(countries_data)
                        except:
                            countries_data = [exam_res.data.get('country', '')] if exam_res.data.get('country') else []
                    elif isinstance(countries_data, list):
                        pass
                    else:
                        countries_data = [exam_res.data.get('country', '')] if exam_res.data.get('country') else []
                else:
                    countries_data = [exam_res.data.get('country', '')] if exam_res.data.get('country') else []
                country_display = ', '.join(countries_data) if countries_data else ''
        
        # ✅ 构建强制访谈数据（覆盖普通访谈）
        result_map[original_id] = {
            "id": original_id,
            "title": force.get('title', '强制访谈'),
            "start_time": start_time,
            "end_time": end_time,
            "question_count": force.get('question_count', 0),
            "is_future": is_future,
            "is_active": is_active,
            "is_completed": is_completed,
            "exam_completed": exam_completed,
            "exam_total_score": exam_total_score,
            "country": country_display,
            "is_force": True,
            "force_record_id": force_id,
            "source": "force"
        }
    
    return jsonify(list(result_map.values()))

@exam_bp.route('/api/exam/draft', methods=['POST'])
@login_required
def save_exam_draft():
    """保存考试草稿 - 修复版"""
    try:
        db = get_supabase()
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "message": "无效请求"}), 400
        
        exam_id = data.get('exam_id')
        answers = data.get('answers', {})
        
        if not exam_id:
            return jsonify({"success": False, "message": "缺少考试ID"}), 400
        
        user_id = session.get('user_id')
        if not user_id:
            return jsonify({"success": False, "message": "未登录"}), 401
        
        # 确保 answers 是字典，不要手动 json.dumps
        if isinstance(answers, str):
            try:
                answers = json.loads(answers)
            except:
                answers = {}
        
        # 直接存储字典对象（让 Supabase 自动处理 JSON）
        now_utc = datetime.now(timezone.utc).isoformat()

        # 使用 upsert - 先查询是否存在，决定是否设置 created_at
        # 方式1：直接 upsert，让数据库自动处理（如果 created_at 有默认值）
        # 方式2：先检查是否存在
        existing = db.table("user_exam_drafts")\
            .select("created_at")\
            .eq("user_id", user_id)\
            .eq("exam_id", int(exam_id))\
            .maybe_single()\
            .execute()

        # 准备数据
        data_to_upsert = {
            "user_id": user_id,
            "exam_id": int(exam_id),
            "answers": answers,
            "updated_at": now_utc
        }
        
        # 如果不存在，设置 created_at；否则不设置（让数据库保留原值）
        if not existing or not existing.data:
            data_to_upsert["created_at"] = now_utc
        
        result = db.table("user_exam_drafts").upsert(
            data_to_upsert,
            on_conflict="user_id, exam_id"
        ).execute()
        
        action = "创建" if not existing or not existing.data else "更新"
        logger.info(f"草稿已{action}(upsert): user={user_id}, exam={exam_id}, 答案数={len(answers)}")
        return jsonify({"success": True, "saved_count": len(answers)})
        
    except Exception as e:
        logger.error(f"保存草稿失败: {e}", exc_info=True)
        return jsonify({"success": False, "message": str(e)}), 500

# routes/api_exam.py - 添加调试端点（仅开发环境）

@exam_bp.route('/debug/draft/<int:exam_id>')
@login_required
def debug_draft(exam_id):
    """调试：查看草稿内容"""
    user_id = session['user_id']
    db = get_supabase()
    
    draft = db.table("user_exam_drafts").select("answers").eq("user_id", user_id).eq("exam_id", exam_id).maybe_single().execute()
    
    if draft and draft.data:
        answers_data = draft.data.get('answers')
        return jsonify({
            "exists": True,
            "type": str(type(answers_data)),
            "keys": list(answers_data.keys())[:10] if isinstance(answers_data, dict) else None,
            "sample": answers_data
        })
    else:
        return jsonify({"exists": False})

# ==================== 我的签到记录 API ====================
@exam_bp.route('/api/my/attendances')
@login_required
def my_attendances():
    """获取当前用户的签到记录"""
    user_id = session['user_id']
    db = get_supabase()
    
    try:
        # 查询签到记录，关联培训信息
        res = db.table("training_attendances")\
            .select("training_id, sign_time, signed_name, signature_url")\
            .eq("user_id", user_id)\
            .is_("deleted_at", "null")\
            .order("sign_time", desc=True)\
            .execute()
        
        attendances = res.data or []
        
        # 获取培训信息
        if attendances:
            training_ids = list(set([a['training_id'] for a in attendances]))
            training_res = db.table("trainings").select("id, name, country, countries, start_time, end_time").in_("id", training_ids).execute()
            training_map = {t['id']: t for t in (training_res.data or [])}
            now = datetime.now(timezone.utc).isoformat()

            # ========== 获取当前用户的权限信息 ==========
            current_user_id = session.get('user_id')
            current_role = session.get('role')
            is_dev = is_developer()  # 从 utils.permissions 导入
            
            # 获取管理员的权限范围
            allowed_countries = None
            if not is_dev and current_role in ['super_admin', 'admin']:
                # 从数据库获取用户的 admin_countries
                user_res = db.table("users").select("admin_countries, country").eq("id", current_user_id).maybe_single().execute()
                if user_res and user_res.data:
                    admin_countries = user_res.data.get('admin_countries')
                    if admin_countries:
                        try:
                            allowed_countries = json.loads(admin_countries) if isinstance(admin_countries, str) else admin_countries
                        except:
                            allowed_countries = []
                    else:
                        # 如果没有设置权限范围，使用用户注册国家
                        user_country = user_res.data.get('country')
                        if user_country:
                            allowed_countries = [user_country]
                        else:
                            allowed_countries = []
            
            for att in attendances:
                training = training_map.get(att['training_id'], {})
                att['training_name'] = training.get('name', '未知培训')

                # 解析多国家
                training_countries = parse_training_countries(training)
                
                # ========== 根据角色过滤国家显示 ==========
                filtered_countries = []
                
                if is_dev:
                    # 开发者：显示所有国家
                    filtered_countries = training_countries
                elif allowed_countries is not None and allowed_countries:
                    # 超管/管理员：只显示与权限范围有交集的国家
                    if training_countries:
                        filtered_countries = [c for c in training_countries if c in allowed_countries]
                    else:
                        # 兼容单国家
                        single_country = training.get('country', '')
                        if single_country and single_country in allowed_countries:
                            filtered_countries = [single_country]
                else:
                    # 没有权限范围的情况（如超管无权限限制）
                    filtered_countries = training_countries
                
                # 设置国家显示
                if filtered_countries:
                    att['country'] = ', '.join(filtered_countries)
                else:
                    # 如果没有匹配的国家，显示 '-' 或者保留原值
                    att['country'] = '-'
                    # 可选：记录原始国家用于调试
                    att['_original_countries'] = training_countries
                    att['_allowed_countries'] = allowed_countries
                
                # 判断是否需要重新签名
                # 条件1：有签到记录但无签名（signature_url 为空）
                # 条件2：培训仍在有效期内
                signature_url = att.get('signature_url', '')
                is_resign_needed = not signature_url or signature_url == '' or signature_url == 'null'
                
                # 检查培训是否在有效期内
                is_training_active = False
                start_time = training.get('start_time')
                end_time = training.get('end_time')
                if start_time and end_time:
                    try:
                        is_training_active = start_time <= now <= end_time
                    except:
                        is_training_active = False
                
                att['needs_resign'] = is_resign_needed and is_training_active
                att['is_training_active'] = is_training_active
                
                # 如果是待重新签名但培训已过期，标记为需要联系管理员
                if is_resign_needed and not is_training_active:
                    att['needs_admin_contact'] = True
                else:
                    att['needs_admin_contact'] = False
        
        return jsonify(attendances)
    except Exception as e:
        logger.error(f"获取签到记录失败: {e}")
        return jsonify([])

# ==================== 我的访谈记录 API ====================
@exam_bp.route('/api/my/interview_results')
@login_required
def my_interview_results():
    """获取当前用户的访谈答题记录"""
    user_id = session['user_id']
    db = get_supabase()
    
    try:
        logger.info(f"=== 用户 {user_id} 请求访谈记录 ===")
        
        # 查询访谈答题记录，关联访谈信息
        res = db.table("interview_results")\
            .select("interview_id, question_id, answer, submitted_at, is_correct, interviews!fk_interview_results_interview_id(title, exam_id)")\
            .eq("user_id", user_id)\
            .is_("deleted_at", "null")\
            .execute()
        
        results = res.data or []
        
        if not results:
            return jsonify([])

        logger.info(f"查询到 {len(results)} 条 interview_results 记录")
        
        # 获取访谈信息
        interview_ids = [r.get('interview_id') for r in results if r.get('interview_id') is not None]
        interview_ids = list(set(interview_ids))
        logger.info(f"interview_ids: {interview_ids}")
        
        if not interview_ids:
            return jsonify([])
        
        interview_res = db.table("interviews").select("id, title, exam_id").in_("id", interview_ids).execute()
        logger.info(f"interview_res.data: {interview_res.data}")

        interview_map = {i['id']: i for i in (interview_res.data or [])}
        logger.info(f"interview_map keys: {list(interview_map.keys())}")

        # 获取所有 question_id
        question_ids = [r.get('question_id') for r in results if r.get('question_id') is not None]
        question_ids = list(set(question_ids))
        
        # 查询完整的题目信息（包括 content 和 options）
        questions_map = {}
        if question_ids:
            q_res = db.table("questions").select("id, num, content, content_cn, content_en, content_raw, type, options").in_("id", question_ids).execute()
            for q in (q_res.data or []):
                # 解析 options
                opts = q.get('options', {})
                if isinstance(opts, str):
                    try:
                        opts = json.loads(opts)
                    except:
                        opts = {}
                q['options'] = opts
                questions_map[q['id']] = q
        
        # 获取所有关联的考试ID
        exam_ids = [i.get('exam_id') for i in interview_map.values() if i.get('exam_id') is not None]
        exam_ids = list(set(exam_ids))
        
        exam_country_map = {}
        if exam_ids:
            exams_res = db.table("exams").select("id, countries, country").in_("id", exam_ids).execute()
            for exam in (exams_res.data or []):
                countries_data = exam.get('countries')
                if countries_data:
                    if isinstance(countries_data, str):
                        try:
                            countries_data = json.loads(countries_data)
                        except:
                            countries_data = [exam.get('country', '')] if exam.get('country') else []
                    elif isinstance(countries_data, list):
                        pass
                    else:
                        countries_data = [exam.get('country', '')] if exam.get('country') else []
                else:
                    countries_data = [exam.get('country', '')] if exam.get('country') else []
                exam_country_map[exam['id']] = ', '.join(countries_data) if countries_data else ''
        
        # 按 interview_id 分组聚合
        interviews = {}
        for r in results:
            try:
                inv_id = r.get('interview_id')
                if not inv_id:
                    continue
                    
                if inv_id not in interviews:
                    interview_info = interview_map.get(inv_id, {})
                    exam_id = interview_info.get('exam_id')
                    country_display = exam_country_map.get(exam_id, '')
                    
                    interviews[inv_id] = {
                        'interview_id': inv_id,
                        'title': interview_info.get('title', '未知访谈'),
                        'country': country_display,
                        'questions': [],
                        'completed_at': None,
                        'correct_count': 0,
                        'total_questions': 0,
                        'answered_count': 0
                    }
                
                q_info = questions_map.get(r.get('question_id'), {})

                # 确保 is_correct 是布尔值
                is_correct_raw = r.get('is_correct')
                if is_correct_raw is None:
                    is_correct_bool = False
                elif isinstance(is_correct_raw, bool):
                    is_correct_bool = is_correct_raw
                elif isinstance(is_correct_raw, str):
                    is_correct_bool = is_correct_raw.lower() == 'true'
                elif isinstance(is_correct_raw, (int, float)):
                    is_correct_bool = is_correct_raw == 1
                else:
                    is_correct_bool = False
                
                # 统计答对数量
                has_answer = r.get('answer') and r.get('answer') != '未作答'
                if has_answer and is_correct_bool:
                    interviews[inv_id]['correct_count'] += 1
                if has_answer:
                    interviews[inv_id]['answered_count'] += 1
                interviews[inv_id]['total_questions'] += 1
                
                # 获取正确答案
                correct_answer = q_info.get('answer', '')
                if q_info.get('type') == 'judge' and correct_answer:
                    if correct_answer.upper() in ('T', 'TRUE', '√', '正确', '对'):
                        correct_answer = 'T (正确)'
                    elif correct_answer.upper() in ('F', 'FALSE', '×', '错误', '错'):
                        correct_answer = 'F (错误)'
                
                interviews[inv_id]['questions'].append({
                    'question_id': r.get('question_id'),
                    'answer': r.get('answer', '未作答'),
                    'user_answer': r.get('answer', ''),
                    'is_correct': r.get('is_correct'),
                    'correct_answer': correct_answer,
                    'content': q_info.get('content_cn') or q_info.get('content') or q_info.get('content_raw', ''),
                    'options': q_info.get('options', {}),
                    'type': q_info.get('type', 'single'),
                    'num': q_info.get('num', 0)
                })
                
                # 更新完成时间（取最新的 submitted_at）
                submitted_at = r.get('submitted_at')
                if submitted_at:
                    current_completed = interviews[inv_id]['completed_at']
                    # ⭐ 关键修复：确保 current_completed 不是 None
                    if current_completed is None:
                        interviews[inv_id]['completed_at'] = submitted_at
                    elif isinstance(submitted_at, str) and isinstance(current_completed, str):
                        if submitted_at > current_completed:
                            interviews[inv_id]['completed_at'] = submitted_at
                    else:
                        # 如果类型不一致，直接赋值
                        interviews[inv_id]['completed_at'] = submitted_at
                        
            except Exception as inner_e:
                logger.error(f"处理记录 {r} 时出错: {inner_e}", exc_info=True)
                continue
        
        # 转换为列表
        result_list = []
        for inv in interviews.values():
            inv['question_count'] = len(inv['questions'])
            result_list.append(inv)
        
        # ⭐ 安全排序：确保 key 是字符串
        result_list.sort(key=lambda x: str(x.get('completed_at', '')), reverse=True)

        return jsonify(result_list)
        
    except Exception as e:
        logger.error(f"获取访谈记录失败: {e}", exc_info=True)
        return jsonify([])

@exam_bp.route('/api/my/stats')
@login_required
def api_my_stats():
    """
    获取学员统计数据（用于仪表盘统计卡片）
    """
    user_id = session['user_id']
    db = get_supabase()
    admin_db = get_supabase_admin()
    now = datetime.now(timezone.utc)
    
    # ========== 1. 获取用户信息 ==========
    user_res = db.table("users").select("country, name_en").eq("id", user_id).maybe_single().execute()
    if not user_res.data:
        return jsonify({"success": False, "message": "用户不存在"}), 404
    user_country = user_res.data.get('country')
    
    # ========== 2. 获取所有分配的考试 ==========
    assign_res = db.table("exam_assignments").select("exam_id").eq("user_id", user_id).is_("deleted_at", "null").execute()
    assigned_exam_ids = list(set([a['exam_id'] for a in (assign_res.data or [])]))
    
    # ========== 3. 考试统计 ==========
    exam_total = 0
    exam_pending = 0
    exam_in_progress = 0
    exam_completed = 0
    
    if assigned_exam_ids:
        # 获取考试信息
        exams_res = db.table("exams").select("*").in_("id", assigned_exam_ids).is_("deleted_at", "null").execute()
        exam_map = {e['id']: e for e in (exams_res.data or [])}
        
        # 获取考试状态
        status_res = db.table("user_exam_status").select("exam_id, started_at, is_submitted").eq("user_id", user_id).execute()
        status_map = {s['exam_id']: s for s in (status_res.data or [])}
        
        # 获取已提交的考试
        results_res = db.table("exam_results").select("exam_id").eq("user_id", user_id).is_("deleted_at", "null").execute()
        submitted_exam_ids = set([r['exam_id'] for r in (results_res.data or [])])
        
        for exam_id in assigned_exam_ids:
            exam_info = exam_map.get(exam_id)
            if not exam_info:
                continue
            
            # 国家过滤
            if user_country:
                exam_countries = []
                countries_data = exam_info.get('countries') or exam_info.get('country', '')
                if isinstance(countries_data, str):
                    try:
                        exam_countries = json.loads(countries_data)
                    except:
                        exam_countries = [countries_data] if countries_data else []
                elif isinstance(countries_data, list):
                    exam_countries = countries_data
                
                if exam_countries and user_country not in exam_countries:
                    continue
            
            # ✅ 获取考试状态（排除 draft）
            start_time = exam_info.get('start_time')
            end_time = exam_info.get('end_time')
            exam_status = 'draft'
            if start_time and end_time:
                try:
                    start_dt = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
                    end_dt = datetime.fromisoformat(end_time.replace('Z', '+00:00'))
                    if now < start_dt:
                        exam_status = 'created'
                    elif now > end_dt:
                        exam_status = 'closed'
                    else:
                        exam_status = 'active'
                except:
                    exam_status = 'draft'
            
            # 跳过草稿
            if exam_status == 'draft':
                continue
            
            is_submitted = exam_id in submitted_exam_ids
            is_started = status_map.get(exam_id, {}).get('started_at') is not None
            
            # ✅ 统计逻辑
            if is_submitted:
                # 已提交 = 已完成
                exam_completed += 1
            elif is_started and exam_status == 'active':
                # 已开始且在进行中
                exam_in_progress += 1
            elif exam_status == 'closed':
                # 已关闭但未提交（跳过，不计入总数）
                continue
            else:
                # 未开始（created 或 active 但未开始）
                exam_pending += 1
            
            exam_total += 1
    
    # ========== 4. 培训签到统计 ==========
    from utils.training_helpers import parse_training_countries
    
    # 获取用户所有签到记录
    att_res = db.table("training_attendances")\
        .select("training_id, signature_url")\
        .eq("user_id", user_id)\
        .is_("deleted_at", "null")\
        .execute()
    
    signed_training_ids = set()
    signed_with_signature = set()
    for att in (att_res.data or []):
        tid = att['training_id']
        signed_training_ids.add(tid)
        signature_url = att.get('signature_url', '')
        if signature_url and signature_url != '' and signature_url != 'null':
            signed_with_signature.add(tid)
    
    # 获取培训信息（用于国家过滤）
    training_total = 0
    training_signed = 0
    training_pending = 0
    
    if signed_training_ids:
        trainings_res = db.table("trainings").select("*").in_("id", list(signed_training_ids)).is_("deleted_at", "null").execute()
        for t in (trainings_res.data or []):
            # 国家过滤
            if user_country:
                training_countries = parse_training_countries(t)
                if training_countries and user_country not in training_countries:
                    continue
            
            training_total += 1
            if t['id'] in signed_with_signature:
                training_signed += 1
            else:
                training_pending += 1
    
    # ========== 5. 访谈统计 ==========
    interview_res = db.table("interview_results").select("interview_id, submitted_at").eq("user_id", user_id).is_("deleted_at", "null").execute()
    interview_ids = list(set([r['interview_id'] for r in (interview_res.data or []) if r.get('interview_id')]))
    
    interview_total = len(interview_ids)
    interview_completed = 0
    interview_pending = 0
    
    if interview_ids:
        for inv_id in interview_ids:
            inv_results = [r for r in (interview_res.data or []) if r['interview_id'] == inv_id]
            if any(r.get('submitted_at') for r in inv_results):
                interview_completed += 1
            else:
                interview_pending += 1
    
    # ========== 6. 整体完成率 ==========
    total_items = exam_total + training_total + interview_total
    completed_items = exam_completed + training_signed + interview_completed
    completion_rate = round(completed_items / total_items * 100, 1) if total_items > 0 else 0
    
    return jsonify({
        "success": True,
        "data": {
            "exam": {
                "total": exam_total,
                "pending": exam_pending,
                "in_progress": exam_in_progress,
                "completed": exam_completed
            },
            "training": {
                "total": training_total,
                "signed": training_signed,
                "pending": training_pending
            },
            "interview": {
                "total": interview_total,
                "pending": interview_pending,
                "completed": interview_completed
            },
            "completion": {
                "rate": completion_rate,
                "total_items": total_items,
                "completed_items": completed_items
            }
        }
    })

@exam_bp.route('/api/my/stats_detail')
@login_required
def api_my_stats_detail():
    """
    获取学员统计详情（用于点击卡片弹出详情）
    统计逻辑与 /api/my/stats 完全一致
    """
    user_id = session['user_id']
    detail_type = request.args.get('type', 'exam')
    db = get_supabase()
    admin_db = get_supabase_admin()
    now = datetime.now(timezone.utc)
    
    # 获取用户信息
    user_res = db.table("users").select("country, role").eq("id", user_id).maybe_single().execute()
    if not user_res.data:
        return jsonify({"success": False, "message": "用户不存在"}), 404
    user_country = user_res.data.get('country')
    
    if detail_type == 'exam':
        # ========== 考试详情（与卡片统计完全一致）==========
        
        # 1. 获取所有分配的考试
        assign_res = db.table("exam_assignments").select("exam_id").eq("user_id", user_id).is_("deleted_at", "null").execute()
        assigned_exam_ids = list(set([a['exam_id'] for a in (assign_res.data or [])]))
        
        if not assigned_exam_ids:
            return jsonify({
                "success": True, 
                "data": [], 
                "summary": {"total": 0, "pending": 0, "in_progress": 0, "completed": 0, "closed": 0}
            })
        
        # 2. 获取考试信息
        exams_res = db.table("exams").select("*").in_("id", assigned_exam_ids).is_("deleted_at", "null").execute()
        exam_map = {e['id']: e for e in (exams_res.data or [])}
        
        # 3. 获取考试状态
        status_res = db.table("user_exam_status").select("exam_id, started_at, is_submitted").eq("user_id", user_id).execute()
        status_map = {s['exam_id']: s for s in (status_res.data or [])}
        
        # 4. 获取已提交的考试
        results_res = db.table("exam_results").select("exam_id, total_score, created_at").eq("user_id", user_id).is_("deleted_at", "null").execute()
        submitted_exam_ids = list(set([r['exam_id'] for r in (results_res.data or [])]))
        
        result = []
        for exam_id in assigned_exam_ids:
            exam_info = exam_map.get(exam_id)
            if not exam_info:
                continue
            
            # ✅ 国家过滤
            if user_country:
                exam_countries = []
                countries_data = exam_info.get('countries') or exam_info.get('country', '')
                if isinstance(countries_data, str):
                    try:
                        exam_countries = json.loads(countries_data)
                    except:
                        exam_countries = [countries_data] if countries_data else []
                elif isinstance(countries_data, list):
                    exam_countries = countries_data
                
                if exam_countries and user_country not in exam_countries:
                    continue
            
            # ✅ 获取考试状态（包括 closed）
            start_time = exam_info.get('start_time')
            end_time = exam_info.get('end_time')
            exam_status = 'draft'
            if start_time and end_time:
                try:
                    start_dt = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
                    end_dt = datetime.fromisoformat(end_time.replace('Z', '+00:00'))
                    if now < start_dt:
                        exam_status = 'created'
                    elif now > end_dt:
                        exam_status = 'closed'
                    else:
                        exam_status = 'active'
                except:
                    exam_status = 'draft'
            
            # ✅ 关键：只统计 created、active、closed 状态的考试（排除 draft）
            # 但如果是 draft 且用户已提交（不可能），或者 draft 但用户已开始（不可能）
            # 所以直接跳过 draft
            if exam_status == 'draft':
                continue
            
            user_status = status_map.get(exam_id, {})
            is_submitted = exam_id in submitted_exam_ids
            is_started = user_status.get('started_at') is not None
            
            title = exam_info.get('title')
            if not title:
                title = f'考试 #{exam_id}'
            
            # 获取分数
            score = None
            submitted_at = None
            for r in (results_res.data or []):
                if r['exam_id'] == exam_id:
                    score = r.get('total_score')
                    submitted_at = r.get('created_at')
                    break
            
            result.append({
                "exam_id": exam_id,
                "title": title,
                "start_time": exam_info.get('start_time'),
                "end_time": exam_info.get('end_time'),
                "exam_status": exam_status,
                "is_submitted": is_submitted,
                "started": is_started,
                "score": score,
                "submitted_at": submitted_at,
                "duration": exam_info.get('duration', 60),
                "pass_score": exam_info.get('pass_score', 85)
            })
        
        # 排序
        def get_sort_key(x):
            if x['is_submitted']:
                return 2
            elif x['exam_status'] == 'closed':
                return 3
            elif x['started']:
                return 1
            else:
                return 0
        
        result.sort(key=get_sort_key)
        
        # 统计
        total = len(result)
        pending = len([x for x in result if not x['is_submitted'] and not x['started'] and x['exam_status'] in ('created', 'active')])
        in_progress = len([x for x in result if x['started'] and not x['is_submitted']])
        completed = len([x for x in result if x['is_submitted']])
        closed = len([x for x in result if x['exam_status'] == 'closed' and not x['is_submitted']])
        
        return jsonify({
            "success": True, 
            "data": result, 
            "summary": {
                "total": total,
                "pending": pending,
                "in_progress": in_progress,
                "completed": completed,
                "closed": closed
            }
        })
    
    elif detail_type == 'training':
        # ========== 培训签到详情 ==========
        from utils.training_helpers import parse_training_countries
        
        # 获取用户所有签到记录
        att_res = db.table("training_attendances")\
            .select("training_id, signature_url, sign_time, signed_name")\
            .eq("user_id", user_id)\
            .is_("deleted_at", "null")\
            .execute()
        
        signed_training_ids = set()
        for att in (att_res.data or []):
            signed_training_ids.add(att['training_id'])
        
        if not signed_training_ids:
            return jsonify({
                "success": True, 
                "data": [], 
                "summary": {"total": 0, "signed": 0, "resign": 0}
            })
        
        # 获取培训信息
        trainings_res = db.table("trainings").select("*").in_("id", list(signed_training_ids)).is_("deleted_at", "null").execute()
        
        result = []
        for t in (trainings_res.data or []):
            tid = t['id']
            
            # 国家过滤
            if user_country:
                training_countries = parse_training_countries(t)
                if training_countries and user_country not in training_countries:
                    continue
            
            # 获取签到记录
            att = next((a for a in (att_res.data or []) if a['training_id'] == tid), {})
            signature_url = att.get('signature_url', '')
            
            is_signed = signature_url and signature_url != '' and signature_url != 'null'
            needs_resign = not is_signed
            
            name = t.get('name')
            if not name:
                name = f'培训 #{tid}'
            
            # 国家显示
            countries = parse_training_countries(t)
            country_display = ', '.join(countries) if countries else t.get('country', '')
            
            result.append({
                "training_id": tid,
                "name": name,
                "country": country_display,
                "sign_time": att.get('sign_time'),
                "signed_name": att.get('signed_name', ''),
                "signature_url": signature_url,
                "is_signed": is_signed,
                "needs_resign": needs_resign
            })
        
        total = len(result)
        signed = len([x for x in result if x['is_signed']])
        resign = len([x for x in result if x['needs_resign']])
        
        return jsonify({
            "success": True, 
            "data": result, 
            "summary": {
                "total": total,
                "signed": signed,
                "resign": resign
            }
        })
    
    elif detail_type == 'interview':
        # ========== 访谈详情 ==========
        
        results_res = db.table("interview_results").select("interview_id, submitted_at, is_correct, answer").eq("user_id", user_id).is_("deleted_at", "null").execute()
        interview_result_map = {}
        for r in (results_res.data or []):
            inv_id = r['interview_id']
            if inv_id not in interview_result_map:
                interview_result_map[inv_id] = []
            interview_result_map[inv_id].append(r)
        
        interview_ids = list(interview_result_map.keys())
        
        if not interview_ids:
            return jsonify({
                "success": True, 
                "data": [], 
                "summary": {"total": 0, "pending": 0, "completed": 0}
            })
        
        inv_res = db.table("interviews").select("*").in_("id", interview_ids).is_("deleted_at", "null").execute()
        inv_map = {i['id']: i for i in (inv_res.data or [])}
        
        result = []
        for inv_id in interview_ids:
            inv_info = inv_map.get(inv_id)
            if not inv_info:
                continue
            
            inv_results = interview_result_map.get(inv_id, [])
            has_submitted = any(r.get('submitted_at') for r in inv_results)
            
            correct_count = sum(1 for r in inv_results if r.get('is_correct') == True)
            total_questions = len(inv_results)
            
            title = inv_info.get('title')
            if not title:
                title = f'访谈 #{inv_id}'
            
            result.append({
                "interview_id": inv_id,
                "title": title,
                "start_time": inv_info.get('start_time'),
                "end_time": inv_info.get('end_time'),
                "question_count": inv_info.get('question_count', 0),
                "is_completed": has_submitted,
                "correct_count": correct_count,
                "total_questions": total_questions,
                "exam_id": inv_info.get('exam_id')
            })
        
        total = len(result)
        pending = len([x for x in result if not x['is_completed']])
        completed = len([x for x in result if x['is_completed']])
        
        return jsonify({
            "success": True, 
            "data": result, 
            "summary": {
                "total": total,
                "pending": pending,
                "completed": completed
            }
        })
    
    elif detail_type == 'completion':
        # ========== 完成率详情（与卡片统计完全一致）==========
        from utils.training_helpers import parse_training_countries
        
        # ✅ 1. 考试统计（与卡片统计一致）
        assign_res = db.table("exam_assignments").select("exam_id").eq("user_id", user_id).is_("deleted_at", "null").execute()
        assigned_exam_ids = list(set([a['exam_id'] for a in (assign_res.data or [])]))
        
        valid_exam_ids = []
        exam_titles = {}
        exam_statuses = {}
        if assigned_exam_ids:
            exams_res = db.table("exams").select("*").in_("id", assigned_exam_ids).execute()
            for e in (exams_res.data or []):
                # 国家过滤
                if user_country:
                    exam_countries = []
                    countries_data = e.get('countries') or e.get('country', '')
                    if isinstance(countries_data, str):
                        try:
                            exam_countries = json.loads(countries_data)
                        except:
                            exam_countries = [countries_data] if countries_data else []
                    elif isinstance(countries_data, list):
                        exam_countries = countries_data
                    
                    if exam_countries and user_country not in exam_countries:
                        continue
                
                # ✅ 获取考试状态
                start_time = e.get('start_time')
                end_time = e.get('end_time')
                status = 'draft'
                if start_time and end_time:
                    try:
                        start_dt = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
                        end_dt = datetime.fromisoformat(end_time.replace('Z', '+00:00'))
                        if now < start_dt:
                            status = 'created'
                        elif now > end_dt:
                            status = 'closed'
                        else:
                            status = 'active'
                    except:
                        status = 'draft'
                
                # ✅ 只统计 created、active、closed（排除 draft）
                if status == 'draft':
                    continue
                
                valid_exam_ids.append(e['id'])
                exam_titles[e['id']] = e.get('title', f'考试 #{e["id"]}')
                exam_statuses[e['id']] = status
        
        results_res = db.table("exam_results").select("exam_id").eq("user_id", user_id).in_("exam_id", valid_exam_ids).is_("deleted_at", "null").execute()
        completed_exam_ids = set([r['exam_id'] for r in (results_res.data or [])])
        
        # ✅ 2. 培训统计（只统计已签到的培训）
        att_res = db.table("training_attendances")\
            .select("training_id, signature_url")\
            .eq("user_id", user_id)\
            .is_("deleted_at", "null")\
            .execute()
        
        signed_training_ids = set()
        signed_with_signature = set()
        for att in (att_res.data or []):
            tid = att['training_id']
            signed_training_ids.add(tid)
            signature_url = att.get('signature_url', '')
            if signature_url and signature_url != '' and signature_url != 'null':
                signed_with_signature.add(tid)
        
        valid_training_ids = []
        training_names = {}
        if signed_training_ids:
            trainings_res = db.table("trainings").select("*").in_("id", list(signed_training_ids)).is_("deleted_at", "null").execute()
            for t in (trainings_res.data or []):
                # 国家过滤
                if user_country:
                    training_countries = parse_training_countries(t)
                    if training_countries and user_country not in training_countries:
                        continue
                valid_training_ids.append(t['id'])
                training_names[t['id']] = t.get('name', f'培训 #{t["id"]}')
        
        # ✅ 3. 访谈统计
        results_res = db.table("interview_results").select("interview_id, submitted_at").eq("user_id", user_id).is_("deleted_at", "null").execute()
        interview_ids = list(set([r['interview_id'] for r in (results_res.data or []) if r.get('interview_id')]))
        
        interview_names = {}
        if interview_ids:
            inv_res = db.table("interviews").select("id, title").in_("id", interview_ids).is_("deleted_at", "null").execute()
            for inv in (inv_res.data or []):
                interview_names[inv['id']] = inv.get('title', f'访谈 #{inv["id"]}')
        
        completed_interview_ids = set()
        for inv_id in interview_ids:
            inv_results = [r for r in (results_res.data or []) if r['interview_id'] == inv_id]
            if any(r.get('submitted_at') for r in inv_results):
                completed_interview_ids.add(inv_id)
        
        # ✅ 4. 构建详细项列表
        items = []
        
        # 考试项（包括已关闭的）
        for exam_id in valid_exam_ids:
            items.append({
                "type": "exam",
                "id": exam_id,
                "name": exam_titles.get(exam_id, f'考试 #{exam_id}'),
                "is_completed": exam_id in completed_exam_ids,
                "status": "已完成" if exam_id in completed_exam_ids else "待完成",
                "exam_status": exam_statuses.get(exam_id, '')
            })
        
        # 培训项
        for tid in valid_training_ids:
            is_signed = tid in signed_with_signature
            items.append({
                "type": "training",
                "id": tid,
                "name": training_names.get(tid, f'培训 #{tid}'),
                "is_completed": is_signed,
                "status": "已签到" if is_signed else "待签到"
            })
        
        # 访谈项
        for inv_id in interview_ids:
            items.append({
                "type": "interview",
                "id": inv_id,
                "name": interview_names.get(inv_id, f'访谈 #{inv_id}'),
                "is_completed": inv_id in completed_interview_ids,
                "status": "已完成" if inv_id in completed_interview_ids else "待完成"
            })
        
        # ✅ 5. 计算汇总
        total_items = len(valid_exam_ids) + len(valid_training_ids) + len(interview_ids)
        completed_items = len(completed_exam_ids) + len(signed_with_signature) + len(completed_interview_ids)
        
        return jsonify({
            "success": True,
            "data": items,
            "summary": {
                "total_items": total_items,
                "completed_items": completed_items,
                "rate": round(completed_items / total_items * 100, 1) if total_items > 0 else 0,
                "exam": {"total": len(valid_exam_ids), "completed": len(completed_exam_ids)},
                "training": {"total": len(valid_training_ids), "completed": len(signed_with_signature)},
                "interview": {"total": len(interview_ids), "completed": len(completed_interview_ids)}
            }
        })
    
    return jsonify({"success": False, "message": "未知类型"}), 400

