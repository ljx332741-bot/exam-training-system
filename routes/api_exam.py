# routes/api_exam.py
import logging
import json
from . import exam_bp
import os
import re
from dateutil import parser
from datetime import datetime, timezone, date
from flask import (
    Flask, render_template, request, redirect, url_for, 
    session, flash, jsonify, send_file
)
from utils.common import get_reviewer_by_country
from routes.helpers import login_required, admin_required, robust_parse_json, get_default_reviewer_by_country, safe_parse_datetime
from services.db import get_supabase
from services import auth, exam, export
from utils.status import get_exam_status

logger = logging.getLogger(__name__)

# routes/api_exam.py

@exam_bp.route('/dashboard')
@login_required
def dashboard():
    db = get_supabase()
    user_id = session['user_id']
    now = datetime.now(timezone.utc)
    
    # 获取当前用户的国家
    user_info = db.table("users").select("country").eq("id", user_id).single().execute()
    user_country = user_info.data.get('country') if user_info.data else None
    
    try:
        # 获取分配关系
        assign_res = db.table("exam_assignments").select("exam_id").eq("user_id", user_id).execute()
        assigned_exam_ids = {a['exam_id'] for a in assign_res.data} if assign_res.data else set()
        
        # ✅ 一次性获取所有考试状态（包括已提交的）
        all_status_res = db.table("user_exam_status").select("*").eq("user_id", user_id).execute()
        status_map = {}
        for s in (all_status_res.data or []):
            status_map[s['exam_id']] = s
        
        # ✅ 获取所有已开始且未提交的考试
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
            
            # ✅ 关键：获取该用户的考试状态
            user_status = status_map.get(exam_id, {})
            
            # ✅ 关键修复：已提交的考试直接跳过
            if user_status.get('is_submitted', False):
                continue
            
            # 国家过滤
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
            
            # 只显示 created 或 active 状态的考试
            if status not in ('created', 'active'):
                continue
            
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

            # ✅ 对于已开始的考试，忽略有效期检查
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
        
        # 获取最近5条成绩记录
        results_res = db.table("exam_results").select("*").eq("user_id", user_id).order("created_at", desc=True).limit(5).execute()
        results = []
        if results_res.data:
            valid_exams = db.table("exams").select("id, title").in_("id", [r['exam_id'] for r in results_res.data]).is_("deleted_at", "null").execute()
            valid_map = {e['id']: e['title'] for e in valid_exams.data or []}
            for r in results_res.data:
                if r['exam_id'] in valid_map:
                    r['exam_title'] = valid_map[r['exam_id']]
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

