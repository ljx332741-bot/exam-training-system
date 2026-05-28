# routes/admin_export.py
import logging
import zipfile, json
import openpyxl 
from datetime import datetime, timezone, timedelta, date
from . import admin_export_bp
from services.db import get_supabase
from routes.helpers import login_required, admin_required, robust_parse_json, get_allowed_countries
from dateutil import parser
from apscheduler.schedulers.background import BackgroundScheduler
from io import BytesIO
from flask import (
    Flask, render_template, request, redirect, url_for, 
    session, flash, jsonify, send_file, make_response
)
from supabase import create_client
from functools import wraps
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
    filter_users_by_permission, apply_country_filter
)
from utils.training_helpers import get_training_country_templates_status

logger = logging.getLogger(__name__)

@admin_export_bp.route('/export/pdf/<int:exam_id>')
@login_required
def export_pdf(exam_id):
    """导出 PDF 成绩单"""
    buf = export.generate_pdf(
        "演示考生", 85, [], {}, {}, "Admin"
    )
    return send_file(
        buf, 
        mimetype="application/pdf", 
        as_attachment=True, 
        download_name=f"exam_{exam_id}.pdf"
    )

@admin_export_bp.route('/admin/export/excel/<int:training_id>/<int:exam_id>')
@login_required
@admin_required
def export_bilingual_excel(training_id, exam_id):
    """导出双语 Excel 报告"""
    country = request.args.get('country', None)
    try:
        buffer, filename = export.generate_bilingual_excel(
            training_id=training_id,
            exam_id=exam_id,
            country=country
        )
        return send_file(
            buffer,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            as_attachment=True,
            download_name=filename
        )
    except Exception as e:
        logger.error(f"❌ Excel 导出失败: {e}")
        #flash(f"❌ 导出失败: {str(e)}", "danger")
        flash({'msg': 'export_error', 'params': [str(e)]}, 'danger')
        return redirect(url_for('admin_dashboard'))

