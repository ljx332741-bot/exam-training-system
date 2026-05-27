# routes/api_exam.py
import logging
import json
from datetime import datetime, timezone
from . import exam_bp
from services.db import get_supabase
from services import exam, export
from utils.status import get_exam_status
from routes.helpers import login_required
import os
import uuid
import sys
import traceback
import atexit
import zipfile
import pytz
import pdfkit
import openpyxl
import random
import base64, re
import secrets, string
from datetime import datetime, timezone, timedelta, date
from dateutil import parser
from apscheduler.schedulers.background import BackgroundScheduler
from io import BytesIO
from flask import (
    Flask, render_template, request, redirect, url_for, 
    session, flash, jsonify, send_file, make_response
)
from routes.helpers import login_required, admin_required, robust_parse_json
from supabase import create_client
from functools import wraps
from services.db import get_supabase
from services import auth, exam, export
from services.export import find_wkhtmltopdf
from services.auth import hash_password
from config import Config
from utils.status import get_exam_status
from utils.common import (
    match_country_code, quarter_to_date_range, get_reviewer_by_country, format_admin_countries_display,
    format_countries_display,  # ✅ 新增
    format_single_country_display  # ✅ 新增（可选）
)
from utils.email_notifier import send_bilingual_notification, EmailScenario, _format_time, _send_training_notifications
from utils.permissions import (
    is_developer, get_developer_id, has_role, can_manage_role,
    can_view_user, get_admin_allowed_countries, set_admin_allowed_countries,
    parse_countries_input, developer_required, super_admin_required,
    apply_country_filter
)
from utils.training_helpers import get_training_country_templates_status
from dotenv import load_dotenv
from routes import register_blueprints
from services.scheduler import init_scheduler
logger = logging.getLogger(__name__)

@exam_bp.route('/dashboard')
@login_required
def dashboard():
    db = get_supabase()
    user_id = session['user_id']
    now = datetime.now(timezone.utc)
    
    # ✅ 获取当前用户的国家
    user_info = db.table("users").select("country").eq("id", user_id).single().execute()
    user_country = user_info.data.get('country') if user_info.data else None
    
    try:
        assign_res = db.table("exam_assignments").select("exam_id").eq("user_id", user_id).execute()
        assigned_exam_ids = {a['exam_id'] for a in assign_res.data} if assign_res.data else set()

        exams_res = db.table("exams").select("*").execute()
        exams = []

        for ex in exams_res.data or []:
            exam_id = ex['id']

            # 简化逻辑：直接复用原 app.py 逻辑，注意变量名冲突
            any_assign = db.table("exam_assignments").select("id").eq("exam_id", exam_id).limit(1).execute()
            if any_assign.data and exam_id not in assigned_exam_ids: continue
            
            # ✅ 新增：按用户国家过滤考试
            # 获取考试的目标国家列表
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
            
            # ✅ 如果考试指定了国家，且用户有国家，检查用户国家是否在考试国家列表中
            if exam_countries and user_country:
                if user_country not in exam_countries:
                    continue  # 用户国家不在考试目标国家中，跳过
            
            status = get_exam_status(ex)
            if status not in ('created', 'active'): continue

            q_count = db.table("questions").select("*", count="exact").eq("exam_id", exam_id).execute()
            ex['questions_count'] = q_count.count if hasattr(q_count, 'count') else len(q_count.data or [])

            status_res = db.table("user_exam_status").select("exam_id, started_at, is_submitted, reset_at").eq("user_id", user_id).eq("exam_id", exam_id).maybe_single().execute()
            status_data = status_res.data if status_res and status_res.data else {}

            ex['user_status'] = {
                'is_submitted': status_data.get('is_submitted', False), 
                'started': False, 
                'remaining': None
            }
            if not status_data.get('is_submitted') and status_data.get('started_at'):
                try:
                    start_dt = datetime.fromisoformat(status_data['started_at'])
                    elapsed = (now - start_dt).total_seconds()
                    ex['user_status']['started'] = True
                    ex['user_status']['remaining'] = max(0, ex.get('duration', 60)*60 - int(elapsed))
                except: pass

            start_time = ex.get('start_time')
            can_enter = False
            if start_time:
                try:
                    start_dt = datetime.fromisoformat(start_time)
                    can_enter = now >= start_dt
                except: pass
            ex['can_enter'] = can_enter

            total_score = sum(q.get('score', 0) for q in db.table("questions").select("score").eq("exam_id", exam_id).execute().data or [])
            ex['total_score'] = total_score if total_score else 100
            exams.append(ex)

        results_res = db.table("exam_results").select("*").eq("user_id", user_id).order("created_at", desc=True).limit(5).execute()
        results = []
        if results_res.data:
            valid_exams = db.table("exams").select("id, title").in_("id", [r['exam_id'] for r in results_res.data]).is_("deleted_at", "null").execute()
            valid_map = {e['id']: e['title'] for e in valid_exams.data or []}
            for r in results_res.data:
                if r['exam_id'] in valid_map: r['exam_title'] = valid_map[r['exam_id']]; results.append(r)
        
        user_info = db.table("users").select("name_cn, name_en, email").eq("id", user_id).single().execute().data
        return render_template('exam/dashboard.html', exams=exams, results=results, training_open=True, user_signed_in=False, user_name=(user_info.get('name_cn') or user_info.get('name_en')))
    except Exception as e:
        logger.error(f"dashboard 异常: {e}")
        return render_template('exam/dashboard.html', exams=[], results=[], training_open=True, user_signed_in=False)