'''
@exam_bp.route('/exam/take/<int:exam_id>')
@login_required
def take_exam(exam_id):
    try:
        db = get_supabase()
        user_id = session['user_id']
        now = datetime.now(timezone.utc)

        # 1. 获取考试基本信息（有效期和时长）
        exam_info = db.table("exams").select("start_time, end_time, duration").eq("id", exam_id).maybe_single().execute()
        if not exam_info.data:
            flash("考试不存在", "danger")
            return redirect(url_for('exam.dashboard'))
        
        exam = exam_info.data
        duration_minutes = exam.get('duration', 60)
        end_time = exam.get('end_time')
        start_time = exam.get('start_time')

        # 2. 检查是否已交卷
        status = db.table("user_exam_status").select("*").eq("user_id", user_id).eq("exam_id", exam_id).maybe_single().execute()

        # ✅ 检查是否已交卷
        if status and status.data and status.data.get("is_submitted"):
            flash("您已完成本场考试，无法再次进入。", "warning")
            return redirect(url_for('exam.dashboard'))
        
        # ✅ 获取 started_at
        started_at = None
        if status and status.data:
            started_at = status.data.get("started_at")
        
        # ✅ 关键修改：只在首次进入（没有 started_at）时检查有效期
        if not started_at:
            # 首次进入，检查考试是否已结束
            if end_time:
                try:
                    end_dt = safe_parse_datetime(end_time)
                    if end_dt and now > end_dt:
                        flash({'msg': 'exam_ended', 'params': []}, 'warning')
                        return redirect(url_for('dashboard'))
                except ValueError as e:
                    logger.warning(f"解析 end_time 失败: {e}")
            
            # 首次进入，检查考试是否未开始
            if start_time:
                try:
                    start_dt = safe_parse_datetime(start_time)
                    if start_dt and now < start_dt:
                        flash({'msg': 'exam_not_started', 'params': []}, 'warning')
                        return redirect(url_for('dashboard'))
                except ValueError as e:
                    logger.warning(f"解析 start_time 失败: {e}")

        # 3. 检查考试是否已结束（带异常处理
        if end_time:
            try:
                end_dt = safe_parse_datetime(end_time)
                if end_dt and now > end_dt:
                    flash({'msg': 'exam_ended', 'params': []}, 'warning')
                    return redirect(url_for('dashboard'))
            except ValueError as e:
                logger.warning(f"解析 end_time 失败: {e}, 原始值: {end_time}")
                # 解析失败时允许进入，避免误拦截

        # ✅ 4. 检查考试是否未开始（带异常处理）
        if start_time:
            try:
                start_dt = safe_parse_datetime(start_time)
                if start_dt and now < start_dt:
                    flash({'msg': 'exam_not_started', 'params': []}, 'warning')
                    return redirect(url_for('dashboard'))
            except ValueError as e:
                logger.warning(f"解析 start_time 失败: {e}, 原始值: {start_time}")
                # 解析失败时允许进入，避免误拦截

        total_seconds = duration_minutes * 60
        
        # 5. 处理开始时间和重置标志
        started_at = None
        reset_timer = False
        reset_token = ''

        if status and status.data:
            started_at = status.data.get("started_at")
            submitted_at = status.data.get("submitted_at")
            reset_at = status.data.get("reset_at")
            
            if reset_at and (not submitted_at or reset_at > submitted_at):
                reset_timer = True
                reset_token = reset_at
            else:
                reset_token = ''
            
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
        
        # ✅ 6. 剩余时间计算（带异常处理）
        if started_at:
            try:
                start_dt = safe_parse_datetime(started_at)
                if start_dt:
                    elapsed = (now - start_dt).total_seconds()
                else:
                    elapsed = 0
            except Exception as e:
                logger.warning(f"解析 started_at 失败: {e}, 原始值: {started_at}")
                elapsed = 0
            # ✅ 基于考试时长计算剩余时间
            remaining = max(0, total_seconds - int(elapsed))
        else:
            remaining = total_seconds
        
        # 7. 超时处理
        if remaining <= 0:
            if not (status and status.data and status.data.get("is_submitted")):
                logger.info(f"⏰ 考试 {exam_id} 用户 {user_id} 超时，自动提交")
                try:
                    # 尝试从草稿表获取答案
                    draft = db.table("user_exam_drafts").select("answers").eq("user_id", user_id).eq("exam_id", exam_id).maybe_single().execute()
                    answers = {}
                    if draft and draft.data:
                        raw = draft.data.get('answers')
                        try:
                            answers = json.loads(raw) if isinstance(raw, str) else raw
                        except:
                            answers = {}
                    
                    grade = exam.auto_grade(answers, exam_id)
            
                    # 计算用时（从开始到超时）
                    time_used = None
                    if started_at_str:
                        try:
                            start_dt = datetime.fromisoformat(started_at_str.replace('Z', '+00:00'))
                            time_used = int((datetime.now(timezone.utc) - start_dt).total_seconds())
                        except:
                            pass
                    
                    customs = {}
                    exam.save_result(
                        user_id, exam_id, answers, grade['total'], grade['details'], 
                        customs,
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
                    
                    logger.info(f"✅ 超时自动提交完成，得分 {grade['total']}")
                    flash({'msg': 'flash_time_expired_exam_automatically', 'params': []}, "info")
                except Exception as e:
                    logger.error(f"❌ 超时自动提交失败: {e}")
                    flash({'msg': 'flash_time_expired_exam_automatic_failed', 'params': []}, "danger")
            else:
                flash({'msg': 'flash_completed_exam_cannot_reenter', 'params': []}, "warning")
            return redirect(url_for('exam.dashboard'))

        # 8. 查询题目
        qs = db.table("questions").select("*").eq("exam_id", exam_id).order("num").execute()
        questions = qs.data or []
        for q in questions:
            if isinstance(q.get('options'), str):
                try:
                    q['options'] = json.loads(q['options'])
                except:
                    q['options'] = {}
        
        # 9. 获取用户信息
        user_info_res = db.table("users").select("name_en, email").eq("id", user_id).single().execute()
        user_info = user_info_res.data
        user_display_name = f"{user_info.get('name_en', '')} ({session.get('user_email', '')})" if user_info.get('name_en') else session.get('user_email', 'User')

        logger.info(f"考试 {exam_id} 用户 {user_id} 进入，剩余 {remaining} 秒")
        return render_template(
            'exam/take.html',
            exam_id=exam_id,
            questions=questions,
            duration_minutes=duration_minutes,
            server_remaining_seconds=remaining,
            reset_timer=reset_timer,
            reset_token=reset_token,
            user_id=session['user_id'],
            user_display_name=user_display_name
        )
    except Exception as e:
        logger.error(f"take_exam 发生异常: {e}", exc_info=True)
        flash({'msg': 'flash_loading_failed_try_later', 'params': []}, "danger")
        return redirect(url_for('exam.dashboard'))
'''