@admin_export_bp.route('/api/admin/export_filtered_excel', methods=['POST'])
@login_required
@admin_required
def export_filtered_excel():
    # 复杂导出逻辑，直接复制 app.py 对应函数体
    data = request.json
    country = data.get('country', '')
    training_name = data.get('training_name', '')
    exam_name = data.get('exam_name', '')
    start_date = data.get('start_date', '')
    end_date = data.get('end_date', '')
    wh_raw = data.get('wh_id', '').strip()
    wh_id = wh_raw.split('(')[0].strip() if wh_raw else None

    db = get_supabase()
    
    # ✅ 新增：获取当前管理员的权限范围
    allowed_countries = get_admin_allowed_countries()
    
    # ✅ 新增：如果管理员有权限限制，且没有指定国家，则使用权限范围
    if allowed_countries is not None and not country:
        # 如果管理员权限范围不为空，用于过滤用户
        final_user_ids = None
        # 后续查询时需要按权限范围过滤
    else:
        final_user_ids = None

    # 构建统一的候选用户ID列表
    user_ids_for_country = None
    user_ids_for_wh = None

    # 1. 如果指定了国家，获取该国用户ID
    if country:
        users_res = db.table("users").select("id").eq("country", country).execute()
        user_ids_for_country = [u['id'] for u in (users_res.data or [])]
        if not user_ids_for_country:
            # 没有该国用户，直接返回空文件
            wb = openpyxl.Workbook()
            wb.active.title = "空报告"
            buffer = BytesIO()
            wb.save(buffer)
            buffer.seek(0)
            return send_file(buffer, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                             as_attachment=True, download_name=f"空报告_{datetime.now().strftime('%Y%m%d%H%M%S')}.xlsx")

    # 2. 如果指定了库房，获取该库房下的所有用户ID
    if wh_id:
        users_in_wh = db.table("users").select("id").eq("wh_id", wh_id).execute()
        user_ids_for_wh = [u['id'] for u in (users_in_wh.data or [])]
        if not user_ids_for_wh:
            wb = openpyxl.Workbook()
            wb.active.title = "空报告"
            buffer = BytesIO()
            wb.save(buffer)
            buffer.seek(0)
            return send_file(buffer, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                             as_attachment=True, download_name=f"空报告_{datetime.now().strftime('%Y%m%d%H%M%S')}.xlsx")

    # 3. 合并国家与库房的用户范围
    if user_ids_for_country is not None and user_ids_for_wh is not None:
        final_user_ids = list(set(user_ids_for_country) & set(user_ids_for_wh))
    elif user_ids_for_country is not None:
        final_user_ids = user_ids_for_country
    elif user_ids_for_wh is not None:
        final_user_ids = user_ids_for_wh
    else:
        final_user_ids = None
    
    # ✅ 新增：如果管理员有权限范围，进一步过滤用户
    if allowed_countries is not None and final_user_ids is not None:
        # 获取这些用户中在权限范围内的
        users_in_allowed = db.table("users").select("id").in_("id", final_user_ids).in_("country", allowed_countries).execute()
        final_user_ids = [u['id'] for u in (users_in_allowed.data or [])]
    elif allowed_countries is not None:
        # 没有指定其他筛选条件，使用权限范围内的所有用户
        users_in_allowed = db.table("users").select("id").in_("country", allowed_countries).execute()
        final_user_ids = [u['id'] for u in (users_in_allowed.data or [])]

    # 4. 查询培训（需要根据权限范围过滤）
    # ✅ 新增：培训查询也需要根据管理员权限过滤
    if allowed_countries is not None:
        # 查询权限范围内的培训（培训的 countries 字段包含允许的国家）
        # 由于 countries 是 JSON 数组，需要使用特定查询语法
        training_query = db.table("trainings").select("*")
        # 简化处理：查询所有培训，后续在 Python 中过滤
    else:
        training_query = db.table("trainings").select("*")
    
    if country:
        training_query = training_query.eq("country", country)
    if training_name:
        training_query = training_query.ilike("name", f"%{training_name}%")
    if start_date:
        training_query = training_query.gte("start_time", start_date)
    if end_date:
        training_query = training_query.lte("end_time", end_date)
    
    trainings = training_query.execute().data or []
    
    # ✅ 新增：根据管理员权限过滤培训
    if allowed_countries is not None:
        filtered_trainings = []
        for t in trainings:
            training_country = t.get('country') or t.get('countries')
            if training_country:
                # 解析国家列表
                country_list = []
                if isinstance(training_country, str):
                    try:
                        parsed = json.loads(training_country)
                        country_list = parsed if isinstance(parsed, list) else [training_country]
                    except:
                        country_list = [training_country]
                elif isinstance(training_country, list):
                    country_list = training_country
                else:
                    country_list = [str(training_country)]
                
                # 检查是否有交集
                if any(c in allowed_countries for c in country_list):
                    filtered_trainings.append(t)
        trainings = filtered_trainings

    # 5. 查询考试（类似逻辑）
    if allowed_countries is not None:
        exam_query = db.table("exams").select("*")
    else:
        exam_query = db.table("exams").select("*")
    
    if country:
        exam_query = exam_query.eq("country", country)
    if exam_name:
        exam_query = exam_query.ilike("title", f"%{exam_name}%")
    
    exams = exam_query.execute().data or []
    
    # 根据管理员权限过滤考试
    if allowed_countries is not None:
        filtered_exams = []
        for e in exams:
            exam_country = e.get('country') or e.get('countries')
            if exam_country:
                country_list = []
                if isinstance(exam_country, str):
                    try:
                        parsed = json.loads(exam_country)
                        country_list = parsed if isinstance(parsed, list) else [exam_country]
                    except:
                        country_list = [exam_country]
                elif isinstance(exam_country, list):
                    country_list = exam_country
                else:
                    country_list = [str(exam_country)]
                
                if any(c in allowed_countries for c in country_list):
                    filtered_exams.append(e)
        exams = filtered_exams

    # 6. 调用生成函数
    try:
        buffer, filename = export.generate_bilingual_excel_filtered(
            trainings=trainings,
            exams=exams,
            country=country,
            start_date=start_date,
            end_date=end_date,
            user_ids=final_user_ids,
            wh_id=wh_id
        )
        return send_file(buffer, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                         as_attachment=True, download_name=filename)
    except Exception as e:
        logger.error(f"Excel 生成失败: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500

@admin_export_bp.route('/admin/export_pdf_by_result/<int:result_id>')
@login_required
@admin_required
def admin_export_pdf_by_result(result_id):
    db = get_supabase()
    result = db.table("exam_results").select("*").eq("id", result_id).execute().data[0] if db.table("exam_results").select("*").eq("id", result_id).execute().data else None
    if not result: return redirect(request.referrer)
    user_data = db.table("users").select("*").eq("id", result['user_id']).execute().data[0] if db.table("users").select("*").eq("id", result['user_id']).execute().data else {}
    exam_data = db.table("exams").select("*").eq("id", result['exam_id']).execute().data[0] if db.table("exams").select("*").eq("id", result['exam_id']).execute().data else {}
    questions = db.table("questions").select("*").eq("exam_id", result['exam_id']).order("num").execute().data or []
    reviewer = get_reviewer_by_country(user_data.get('country'), exam_data.get('reviewer'), request.args.get('reviewer'))
    try:
        pdf = export.generate_user_pdf(user_data.get('name_cn') or user_data.get('name_en', '未知考生'), user_data.get('email', ''), exam_data.get('title', '考试'), result.get('total_score', 0), questions, robust_parse_json(result.get('answers')), robust_parse_json(result.get('details')), result.get('created_at', ''), reviewer)
        return send_file(pdf, mimetype="application/pdf", as_attachment=True, download_name=f"Transcript_{user_data.get('name_en', 'exam')}.pdf")
    except Exception as e:
        logger.error(f"PDF 失败: {e}"); return redirect(request.referrer)

def generate_pdf_by_result_id(result_id):
    db = get_supabase()
    result = db.table("exam_results").select("*").eq("id", result_id).execute().data[0]
    user = db.table("users").select("*").eq("id", result['user_id']).execute().data[0]
    exam = db.table("exams").select("*").eq("id", result['exam_id']).execute().data[0]
    questions = db.table("questions").select("*").eq("exam_id", result['exam_id']).order("num").execute().data or []
    reviewer = get_reviewer_by_country(user.get('country'), exam.get('reviewer'), None)
    return export.generate_user_pdf(user.get('name_cn') or user.get('name_en', '未知'), user.get('email', ''), exam.get('title', ''), result.get('total_score', 0), questions, robust_parse_json(result.get('answers')), robust_parse_json(result.get('details')), result.get('created_at', ''), reviewer)

@admin_export_bp.route('/api/admin/exam/batch_export_by_result', methods=['POST'])
@login_required
@admin_required
def admin_batch_export_by_result():
    data = request.json
    ids = data.get('result_ids', [])
    if not ids: return jsonify({"error": "未选择"}), 400
    buf = BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for rid in ids:
            try:
                pdf = generate_pdf_by_result_id(rid)
                if pdf:
                    name = db.table("users").select("name_en").eq("id", db.table("exam_results").select("user_id").eq("id", rid).execute().data[0]['user_id']).execute().data[0].get('name_en', 'user')
                    zf.writestr(f"{name}_{rid}.pdf", pdf.getvalue())
            except: pass
    buf.seek(0)
    return send_file(buf, mimetype='application/zip', as_attachment=True, download_name="export.zip")

@admin_export_bp.route('/admin/export_pdf/<int:exam_id>/<user_id>')
@login_required
@admin_required
def admin_export_user_pdf(exam_id, user_id):
    """管理员导出指定考生的成绩单 PDF, 好像未使用"""
    db = get_supabase()
    
    # 获取考试信息
    exam_res = db.table("exams").select("*").eq("id", exam_id).execute()
    if not exam_res.data:
        #flash("考试不存在", "danger")
        flash({'msg': 'exam_not_found', 'params': []}, 'danger')
        return redirect(url_for('admin_dashboard'))
    exam_data = exam_res.data[0]
    
    # 获取考生信息
    user_res = db.table("users").select("*").eq("id", user_id).execute()
    if not user_res.data:
        #flash("考生不存在", "danger")
        flash({'msg': 'student_not_found', 'params': []}, 'danger')
        return redirect(url_for('admin_dashboard'))
    user_data = user_res.data[0]
    
    # 获取成绩记录（不使用 maybe_single，避免 204 异常）
    result_res = db.table("exam_results").select("*").eq("exam_id", exam_id).eq("user_id", user_id).execute()
    if not result_res.data:
        #flash("该考生尚无成绩记录", "warning")
        flash({'msg': 'no_score_record', 'params': []}, 'warning')
        return redirect(url_for('admin_dashboard'))
    result = result_res.data[0]

    raw_answers = result.get('answers')
    logger.info(f"原始 answers 类型: {type(raw_answers)}, 值前200: {str(raw_answers)[:200]}")

    raw_details = result.get('details')
    logger.info(f"原始 details 类型: {type(raw_details)}, 值前200: {str(raw_details)[:200]}")

    answers = robust_parse_json(result.get('answers'), "answers")
    details = robust_parse_json(result.get('details'), "details")

    logger.info(f"解析后 answers 类型: {type(answers)}, 键示例: {list(answers.keys())[:5] if isinstance(answers, dict) else 'not dict'}")
    logger.info(f"解析后 details 类型: {type(details)}, 键示例: {list(details.keys())[:5] if isinstance(details, dict) else 'not dict'}")

    # 在获取成绩记录后，如果 answers 为空，则尝试从 user_exam_drafts 表读取草稿答案
    if not answers:
        draft_res = db.table("user_exam_drafts").select("answers").eq("user_id", user_id).eq("exam_id", exam_id).execute()
        if draft_res.data:
            draft_answers = draft_res.data[0].get('answers')
            if draft_answers:
                if isinstance(draft_answers, str):
                    try:
                        answers = json.loads(draft_answers)
                        logger.info(f"从草稿表恢复了答案，共 {len(answers)} 条")
                    except:
                        pass
                else:
                    answers = draft_answers

    logger.info(f"解析后 answers 类型: {type(answers)}, 键示例: {list(answers.keys())[:3]}")
    logger.info(f"解析后 details 类型: {type(details)}, 键示例: {list(details.keys())[:3]}")

    # 获取题目列表（用于展示题干和标准答案）
    questions_res = db.table("questions").select("*").eq("exam_id", exam_id).order("num").execute()
    questions = questions_res.data or []
    
    # ---------- 获取阅卷人（多级优先级）----------
    reviewer = get_reviewer_by_country(
        user_country=user_data.get('country'),
        exam_reviewer=exam_data.get('reviewer'),
        url_reviewer=request.args.get('reviewer')
    )


    # ✅ 添加调试日志
    logger.info(f"========== 阅卷人调试 ==========")
    logger.info(f"考生国家: {user_data.get('country')}")
    logger.info(f"考试表 reviewer: {exam_data.get('reviewer')}")
    logger.info(f"URL reviewer: {request.args.get('reviewer')}")
    logger.info(f"最终 reviewer: {reviewer}")
    logger.info(f"================================")

    # 生成 PDF（捕获异常）
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
        logger.error(f"PDF 生成失败: {e}")
        #flash(f"PDF 生成失败: {str(e)}", "danger")
        flash({'msg': 'pdf_generation_error', 'params': []}, 'danger')
        return redirect(url_for('admin_dashboard'))
    
    filename = f"Transcript_{user_name}_{exam_data.get('title', 'exam')}.pdf"
    return send_file(
        pdf_buffer,
        mimetype="application/pdf",
        as_attachment=True,
        download_name=filename
    )