@exam_bp.route('/exam/take/<int:exam_id>')
@login_required
def take_exam(exam_id):
    db = get_supabase()
    user_id = session['user_id']
    now = datetime.now(timezone.utc)
    try:
        exam_info = db.table("exams").select("start_time, end_time, duration").eq("id", exam_id).maybe_single().execute()
        if not exam_info.data: flash("考试不存在", "danger"); return redirect(url_for('exam.dashboard'))
        exam = exam_info.data
        if exam.get('end_time') and now > datetime.fromisoformat(exam['end_time']): flash({'msg': 'exam_ended'}, 'warning'); return redirect(url_for('exam.dashboard'))
        if exam.get('start_time') and now < datetime.fromisoformat(exam['start_time']): flash({'msg': 'exam_not_started'}, 'warning'); return redirect(url_for('exam.dashboard'))
        
        status = db.table("user_exam_status").select("*").eq("user_id", user_id).eq("exam_id", exam_id).maybe_single().execute()
        if status and status.data and status.data.get("is_submitted"): flash("已完成本场考试", "warning"); return redirect(url_for('exam.dashboard'))
        
        duration_minutes = exam.get('duration', 60)
        total_seconds = duration_minutes * 60
        started_at = None; reset_timer = False; reset_token = ''
        
        if status and status.data:
            started_at = status.data.get("started_at")
            reset_at = status.data.get("reset_at")
            if reset_at and (not status.data.get("submitted_at") or reset_at > status.data.get("submitted_at")):
                reset_timer = True; reset_token = reset_at
            else: reset_token = ''
            if not started_at or reset_timer:
                started_at = now.isoformat()
                db.table("user_exam_status").update({"started_at": started_at, "reset_at": None}).eq("user_id", user_id).eq("exam_id", exam_id).execute()
        else:
            started_at = now.isoformat()
            db.table("user_exam_status").insert({"user_id": user_id, "exam_id": exam_id, "started_at": started_at, "is_submitted": False}).execute()
        
        remaining = total_seconds
        if started_at:
            try:
                elapsed = (now - datetime.fromisoformat(started_at)).total_seconds()
                remaining = max(0, total_seconds - int(elapsed))
            except: pass
        
        if remaining <= 0:
            # 自动提交逻辑简化，复用 submit_exam 逻辑或保留原样
            if not (status and status.data and status.data.get("is_submitted")):
                try:
                    draft = db.table("user_exam_drafts").select("answers").eq("user_id", user_id).eq("exam_id", exam_id).maybe_single().execute()
                    answers = {}
                    if draft and draft.data:
                        raw = draft.data.get('answers')
                        try: answers = json.loads(raw) if isinstance(raw, str) else raw
                        except: pass
                    grade = exam.auto_grade(answers, exam_id)
                    exam.save_result(user_id, exam_id, answers, grade['total'], grade['details'], {})
                    existing = db.table("user_exam_status").select("id").eq("user_id", user_id).eq("exam_id", exam_id).maybe_single().execute()
                    update_data = {"is_submitted": True, "submitted_at": now.isoformat(), "reset_at": None}
                    if existing and existing.data: db.table("user_exam_status").update(update_data).eq("id", existing.data['id']).execute()
                    else: update_data.update({"user_id": user_id, "exam_id": exam_id, "started_at": started_at}); db.table("user_exam_status").insert(update_data).execute()
                    db.table("user_exam_drafts").delete().eq("user_id", user_id).eq("exam_id", exam_id).execute()
                    flash({'msg': 'flash_time_expired_exam_automatically'}, "info")
                except: flash({'msg': 'flash_time_expired_exam_automatic_failed'}, "danger")
            return redirect(url_for('exam.dashboard'))

        qs = db.table("questions").select("*").eq("exam_id", exam_id).order("num").execute()
        questions = qs.data or []
        for q in questions:
            if isinstance(q.get('options'), str):
                try: q['options'] = json.loads(q['options'])
                except: q['options'] = {}
        user_info = db.table("users").select("name_en, email").eq("id", user_id).single().execute().data
        user_display_name = f"{user_info.get('name_en', '')} ({session.get('user_email', '')})" if user_info.get('name_en') else session.get('user_email', 'User')
        return render_template('exam/take.html', exam_id=exam_id, questions=questions, duration_minutes=duration_minutes, server_remaining_seconds=remaining, reset_timer=reset_timer, reset_token=reset_token, user_id=user_id, user_display_name=user_display_name)
    except Exception as e:
        logger.error(f"take_exam 异常: {e}")
        flash({'msg': 'flash_loading_failed_try_later'}, "danger")
        return redirect(url_for('exam.dashboard'))