# routes/api_exam.py - take_exam 函数（完整修复版）

@exam_bp.route('/exam/take/<int:exam_id>')
@login_required
def take_exam(exam_id):
    try:
        db = get_supabase()
        user_id = session['user_id']
        now = datetime.now(timezone.utc)

        # 1. 获取考试基本信息
        exam_info = db.table("exams").select("start_time, end_time, duration, title").eq("id", exam_id).maybe_single().execute()
        if not exam_info.data:
            flash("考试不存在", "danger")
            return redirect(url_for('dashboard'))
        
        exam = exam_info.data
        duration_minutes = exam.get('duration', 60)
        end_time = exam.get('end_time')
        start_time = exam.get('start_time')
        exam_title = exam.get('title', '考试')

        # 2. 获取用户考试状态
        status = db.table("user_exam_status").select("*").eq("user_id", user_id).eq("exam_id", exam_id).maybe_single().execute()
        
        # 检查是否已交卷
        if status and status.data and status.data.get("is_submitted"):
            flash("您已完成本场考试，无法再次进入。", "warning")
            return redirect(url_for('exam.dashboard'))
        
        started_at = None
        if status and status.data:
            started_at = status.data.get("started_at")
        
        # ========== 区分首次进入和再次进入 ==========
        is_first_entry = (started_at is None)
        
        if is_first_entry:
            # 首次进入：检查有效期
            if end_time:
                try:
                    end_dt = safe_parse_datetime(end_time)
                    if end_dt and now > end_dt:
                        flash({'msg': 'exam_ended', 'params': []}, 'warning')
                        return redirect(url_for('dashboard'))
                except ValueError as e:
                    logger.warning(f"解析 end_time 失败: {e}")
            
            if start_time:
                try:
                    start_dt = safe_parse_datetime(start_time)
                    if start_dt and now < start_dt:
                        flash({'msg': 'exam_not_started', 'params': []}, 'warning')
                        return redirect(url_for('dashboard'))
                except ValueError as e:
                    logger.warning(f"解析 start_time 失败: {e}")
        else:
            # 再次进入：只检查考试时长是否用完
            logger.info(f"用户 {user_id} 再次进入考试 {exam_id}，跳过有效期检查")
        
        total_seconds = duration_minutes * 60
        
        # 3. 处理开始时间和重置标志
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
        
        # 4. 计算剩余时间
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
        
        # ========== 5. 超时处理 ==========
        if remaining <= 0:
            if not (status and status.data and status.data.get("is_submitted")):
                try:
                    # ✅ 正确获取草稿答案
                    answers = {}
                    draft = db.table("user_exam_drafts").select("answers").eq("user_id", user_id).eq("exam_id", exam_id).maybe_single().execute()
                    
                    if draft and draft.data:
                        answers_data = draft.data.get('answers')
                        if answers_data:
                            if isinstance(answers_data, str):
                                answers = json.loads(answers_data)
                            else:
                                answers = answers_data
                            logger.info(f"自动提交: 从草稿获取到 {len(answers)} 条答案")
                        else:
                            logger.warning("自动提交: 草稿 answers 为空")
                    else:
                        logger.warning(f"自动提交: 未找到草稿记录 user={user_id}, exam={exam_id}")
                    
                    # 评分
                    grade = exam.auto_grade(answers, exam_id)
                    
                    # 计算用时
                    time_used = None
                    if started_at:
                        try:
                            start_dt = safe_parse_datetime(started_at)
                            if start_dt:
                                time_used = int((datetime.now(timezone.utc) - start_dt).total_seconds())
                        except:
                            pass
                    
                    # 保存成绩
                    exam.save_result(
                        user_id, exam_id, answers, grade['total'], grade['details'], 
                        customs={}, 
                        submit_method='auto', 
                        time_used=time_used
                    )
                    
                    # 更新状态
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
                    
                    # 删除草稿
                    db.table("user_exam_drafts").delete().eq("user_id", user_id).eq("exam_id", exam_id).execute()
                    
                    logger.info(f"超时自动提交完成，得分 {grade['total']}")
                    flash({'msg': 'flash_time_expired_exam_automatically', 'params': []}, "info")
                    
                except Exception as e:
                    logger.error(f"超时自动提交失败: {e}", exc_info=True)
                    flash({'msg': 'flash_time_expired_exam_automatic_failed', 'params': []}, "danger")
            
            return redirect(url_for('exam.dashboard'))
        
        # ========== 6. 正常进入考试：查询题目 ==========
        qs = db.table("questions").select("*").eq("exam_id", exam_id).order("num").execute()
        questions = qs.data or []
        for q in questions:
            if isinstance(q.get('options'), str):
                try:
                    q['options'] = json.loads(q['options'])
                except:
                    q['options'] = {}
        
        # ========== 7. 加载已保存的草稿答案（移到正确位置）==========
        saved_answers = {}
        try:
            draft = db.table("user_exam_drafts").select("answers").eq("user_id", user_id).eq("exam_id", exam_id).maybe_single().execute()
            
            if draft and draft.data:
                answers_data = draft.data.get('answers')
                if answers_data:
                    # ✅ 兼容两种格式：字典或字符串
                    if isinstance(answers_data, str):
                        saved_answers = json.loads(answers_data)
                        logger.info(f"从草稿表(JSON字符串)加载成功，共 {len(saved_answers)} 条")
                    elif isinstance(answers_data, dict):
                        saved_answers = answers_data
                        logger.info(f"从草稿表(字典)加载成功，共 {len(saved_answers)} 条")
                    else:
                        logger.warning(f"草稿答案格式异常: {type(answers_data)}")
                else:
                    logger.info("草稿表 answers 字段为空")
            else:
                logger.info(f"未找到草稿记录: user={user_id}, exam={exam_id}")
                
        except Exception as e:
            logger.error(f"加载草稿失败: {e}", exc_info=True)
            saved_answers = {}

        # 调试日志
        if saved_answers:
            sample_keys = list(saved_answers.keys())[:3]
            logger.info(f"草稿答案示例 keys: {sample_keys}")

        # ========== 8. 获取用户信息 ==========
        user_info_res = db.table("users").select("name_en, email").eq("id", user_id).single().execute()
        user_info = user_info_res.data if user_info_res.data else {}
        user_display_name = f"{user_info.get('name_en', '')} ({session.get('user_email', '')})" if user_info.get('name_en') else session.get('user_email', 'User')
        
        logger.info(f"考试 {exam_id} 用户 {user_id} 进入，剩余 {remaining} 秒，首次进入={is_first_entry}，草稿答案数={len(saved_answers)}")

        # ========== 9. 渲染模板 ==========
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
            saved_answers=json.dumps(saved_answers)  # 传递 JSON 字符串给前端
        )
        
    except Exception as e:
        logger.error(f"take_exam 发生异常: {e}", exc_info=True)
        flash({'msg': 'flash_loading_failed_try_later', 'params': []}, "danger")
        return redirect(url_for('exam.dashboard'))