@exam_bp.route('/api/exam/draft', methods=['POST'])
@login_required
def save_exam_draft():
    db = get_supabase()
    data = request.get_json()
    if not data: return jsonify({"success": False, "message": "无效请求"}), 400
    exam_id = data.get('exam_id'); answers = data.get('answers', {})
    if not exam_id: return jsonify({"success": False, "message": "缺少考试ID"}), 400
    user_id = session.get('user_id')
    if not user_id: return jsonify({"success": False, "message": "未登录"}), 401
    answers_json = json.dumps(answers) if isinstance(answers, dict) else json.dumps({})
    existing = db.table("user_exam_drafts").select("id").eq("user_id", user_id).eq("exam_id", int(exam_id)).maybe_single().execute()
    now = datetime.utcnow().isoformat()
    if existing and hasattr(existing, 'data') and existing.data:
        db.table("user_exam_drafts").update({"answers": answers_json, "updated_at": now}).eq("id", existing.data['id']).execute()
    else:
        db.table("user_exam_drafts").insert({"user_id": user_id, "exam_id": int(exam_id), "answers": answers_json, "updated_at": now}).execute()
    return jsonify({"success": True})

@exam_bp.route('/exam/submit/<int:exam_id>', methods=['POST'])
@login_required
def submit_exam(exam_id):
    db = get_supabase()
    user_id = session['user_id']
    # 超时检查
    exam_info = db.table("exams").select("duration").eq("id", exam_id).maybe_single().execute()
    duration_minutes = exam_info.data.get("duration", 60) if exam_info.data else 60
    status = db.table("user_exam_status").select("started_at").eq("user_id", user_id).eq("exam_id", exam_id).maybe_single().execute()
    if status.data and status.data.get("started_at"):
        start_dt = datetime.fromisoformat(status.data['started_at'])
        if (datetime.now(timezone.utc) - start_dt).total_seconds() > duration_minutes * 60:
            flash({'msg': 'exam_timeout'}, 'danger'); return redirect(url_for('exam.dashboard'))
    # 重复提交检查
    try:
        existing = db.table("user_exam_status").select("id").eq("user_id", user_id).eq("exam_id", exam_id).maybe_single().execute()
        if existing.data and existing.data.get("is_submitted"): flash({'msg': 'already_submitted'}, 'warning'); return redirect(url_for('exam.dashboard'))
    except: pass

    answers = {}
    for key, values in request.form.to_dict(flat=False).items():
        if key.startswith('q_'):
            answers[key] = values[0] if len(values) == 1 else ''.join(sorted(values))
    try: grade = exam.auto_grade(answers, exam_id)
    except Exception as e: logger.error(f"评分失败: {e}"); flash({'msg': 'grading_error'}, 'danger'); return redirect(url_for('exam.dashboard'))

    customs = {f"c{i}": request.form.get(f"custom{i}", "") for i in range(1, 6)}
    exam.save_result(user_id, exam_id, answers, grade['total'], grade['details'], customs)
    # 备份答案
    try:
        draft_res = db.table("user_exam_drafts").select("id").eq("user_id", user_id).eq("exam_id", exam_id).maybe_single().execute()
        if draft_res and draft_res.data: db.table("user_exam_drafts").update({"answers": json.dumps(answers)}).eq("id", draft_res.data['id']).execute()
        else: db.table("user_exam_drafts").insert({"user_id": user_id, "exam_id": exam_id, "answers": json.dumps(answers)}).execute()
    except: pass

    update_data = {"is_submitted": True, "submitted_at": datetime.now().isoformat(), "reset_at": None}
    existing = db.table("user_exam_status").select("id").eq("user_id", user_id).eq("exam_id", exam_id).maybe_single().execute()
    if existing.data: db.table("user_exam_status").update(update_data).eq("id", existing.data['id']).execute()
    else: update_data.update({"user_id": user_id, "exam_id": exam_id}); db.table("user_exam_status").insert(update_data).execute()
    db.table("user_exam_drafts").delete().eq("user_id", user_id).eq("exam_id", exam_id).execute()
    flash(f'交卷成功！得分：{grade["total"]}', 'success')
    return redirect(url_for('exam.dashboard'))