'''
@exam_bp.route('/api/exam/draft', methods=['POST'])
@login_required
def save_exam_draft():
    """保存考试草稿（答案）"""
    try:
        db = get_supabase()
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "message": "无效的请求数据"}), 400

        exam_id = data.get('exam_id')
        answers = data.get('answers', {})
        if not exam_id:
            return jsonify({"success": False, "message": "缺少考试ID"}), 400

        user_id = session['user_id']
        if not user_id:
            return jsonify({"success": False, "message": "未登录"}), 401

        # 转为 JSON 字符串存入数据库
        answers_json = json.dumps(answers) if isinstance(answers, dict) else json.dumps({})
        
        # 检查是否已存在草稿记录
        try:
            existing = db.table("user_exam_drafts").select("id").eq("user_id", user_id).eq("exam_id", int(exam_id)).maybe_single().execute()
        except Exception as e:
            logger.warning(f"草稿查询失败，假定不存在: {e}")
            existing = None

        now = datetime.utcnow().isoformat()

        # 判断是否存在有效数据
        if existing and hasattr(existing, 'data') and existing.data:
            # 更新现有记录
            db.table("user_exam_drafts").update({
                    "answers": answers_json,
                    "updated_at": now}).eq("id", existing.data['id']).execute()
            logger.info(f"✏️ 草稿已更新：用户 {user_id}，考试 {exam_id}")
        else:
            # 插入新记录
            db.table("user_exam_drafts").insert({
                    "user_id": user_id,
                    "exam_id": int(exam_id),
                    "answers": answers_json,
                    "updated_at": now}).execute()
            logger.info(f"✅ 草稿已创建：用户 {user_id}，考试 {exam_id}")
        return jsonify({"success": True})
    except Exception as e:
        logger.error(f"❌ 保存草稿失败: {e}", exc_info=True)
        return jsonify({"success": False, "message": str(e)}), 500
'''

@exam_bp.route('/exam/submit/<int:exam_id>', methods=['POST'])
@login_required
def submit_exam(exam_id):
    db = get_supabase()
    user_id = session['user_id']
    now = datetime.now(timezone.utc)
    
    logger.info(f"📥 收到交卷请求：用户 {user_id}，考试 {exam_id}")
    
    # 获取开始时间和提交时间
    status = db.table("user_exam_status").select("*").eq("user_id", user_id).eq("exam_id", exam_id).maybe_single().execute()
    started_at_str = None
    if status and status.data:
        started_at_str = status.data.get('started_at')
        
        # 检查是否已交卷
        if status.data.get('is_submitted'):
            flash({'msg': 'already_submitted', 'params': []}, 'warning')
            return redirect(url_for('exam.dashboard'))
    
    # ✅ 计算用时
    time_used = None
    if started_at_str:
        try:
            # 统一处理时间格式
            start_str = started_at_str.replace('Z', '+00:00') if started_at_str.endswith('Z') else started_at_str
            start_dt = datetime.fromisoformat(start_str)
            time_used = int((now - start_dt).total_seconds())
            logger.info(f"📊 计算用时: {time_used} 秒")
            print(f"[DEBUG] started_at: {started_at_str}")
            print(f"[DEBUG] now: {now.isoformat()}")
            print(f"[DEBUG] time_used: {time_used}")
        except Exception as e:
            logger.warning(f"计算用时失败: {e}")
            print(f"[DEBUG] 计算用时异常: {e}")
    else:
        print(f"[DEBUG] 没有找到 started_at")
    
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
    
    # 超时检查
    exam_info = db.table("exams").select("duration").eq("id", exam_id).maybe_single().execute()
    duration_minutes = exam_info.data.get("duration", 60) if exam_info.data else 60
    total_seconds = duration_minutes * 60
    
    if started_at_str and time_used and time_used > total_seconds:
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
        flash({'msg': 'grading_error', 'params': []}, 'danger')
        return redirect(url_for('exam.dashboard'))
    
    # 保存成绩（传入用时）
    customs = {f"c{i}": request.form.get(f"custom{i}", "") for i in range(1, 6)}
    try:
        exam.save_result(
            user_id, exam_id, answers, grade['total'], grade['details'], 
            customs, 
            submit_method='manual', 
            time_used=time_used
        )
        logger.info(f"💾 成绩保存成功，用时: {time_used}秒")
    except Exception as e:
        logger.error(f"❌ 成绩保存失败: {e}")
        flash({'msg': 'save_score_failed', 'params': []}, 'danger')
        return redirect(url_for('exam.dashboard'))
    
    # 更新考试状态（设置 submitted_at）
    try:
        update_data = {
            "is_submitted": True,
            "submitted_at": now.isoformat(),
            "reset_at": None
        }
        
        if status and status.data:
            db.table("user_exam_status").update(update_data).eq("id", status.data['id']).execute()
        else:
            update_data.update({
                "user_id": user_id,
                "exam_id": exam_id,
                "started_at": started_at_str or now.isoformat()
            })
            db.table("user_exam_status").insert(update_data).execute()
        
        logger.info(f"✅ 考试状态已更新")
    except Exception as e:
        logger.error(f"❌ 状态写入失败: {e}")
    
    # 清理草稿
    db.table("user_exam_drafts").delete().eq("user_id", user_id).eq("exam_id", exam_id).execute()
    
    flash(f'✅ 交卷成功！得分：{grade["total"]}', 'success')
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
        return redirect(url_for('dashboard'))
    result = result_res.data[0]
    if result['user_id'] != session['user_id']:
        #flash("无权访问", "danger")
        flash({'msg': 'access_denied', 'params': []}, 'danger')
        return redirect(url_for('dashboard'))
    
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
        return redirect(url_for('dashboard'))
    
    filename = f"Transcript_{user_name}_{exam_data.get('title', 'exam')}.pdf"
    return send_file(
        pdf_buffer,
        mimetype="application/pdf",
        as_attachment=True,
        download_name=filename
    )