@exam_bp.route('/exam/result/<int:result_id>')
@login_required
def exam_result_detail(result_id):
    db = get_supabase()
    result_res = db.table("exam_results").select("*").eq("id", result_id).maybe_single().execute()
    if not result_res.data: flash({'msg': 'result_not_found'}, 'danger'); return redirect(url_for('exam.dashboard'))
    result = result_res.data
    if result['user_id'] != session['user_id']: flash({'msg': 'access_denied'}, 'danger'); return redirect(url_for('exam.dashboard'))
    user_info = db.table("users").select("email, name_en").eq("id", result['user_id']).maybe_single().execute()
    result['users'] = user_info.data if user_info.data else {}
    exam_res = db.table("exams").select("title").eq("id", result['exam_id']).maybe_single().execute()
    result['exams'] = {"title": exam_res.data.get("title") if exam_res.data else "未知考试"}
    questions = db.table("questions").select("*").eq("exam_id", result['exam_id']).order("num").execute()
    return render_template('exam/result_detail.html', result=result, questions=questions.data or [], answers=robust_parse_json(result.get('answers')), details=robust_parse_json(result.get('details')))

@exam_bp.route('/exam/export_pdf/<int:result_id>')
@login_required
def exam_export_pdf(result_id):
    db = get_supabase()
    result_res = db.table("exam_results").select("*").eq("id", result_id).execute()
    if not result_res.data: flash({'msg': 'result_not_found'}, 'danger'); return redirect(url_for('exam.dashboard'))
    result = result_res.data[0]
    if result['user_id'] != session['user_id']: flash({'msg': 'access_denied'}, 'danger'); return redirect(url_for('exam.dashboard'))
    user_data = db.table("users").select("*").eq("id", result['user_id']).execute().data[0] if db.table("users").select("*").eq("id", result['user_id']).execute().data else {}
    exam_data = db.table("exams").select("*").eq("id", result['exam_id']).execute().data[0] if db.table("exams").select("*").eq("id", result['exam_id']).execute().data else {}
    from routes.helpers import robust_parse_json
    answers = robust_parse_json(result.get('answers'))
    details = robust_parse_json(result.get('details'))
    questions = db.table("questions").select("*").eq("exam_id", result['exam_id']).order("num").execute().data or []
    from routes.helpers import get_default_reviewer_by_country
    reviewer = get_default_reviewer_by_country(user_data.get('country')) or exam_data.get('reviewer') or "Administrator"
    try:
        pdf_buffer = export.generate_user_pdf(user_data.get('name_cn') or user_data.get('name_en', '未知考生'), user_data.get('email', ''), exam_data.get('title', '未命名考试'), result.get('total_score', 0), questions, answers, details, result.get('created_at', ''), reviewer)
        from flask import send_file
        return send_file(pdf_buffer, mimetype="application/pdf", as_attachment=True, download_name=f"Transcript_{user_data.get('name_en', 'exam')}_{exam_data.get('title', 'exam')}.pdf")
    except Exception as e:
        logger.error(f"PDF生成失败: {e}")
        flash({'msg': 'pdf_generation_error'}, 'danger'); return redirect(url_for('exam.dashboard'))

@exam_bp.route('/api/my/interviews')
@login_required
def my_interviews():
    db = get_supabase()
    user_id = session['user_id']
    res = db.table("interview_results").select("interview_id").eq("user_id", user_id).is_("deleted_at", "null").execute()
    ids = list(set(r['interview_id'] for r in res.data or []))
    if not ids: return jsonify([])
    inv_res = db.table("interviews").select("*").in_("id", ids).execute()
    now = datetime.now(timezone.utc).isoformat()
    active = []
    for inv in inv_res.data or []:
        if inv.get('start_time') and inv.get('end_time') and inv['start_time'] < now < inv['end_time']:
            answers = db.table("interview_results").select("answer").eq("interview_id", inv['id']).eq("user_id", user_id).execute()
            inv['is_completed'] = all(row.get('answer') for row in answers.data or [])
            active.append(inv)
    return jsonify(active)