'''
@exam_bp.route('/api/my/interviews')
@login_required
def my_interviews():
    """获取学员的访谈列表（检查考试完成状态）"""
    user_id = session['user_id']
    db = get_supabase()
    now = datetime.now(timezone.utc).isoformat()

    print(f"=== 调试 my_interviews ===")
    print(f"用户ID: {user_id}")
    
    # 获取用户所有访谈记录
    res = db.table("interview_results").select("interview_id").eq("user_id", user_id).is_("deleted_at", "null").execute()
    interview_ids = list(set(r['interview_id'] for r in (res.data or [])))
    

    print(f"访谈ID列表: {interview_ids}")

    if not interview_ids:
        return jsonify([])
    
    # 获取访谈信息（同时关联考试信息）
    inv_res = db.table("interviews").select("*").in_("id", interview_ids).is_("deleted_at", "null").execute()
    
    result = []
    for inv in (inv_res.data or []):
        start_time = inv.get('start_time')
        end_time = inv.get('end_time')
        exam_id = inv.get('exam_id')

        print(f"\n--- 处理访谈: {inv.get('title')} (ID: {inv.get('id')}) ---")
        print(f"  关联考试ID: {exam_id}")
        print(f"  有效期: {start_time} -> {end_time}")
        
        if not start_time or not end_time:
            print(f"  跳过: 没有有效期")
            continue
        
        is_future = now < start_time
        is_active = start_time <= now <= end_time
        is_expired = now > end_time
        
        print(f"  状态: future={is_future}, active={is_active}, expired={is_expired}")

        if is_expired:
            print(f"  跳过: 已过期")
            continue
        
        # ✅ 1. 检查是否已完成访谈
        answers = db.table("interview_results").select("answer").eq("interview_id", inv['id']).eq("user_id", user_id).is_("deleted_at", "null").execute()
        is_completed = all(row.get('answer') for row in (answers.data or []))
        
        print(f"  访谈完成: {is_completed}, 答题数量: {len(answers.data or [])}")
        
        # ✅ 2. 检查关联考试是否已完成
        exam_completed = False
        exam_total_score = 0
        if exam_id:
            print(f"  查询考试结果: exam_id={exam_id}, user_id={user_id}")
            exam_result = db.table("exam_results").select("total_score").eq("exam_id", exam_id).eq("user_id", user_id).is_("deleted_at", "null").execute()
            print(f"  考试结果数量: {len(exam_result.data or [])}")
            if exam_result.data:
                for r in exam_result.data:
                    print(f"    记录: total_score={r.get('total_score')}, deleted_at={r.get('deleted_at')}")
                    if r.get('deleted_at') is None:
                        exam_completed = True
                        exam_total_score = r.get('total_score', 0)
                        print(f"    有效考试结果: score={exam_total_score}")
                        break
            else:
                print(f"  未找到考试结果")
        
        
        result.append({
            "id": inv['id'],
            "title": inv.get('title', ''),
            "start_time": start_time,
            "end_time": end_time,
            "question_count": inv.get('question_count', 0),
            "is_future": is_future,
            "is_active": is_active,
            "is_completed": is_completed,
            "exam_completed": exam_completed,      # ✅ 新增：考试是否完成
            "exam_total_score": exam_total_score   # ✅ 新增：考试成绩
        })
    
    return jsonify(result)
'''

# routes/api_exam.py - 修改 my_interviews 函数

@exam_bp.route('/api/my/interviews')
@login_required
def my_interviews():
    """获取学员的访谈列表（包括强制访谈）"""
    user_id = session['user_id']
    db = get_supabase()
    now = datetime.now(timezone.utc).isoformat()

    # 1. 获取普通的访谈记录
    res = db.table("interview_results").select("interview_id").eq("user_id", user_id).is_("deleted_at", "null").execute()
    interview_ids = list(set(r['interview_id'] for r in (res.data or [])))
    
    result = []
    
    # 2. 处理普通访谈
    if interview_ids:
        inv_res = db.table("interviews").select("*").in_("id", interview_ids).is_("deleted_at", "null").execute()
        
        for inv in (inv_res.data or []):
            start_time = inv.get('start_time')
            end_time = inv.get('end_time')
            exam_id = inv.get('exam_id')
            
            if not start_time or not end_time:
                continue
            
            is_future = now < start_time
            is_active = start_time <= now <= end_time
            
            if now > end_time:
                continue
            
            # 检查是否已完成访谈
            answers = db.table("interview_results").select("answer").eq("interview_id", inv['id']).eq("user_id", user_id).is_("deleted_at", "null").execute()
            is_completed = all(row.get('answer') for row in (answers.data or []))
            
            # 检查关联考试是否已完成
            exam_completed = False
            exam_total_score = 0
            if exam_id:
                exam_result = db.table("exam_results").select("total_score").eq("exam_id", exam_id).eq("user_id", user_id).is_("deleted_at", "null").execute()
                if exam_result.data:
                    exam_completed = True
                    exam_total_score = exam_result.data[0].get('total_score', 0)
            
            result.append({
                "id": inv['id'],
                "title": inv.get('title', ''),
                "start_time": start_time,
                "end_time": end_time,
                "question_count": inv.get('question_count', 0),
                "is_future": is_future,
                "is_active": is_active,
                "is_completed": is_completed,
                "exam_completed": exam_completed,
                "exam_total_score": exam_total_score,
                "is_force": False  # 标记为普通访谈
            })
    
    # 3. ✅ 获取强制访谈记录
    force_res = db.table("user_interview_force_records").select("*").eq("user_id", user_id).is_("deleted_at", "null").execute()
    
    for force in (force_res.data or []):
        start_time = force.get('start_time')
        end_time = force.get('end_time')
        exam_id = force.get('exam_id')
        
        if not start_time or not end_time:
            continue
        
        is_future = now < start_time
        is_active = start_time <= now <= end_time
        is_expired = now > end_time
        
        # 已过期的强制访谈不再显示
        if is_expired:
            # 可选：软删除已过期的强制访谈记录
            db.table("user_interview_force_records").update({
                "deleted_at": now,
                "deleted_by": user_id
            }).eq("id", force['id']).execute()
            continue
        
        # 检查是否已完成访谈
        answers = db.table("interview_results").select("answer").eq("interview_id", force['original_interview_id']).eq("user_id", user_id).is_("deleted_at", "null").execute()
        is_completed = all(row.get('answer') for row in (answers.data or []))
        
        # 检查关联考试是否已完成
        exam_completed = False
        exam_total_score = 0
        if exam_id:
            exam_result = db.table("exam_results").select("total_score").eq("exam_id", exam_id).eq("user_id", user_id).is_("deleted_at", "null").execute()
            if exam_result.data:
                exam_completed = True
                exam_total_score = exam_result.data[0].get('total_score', 0)
        
        result.append({
            "id": force['original_interview_id'],  # 使用原访谈ID
            "title": force.get('title', '强制访谈'),
            "start_time": start_time,
            "end_time": end_time,
            "question_count": force.get('question_count', 0),
            "is_future": is_future,
            "is_active": is_active,
            "is_completed": is_completed,
            "exam_completed": exam_completed,
            "exam_total_score": exam_total_score,
            "is_force": True,  # 标记为强制访谈
            "force_record_id": force['id']  # 记录ID，便于后续管理
        })
    
    return jsonify(result)

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
        
        # ✅ 关键修复：确保 answers 是字典，不要手动 json.dumps
        if isinstance(answers, str):
            try:
                answers = json.loads(answers)
            except:
                answers = {}
        
        # ✅ 直接存储字典对象（让 Supabase 自动处理 JSON）
        now_utc = datetime.now(timezone.utc).isoformat()
        
        existing = db.table("user_exam_drafts").select("id").eq("user_id", user_id).eq("exam_id", int(exam_id)).maybe_single().execute()
        
        if existing and existing.data:
            db.table("user_exam_drafts").update({
                "answers": answers,  # ✅ 直接传字典
                "updated_at": now_utc
            }).eq("id", existing.data['id']).execute()
            logger.info(f"草稿已更新: user={user_id}, exam={exam_id}, 答案数={len(answers)}")
        else:
            db.table("user_exam_drafts").insert({
                "user_id": user_id,
                "exam_id": int(exam_id),
                "answers": answers,  # ✅ 直接传字典
                "created_at": now_utc,
                "updated_at": now_utc
            }).execute()
            logger.info(f"草稿已创建: user={user_id}, exam={exam_id}, 答案数={len(answers)}")
        
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
