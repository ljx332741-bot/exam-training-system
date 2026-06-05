# routes/admin_exam.py
import os
import json
import logging
from datetime import datetime, timezone, timedelta, date
from flask import (
    current_app as app, 
    Flask, request, jsonify, redirect, url_for, render_template, session, flash, send_file, make_response
)
from . import admin_exam_bp
from services.db import get_supabase
from routes.helpers import login_required, admin_required, parse_exam_countries, get_exam_countries_display, can_access_exam
from services import auth, exam, export
from config import Config
from utils.status import get_exam_status
from utils.common import match_country_code, quarter_to_date_range, get_reviewer_by_country, utc_to_local, format_datetime_local
from utils.email_notifier import send_bilingual_notification, EmailScenario, _format_time
from utils.permissions import (
    is_developer, get_admin_allowed_countries, set_admin_allowed_countries, 
    developer_required, get_allowed_countries, apply_country_filter, has_role
)
from .admin_stats import (
    get_user_stats,
    get_exam_stats,
    get_training_stats,
    get_interview_stats,
    get_questions_stats,
    get_exams_for_display,
    get_sign_in_status
)

from utils.timezone_utils import get_user_timezone, utc_string_to_local, format_datetime

logger = logging.getLogger(__name__)

@admin_exam_bp.route('/api/admin/current_user_permissions')
@login_required
@admin_required
def api_admin_current_user_permissions():
    """获取当前用户的权限信息"""
    return jsonify({
        "role": session.get('role', 'user'),
        "user_id": session.get('user_id'),
        "is_developer": is_developer(),
        "is_super_admin": session.get('role') == 'super_admin' or is_developer(),
        "allowed_countries": get_allowed_countries()
    })

@admin_exam_bp.route('/admin/dashboard')
@login_required
@admin_required
def admin_dashboard():
    """管理员仪表盘 - 重构版"""
    # ========== 1. 获取基础信息 ==========
    allowed_countries = get_admin_allowed_countries()
    is_dev = is_developer()
    db = get_supabase()
    # ========== 2. 用户统计 ==========
    registered_count, imported_count = get_user_stats(allowed_countries)
    
    # ========== 3. 考试统计 ==========
    # ✅ 正确获取 get_exam_stats 返回的各个值
    (exams_total_from_stats, exams_completed_from_stats, exam_stats_from_stats, 
    filtered_exams, allowed_user_ids, allowed_exam_ids) = get_exam_stats(allowed_countries)
    
    logger.info(f"filtered_exams 数量: {len(filtered_exams)}")
    for exam in filtered_exams:
        logger.info(f"  - id={exam.get('id')}, title={exam.get('title')}, countries={exam.get('countries')}")
    
    # 注意：filtered_exams 已经是权限过滤后的考试列表
    exam_stats = {'draft': 0, 'created': 0, 'active': 0, 'closed': 0}
    
    for exam in filtered_exams:
        status = get_exam_status(exam)
        if status in exam_stats:
            exam_stats[status] += 1
        logger.debug(f"考试 {exam.get('id')}: 状态={status}")
    
    exams_total = len(filtered_exams)
    # ✅ 已完成 = 已关闭的考试数量（考试状态为 closed）
    exams_completed = exam_stats.get('closed', 0)
    exams_active = exam_stats.get('active', 0)
    exams_other = exam_stats.get('draft', 0) + exam_stats.get('created', 0)
    
    logger.info(f"考试统计(基于考试状态): 总数={exams_total}, "
                f"已完成(closed)={exams_completed}, "
                f"进行中(active)={exams_active}, "
                f"其它(draft+created)={exams_other}")
    
    # ========== 4. 培训统计 ==========
    trainings_count, total_attendances, signins_today = get_training_stats(
        allowed_countries, allowed_user_ids
    )
    
    # ========== 5. 访谈统计 ==========
    interviewee_count = get_interview_stats(allowed_countries)
    
    # ========== 6. 题库统计 ==========
    questions_count = get_questions_stats(allowed_countries, filtered_exams)
    
    # ========== 7. 考试列表（表格和下拉框）==========
    exams_for_table, exams_for_selector = get_exams_for_display(
        filtered_exams, allowed_countries, allowed_user_ids
    )
    
    # ========== 8. 培训签到开关 ==========
    sign_in_open = get_sign_in_status()
    
    # ========== 9. 组装统计数据 ==========
    stats = {
        "users": registered_count,
        "users_imported": imported_count,
        "exams_total": exams_total,
        "exams_completed": exams_completed,
        "exam_draft": exam_stats.get('draft', 0),
        "exam_active": exam_stats.get('active', 0),
        "exam_closed": exam_stats.get('closed', 0),
        "trainings_count": trainings_count,
        "total_attendances": total_attendances,
        "signins_today": signins_today,
        "questions": questions_count
    }
    
    logger.info(f"最终统计: {stats}")
    
    # ========== 10. 渲染模板 ==========
    return render_template(
        'admin/dashboard.html',
        signs=[],
        exams_table=exams_for_table,
        exams_selector=exams_for_selector,
        stats=stats,
        sign_in_open=sign_in_open,
        questions_count=questions_count,
        signins_today=signins_today,
        total_attendances=total_attendances,
        trainings_count=trainings_count,
        interviewee_count=interviewee_count
    )

@admin_exam_bp.route('/admin/exams')
@login_required
@admin_required
def admin_exams_page():
    """考试清单页面（一级菜单）"""
    db = get_supabase()
    exams = db.table("exams").select("*").execute().data or []
    
    # 获取当前管理员的权限范围
    allowed = get_allowed_countries()
    
    # 确保 allowed 是列表格式
    if isinstance(allowed, str):
        try:
            allowed = json.loads(allowed)
        except:
            allowed = None
    elif allowed is None:
        # 超管，无限制
        pass
    
    for exam in exams:
        # 使用辅助函数获取国家显示
        exam['countries_display'] = get_exam_countries_display(exam, allowed)
        
        # 可选：也存储原始国家列表供其他用途
        exam['countries'] = parse_exam_countries(exam)
    
    return render_template('admin/list_exams.html', exams=exams)

@admin_exam_bp.route('/admin/exam/edit/<int:exam_id>')
@login_required
@admin_required
def edit_exam_preview(exam_id):
    db = get_supabase()
    exam = db.table("exams").select("*").eq("id", exam_id).maybe_single().execute().data
    if not exam: flash("考试不存在", "danger"); return redirect(url_for('exam.dashboard'))
    status = get_exam_status(exam)
    if status == 'closed': flash("已关闭的考试不能编辑", "warning"); return redirect(url_for('admin_exams_page'))
    questions = db.table("questions").select("*").eq("exam_id", exam_id).order("num").execute().data or []
    for q in questions:
        if isinstance(q.get('options'), str):
            try: q['options'] = json.loads(q['options'])
            except: q['options'] = {}
    return render_template('admin/import_preview.html', questions=questions, exam_title=exam['title'], edit_mode=True, original_exam_id=exam_id, return_url=url_for('exam.dashboard'), exam_country=exam.get('country', ''), exam_country_name='', exam_status=status, can_edit_questions=status in ['draft', 'created'], exam_duration=exam.get('duration', 60), exam_reviewer=exam.get('reviewer', ''))

@admin_exam_bp.route('/admin/import', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_import():
    """Word 题库导入页面"""
    print(f"\n🔥🔥🔥 admin_import 被调用！method={request.method} 🔥🔥🔥\n", flush=True)
    
    if request.method == 'POST' and 'docx_file' in request.files:
        file = request.files['docx_file']
        logger.info(f"📄 收到文件: {file.filename}, size={file.content_length}")
        
        if not file.filename.endswith('.docx'):
            logger.warning(f"❌ 文件格式错误: {file.filename}")
            #flash('❌ 仅支持 .docx 格式', 'danger')
            flash({'msg': 'only_docx', 'params': []}, 'danger')
            return redirect(request.url)
        
        import tempfile, os
        tmp_path = None
        
        try:
            # Windows 兼容的临时文件处理
            with tempfile.NamedTemporaryFile(
                delete=False, suffix='.docx', dir=os.getenv('TEMP', '/tmp')
            ) as tmp:
                file.save(tmp.name)
                tmp_path = tmp.name
            logger.info(f"💾 临时文件已保存: {tmp_path}")
            
            # 调用双语解析函数
            exam_title, qs = exam.parse_docx_bilingual(tmp_path, exam_id=0)  # 不再需要 exam_id 参数
            # 如果解析出的标题无效（空或默认值），则使用文件名
            if not exam_title or exam_title == '未命名考试':
                exam_title = os.path.splitext(os.path.basename(file.filename))[0]
                logger.info(f"使用文件名作为考试标题: {exam_title}")
            logger.info(f"✅ 解析成功: 返回 {len(qs)} 道题目")
            
            if not qs:
                logger.warning("⚠️ 解析结果为空")
                #flash('⚠️ 未识别到有效题目，请检查 Word 格式', 'warning')
                flash({'msg': 'no_valid_question', 'params': []}, 'warning')
                return render_template('admin/import.html')
            
            logger.info("🔄 跳转预览页")
            return render_template('admin/import_preview.html', questions=qs, exam_title=exam_title)
            
        except AttributeError as e:
            logger.error(f"❌ AttributeError: {e}")
            logger.error(f"💡 请确认 services/exam.py 中存在 parse_docx_bilingual 函数")
            #flash('❌ 系统错误: 解析函数未找到', 'danger')
            flash({'msg': 'parse_func_missing', 'params': []}, 'danger')
        except FileNotFoundError as e:
            logger.error(f"❌ 文件未找到: {e}")
            #flash('❌ 临时文件创建失败', 'danger')
            flash({'msg': 'temp_file_failed', 'params': []}, 'danger')
        except PermissionError as e:
            logger.error(f"❌ 权限错误: {e}")
            #flash('❌ 文件访问权限不足', 'danger')
            flash({'msg': 'file_permission_denied', 'params': []}, 'danger')
        except Exception as e:
            logger.error(f"❌ 未知异常: {type(e).__name__}: {e}")
            logger.error(f"📋 完整堆栈:\n{traceback.format_exc()}")
            #flash(f'❌ 解析失败: {str(e)}', 'danger')
            flash({'msg': 'parse_error', 'params': [str(e)]}, 'danger')          # 带参数
        finally:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                    logger.debug(f"🗑️ 已清理临时文件: {tmp_path}")
                except Exception:
                    pass
        return redirect(request.url)
    
    # GET 请求：渲染上传表单
    logger.info("📄 渲染 import.html")
    return render_template('admin/import.html')

@admin_exam_bp.route('/admin/import/save', methods=['POST'])
@login_required
@admin_required
def admin_import_save():
    logger.info(f"/admin/import/save 收到请求，questions 长度: {len(request.json.get('questions', []))}")
    data = request.json.get('questions', [])
    if not data:
        return jsonify({"success": False, "message": "无数据"})

    exam_title = request.args.get('title', '未命名考试')
    is_draft = request.args.get('draft', 'false').lower() == 'true'
    db = get_supabase()
    
    # ✅ 修改：支持多国家，接收 countries 数组
    countries = request.json.get('countries', [])  # 数组格式 ["NP", "LK", "BD"]
    country_code = request.json.get('country_code', '')  # 兼容旧版单国家
    
    # 如果没有 countries 但有 country_code，转换为数组
    if not countries and country_code:
        countries = [country_code]
    
    # 权限检查
    current_user_id = session.get('user_id')
    current_role = session.get('role')
    
    user_res = db.table("users").select("admin_countries, country").eq("id", current_user_id).maybe_single().execute()
    user_data = user_res.data if user_res and user_res.data else {}
    
    allowed = None
    if current_role == 'super_admin':
        admin_countries = user_data.get('admin_countries')
        if admin_countries:
            try:
                allowed = json.loads(admin_countries) if isinstance(admin_countries, str) else admin_countries
            except:
                allowed = None
    elif current_role == 'admin':
        admin_countries = user_data.get('admin_countries')
        if admin_countries:
            try:
                allowed = json.loads(admin_countries) if isinstance(admin_countries, str) else admin_countries
            except:
                allowed = None
        
        if not allowed:
            user_country = user_data.get('country')
            if user_country:
                allowed = [user_country]
            else:
                allowed = []
    
    # 权限检查：管理员创建的所有国家必须在允许范围内
    if current_role == 'admin':
        if not allowed or len(allowed) == 0:
            return jsonify({"success": False, "message": "jsonify_no_country_permission", "params": []}), 403
        
        if not countries:
            return jsonify({"success": False, "message": "请至少选择一个国家"}), 400
        
        for c in countries:
            if c not in allowed:
                return jsonify({"success": False, "message": f"无权创建国家 {c} 的考试"}), 403

    try:
        # 创建考试记录
        exam_insert = db.table("exams").insert({
            "title": exam_title,
            "is_active": not is_draft,
            "countries": json.dumps(countries),  # ✅ 存储为 JSON 字符串
            "status": "draft" if is_draft else "active"
        }).execute()
        
        if not exam_insert.data:
            raise Exception("创建考试失败")
        new_exam_id = exam_insert.data[0]['id']
        
        # 插入题目
        for q in data:
            q['exam_id'] = new_exam_id
            q['options'] = json.dumps(q.get('options', {}))

        res = db.table("questions").insert(data).execute()
        logger.info(f"✅ 成功创建考试「{exam_title}」ID={new_exam_id}，插入 {len(res.data)} 道题目，国家: {countries}")
        return jsonify({"success": True, "exam_id": new_exam_id})
    except Exception as e:
        logger.error(f"❌ 导入保存失败: {e}")
        return jsonify({"success": False, "message": str(e)}), 500

@admin_exam_bp.route('/admin/result/<int:result_id>')
@login_required
@admin_required
def admin_result_detail(result_id):
    """管理查看考生考试详情"""
    db = get_supabase()
    # 1. 获取成绩记录
    result_res = db.table("exam_results").select("*").eq("id", result_id).maybe_single().execute()
    if not result_res.data:
        #flash("成绩记录不存在", "danger")
        flash({'msg': 'result_not_found', 'params': []}, 'danger')
        return redirect(url_for('admin_dashboard'))
    
    result = result_res.data
    exam_id = result['exam_id']
    user_id = result['user_id']

    # 2. 获取用户信息
    user_res = db.table("users").select("email, name_en").eq("id", user_id).maybe_single().execute()
    user_info = user_res.data if user_res.data else {"email": "未知", "name_en": "未知"}
    
    # 3. 获取考试信息
    exam_res = db.table("exams").select("title").eq("id", exam_id).maybe_single().execute()
    exam_title = exam_res.data.get("title", "未知考试") if exam_res.data else "未知考试"
    
    # 将关联信息附加到 result 对象（模板中会使用 result.users.email 等形式）
    result['users'] = user_info
    result['exams'] = {"title": exam_title}
    
    # 4. 获取题目列表
    questions = db.table("questions").select("*").eq("exam_id", exam_id).order("num").execute()
    
    # 5. 解析 JSON 字段（answers 和 details）
    answers = result.get('answers', {})
    if isinstance(answers, str):
        answers = json.loads(answers)
    details = result.get('details', {})
    if isinstance(details, str):
        details = json.loads(details)

    # ✅ 格式化时间
    result['created_at_local'] = format_datetime(result.get('created_at'))
    result['submitted_at_local'] = format_datetime_local(result.get('submitted_at'))
    
    return render_template(
        'admin/result_detail.html',
        result=result,
        questions=questions.data or [],
        answers=answers,
        details=details
    )

'''
@admin_exam_bp.route('/admin/reset_exam/<int:exam_id>/<user_id>', methods=['POST'])
@login_required
@admin_required
def admin_reset_exam(exam_id, user_id):
    db = get_supabase()
    reset_at = datetime.now(timezone.utc).isoformat()
    existing = db.table("user_exam_status").select("id").eq("user_id", user_id).eq("exam_id", exam_id).maybe_single().execute()
    if existing.data: db.table("user_exam_status").update({"is_submitted": False, "reset_at": reset_at, "started_at": None, "submitted_at": None}).eq("id", existing.data['id']).execute()
    else: db.table("user_exam_status").insert({"user_id": user_id, "exam_id": int(exam_id), "is_submitted": False, "reset_at": reset_at}).execute()
    db.table("user_exam_drafts").delete().eq("user_id", user_id).eq("exam_id", int(exam_id)).execute()
    return jsonify({"success": True, "reset_token": reset_at})
'''

# routes/admin_exam.py - admin_reset_exam 函数

@admin_exam_bp.route('/admin/reset_exam/<int:exam_id>/<user_id>', methods=['POST'])
@login_required
@admin_required
def admin_reset_exam(exam_id, user_id):
    db = get_supabase()
    reset_at = datetime.now(timezone.utc).isoformat()
    
    # ✅ 重置考试状态，但不删除草稿
    existing = db.table("user_exam_status").select("id").eq("user_id", user_id).eq("exam_id", exam_id).maybe_single().execute()
    if existing.data:
        db.table("user_exam_status").update({
            "is_submitted": False, 
            "reset_at": reset_at, 
            "started_at": None, 
            "submitted_at": None
        }).eq("id", existing.data['id']).execute()
    else:
        db.table("user_exam_status").insert({
            "user_id": user_id, 
            "exam_id": int(exam_id), 
            "is_submitted": False, 
            "reset_at": reset_at
        }).execute()
        
    db.table("user_exam_drafts").delete().eq("user_id", user_id).eq("exam_id", int(exam_id)).execute()
    logger.info(f"重置考试状态，保留草稿: user={user_id}, exam={exam_id}")
    
    return jsonify({"success": True, "reset_token": reset_at})

@admin_exam_bp.route('/admin/exam/delete/<int:exam_id>', methods=['POST'])
@login_required
@admin_required
def admin_delete_exam(exam_id):
    """支持软删除和永久删除"""
    permanent = request.args.get('permanent', 'false').lower() == 'true'
    db = get_supabase()
    
    referer = request.referrer or url_for('admin_exams_page')
    
    logger.info("=" * 60)
    logger.info(f"删除考试请求 - exam_id: {exam_id}")
    logger.info(f"permanent: {permanent}")
    
    try:
        # 检查考试是否存在
        exam_res = db.table("exams").select("*").eq("id", exam_id).maybe_single().execute()
        if not exam_res.data:
            flash(f"考试 ID {exam_id} 不存在", "warning")
            return redirect(referer)
        
        exam_title = exam_res.data.get('title', f'ID {exam_id}')
        
        if permanent:
            logger.info(f"开始永久删除考试 {exam_id}")
            
            # 按顺序删除关联数据
            tables_to_delete = [
                ("questions", "题目"),
                ("exam_assignments", "考试分配"),
                ("exam_results", "考试成绩"),
                ("user_exam_status", "考试状态"),
                ("user_exam_drafts", "考试草稿"),
            ]
            
            for table_name, cn_name in tables_to_delete:
                try:
                    result = db.table(table_name).delete().eq("exam_id", exam_id).execute()
                    # 检查删除结果
                    deleted_count = 0
                    if hasattr(result, 'count'):
                        deleted_count = result.count
                    elif result.data:
                        deleted_count = len(result.data)
                    logger.info(f"已删除 {cn_name} ({table_name}): {deleted_count} 条")
                except Exception as e:
                    logger.warning(f"删除 {cn_name} 时出错: {e}")
            
            # ✅ 关键：删除考试本身，并检查结果
            delete_result = db.table("exams").delete().eq("id", exam_id).execute()
            logger.info(f"删除考试原始结果: {delete_result}")
            
            # 检查是否真的删除了
            deleted_success = False
            if hasattr(delete_result, 'data') and delete_result.data:
                deleted_success = len(delete_result.data) > 0
            elif hasattr(delete_result, 'count'):
                deleted_success = delete_result.count > 0
            
            if deleted_success:
                flash(f"考试「{exam_title}」已永久删除", "success")
                logger.info(f"✅ 考试 {exam_id} 永久删除成功")
            else:
                # 尝试使用 raw SQL 删除
                logger.warning("Supabase API 删除失败，尝试使用 raw SQL")
                try:
                    from services.db import get_supabase_raw
                    raw_result = get_supabase_raw().table("exams").delete().eq("id", exam_id).execute()
                    logger.info(f"Raw SQL 删除结果: {raw_result}")
                    flash(f"考试「{exam_title}」已永久删除", "success")
                except Exception as raw_e:
                    logger.error(f"Raw SQL 删除也失败: {raw_e}")
                    flash(f"考试「{exam_title}」删除失败，请检查数据库权限", "danger")
        else:
            # 软删除
            now_utc = datetime.now(timezone.utc).isoformat()
            update_result = db.table("exams").update({
                "deleted_at": now_utc
            }).eq("id", exam_id).execute()
            
            if update_result.data:
                flash(f"考试「{exam_title}」已软删除", "info")
                logger.info(f"考试 {exam_id} 软删除成功")
            else:
                flash(f"考试「{exam_title}」软删除失败", "danger")
        
        return redirect(referer)
        
    except Exception as e:
        logger.error(f"❌ 删除考试失败: {type(e).__name__}: {e}", exc_info=True)
        flash(f"删除失败: {str(e)}", "danger")
        return redirect(referer)

@admin_exam_bp.route('/admin/exam/restore/<int:exam_id>', methods=['POST'])
@login_required
@admin_required
def restore_exam(exam_id):
    db = get_supabase()
    db.table("exams").update({"deleted_at": None}).eq("id", exam_id).execute()
    return redirect(url_for('admin_exams_page'))

@admin_exam_bp.route('/api/admin/exam/<int:exam_id>/copy', methods=['POST'])
@login_required
@admin_required
def copy_exam(exam_id):
    # 复制逻辑
    data = request.json
    new_title = data.get('new_title')
    if not new_title:
        return jsonify({"success": False, "message": "新考试名称不能为空"}), 400

    db = get_supabase()

    # 获取原考试信息
    exam_res = db.table("exams").select("*").eq("id", exam_id).maybe_single().execute()
    if not exam_res.data:
        return jsonify({"success": False, "message": "原考试不存在"}), 404
    exam = exam_res.data
    
    # ✅ 获取原考试的国家列表
    original_countries = exam.get('countries', [])
    if isinstance(original_countries, str):
        try:
            original_countries = json.loads(original_countries)
        except:
            original_countries = [exam.get('country', '')] if exam.get('country') else []
    
    # 权限检查
    current_user_id = session.get('user_id')
    current_role = session.get('role')
    
    user_res = db.table("users").select("admin_countries, country").eq("id", current_user_id).maybe_single().execute()
    user_data = user_res.data if user_res and user_res.data else {}
    
    allowed = None
    if current_role == 'super_admin':
        admin_countries = user_data.get('admin_countries')
        if admin_countries:
            try:
                allowed = json.loads(admin_countries) if isinstance(admin_countries, str) else admin_countries
            except:
                allowed = None
    elif current_role == 'admin':
        admin_countries = user_data.get('admin_countries')
        if admin_countries:
            try:
                allowed = json.loads(admin_countries) if isinstance(admin_countries, str) else admin_countries
            except:
                allowed = None
        
        if not allowed:
            user_country = user_data.get('country')
            if user_country:
                allowed = [user_country]
            else:
                allowed = []
    
    # 权限检查：拷贝的考试国家必须在允许范围内
    if current_role == 'admin':
        if not allowed or len(allowed) == 0:
            return jsonify({"success": False, "message": "jsonify_no_country_permission", "params": []}), 403
        
        for c in original_countries:
            if c not in allowed:
                return jsonify({"success": False, "message": f"无权拷贝国家 {c} 的考试"}), 403

    # 创建新考试
    new_exam_data = {
        "title": new_title,
        "duration": exam.get("duration", 60),
        "is_active": exam.get("is_active", False),
        "status": exam.get("status", "draft"),
        "start_time": exam.get("start_time"),
        "end_time": exam.get("end_time"),
        "quarter": exam.get("quarter"),
        "created_by": session['user_id'],
        "reviewer": exam.get("reviewer"),
        "countries": json.dumps(original_countries)  # ✅ 复制多国家
    }
    new_exam_data = {k: v for k, v in new_exam_data.items() if v is not None}
    insert_res = db.table("exams").insert(new_exam_data).execute()
    if not insert_res.data:
        return jsonify({"success": False, "message": "jsonify_exam_copy_failure", "params": []}), 500
    new_exam_id = insert_res.data[0]['id']

    # 复制题目
    questions_res = db.table("questions").select("*").eq("exam_id", exam_id).execute()
    if questions_res.data:
        new_questions = []
        for q in questions_res.data:
            new_q = {
                "exam_id": new_exam_id,
                "num": q.get("num"),
                "type": q.get("type"),
                "content": q.get("content"),
                "content_cn": q.get("content_cn"),
                "content_en": q.get("content_en"),
                "content_raw": q.get("content_raw"),
                "options": q.get("options"),
                "answer": q.get("answer"),
                "score": q.get("score")
            }
            new_questions.append(new_q)
        db.table("questions").insert(new_questions).execute()

    logger.info(f"✅ 复制考试成功: 原考试ID={exam_id}, 新考试ID={new_exam_id}, 国家={original_countries}")
    return jsonify({"success": True, "new_id": new_exam_id})


@admin_exam_bp.route('/admin/exam/copy_preview/<int:exam_id>')
@login_required
@admin_required
def copy_exam_preview(exam_id):
    """拷贝考试预览页（可编辑）"""
    db = get_supabase()
    # 获取原考试信息
    exam_res = db.table("exams").select("*").eq("id", exam_id).maybe_single().execute()
    if not exam_res.data:
        #flash("考试不存在", "danger")
        flash({'msg': 'exam_not_found', 'params': []}, 'danger')
        return redirect(url_for('admin_exams_page'))
    exam = exam_res.data
    # 获取原考试的所有题目
    questions_res = db.table("questions").select("*").eq("exam_id", exam_id).order("num").execute()
    questions = questions_res.data or []
    for q in questions:
        # 解析 options 字符串为字典
        if isinstance(q.get('options'), str):
            try:
                q['options'] = json.loads(q['options'])
            except:
                q['options'] = {}
    # 获取用户输入的新考试名称（从 query 参数）
    new_title = request.args.get('new_title', exam['title'] + '_copy')
    return render_template('admin/import_preview.html', 
                           questions=questions,
                           exam_title=new_title,
                           copy_mode=False,        # 拷贝是新建，非编辑
                           original_exam_id=exam_id,
                           return_url=url_for('admin_exam.admin_exams_page'), # 拷贝后返回的页面有疑问
                           exam_country=exam.get('country', ''),
                           exam_country_name=''   # 可选
                           ) 


@admin_exam_bp.route('/admin/exam/<int:exam_id>/update_full', methods=['PUT'])
@login_required
@admin_required
def update_exam_full(exam_id):
    """更新现有考试的信息和题目（支持部分更新）"""
    data = request.json
    db = get_supabase()

    # 获取原考试信息
    exam_res = db.table("exams").select("*").eq("id", exam_id).maybe_single().execute()
    if not exam_res.data:
        return jsonify({"success": False, "message": "jsonify_no_such_exam", "params": []}), 404
    
    original = exam_res.data
    current_status = get_exam_status(original)
    
    # 已关闭的考试不能编辑
    if current_status == 'closed':
        return jsonify({"success": False, "message": "jsonify_closed_exams_cannot_edited", "params": []}), 403
    
    # 构建更新数据
    update_data = {}
    
    # 基本信息（所有状态都可更新）
    if 'title' in data:
        update_data['title'] = data['title']
    if 'duration' in data:
        update_data['duration'] = data['duration']
    if 'reviewer' in data:
        update_data['reviewer'] = data['reviewer']
        logger.info(f"更新考试阅卷人: {data['reviewer']}")
    if 'country_code' in data:
        update_data['country'] = data['country_code']
    
    # 有效期（只有草稿和未开始状态可更新）
    if current_status in ['draft', 'created']:
        if 'start_time' in data:
            update_data['start_time'] = data['start_time']
        if 'end_time' in data:
            update_data['end_time'] = data['end_time']
    
    # 更新考试基本信息
    if update_data:
        db.table("exams").update(update_data).eq("id", exam_id).execute()
    
    # 题目更新（只有草稿和未开始状态，且前端要求更新时）
    can_update_questions = current_status in ['draft', 'created']
    should_update_questions = 'questions' in data and data['questions'] and not data.get('skip_questions', False)
    
    if can_update_questions and should_update_questions:
        questions = data.get('questions', [])
        if questions:
            # 删除原题目
            db.table("questions").delete().eq("exam_id", exam_id).execute()
            # 插入新题目
            for q in questions:
                q['exam_id'] = exam_id
                q['options'] = json.dumps(q.get('options', {}))
            db.table("questions").insert(questions).execute()
            logger.info(f"更新了 {len(questions)} 道题目")
    
    # 重新计算状态
    if update_data.get('start_time') and update_data.get('end_time'):
        now = datetime.now(timezone.utc)
        
        # ✅ 安全解析开始时间和结束时间
        start_str = update_data['start_time']
        end_str = update_data['end_time']
        
        try:
            start_dt = datetime.fromisoformat(start_str)
            end_dt = datetime.fromisoformat(end_str)
            
            # 确保带时区
            if start_dt.tzinfo is None:
                start_dt = start_dt.replace(tzinfo=timezone.utc)
            if end_dt.tzinfo is None:
                end_dt = end_dt.replace(tzinfo=timezone.utc)
            
            if now < start_dt:
                db.table("exams").update({"status": "created"}).eq("id", exam_id).execute()
            elif now > end_dt:
                db.table("exams").update({"status": "closed"}).eq("id", exam_id).execute()
            else:
                db.table("exams").update({"status": "active", "is_active": True}).eq("id", exam_id).execute()
        except ValueError as e:
            logger.error(f"时间格式解析错误: {e}, start={start_str}, end={end_str}")
    
    return jsonify({"success": True, "exam_id": exam_id})

@admin_exam_bp.route('/api/admin/exam/<int:exam_id>/settings', methods=['POST'])
@login_required
@admin_required
def admin_exam_settings(exam_id):
    """管理员仪表盘设置考试：有效期、时长、考生分配（清除原有分配，重新分配）"""
    data = request.json
    start_time = data.get('start_time')
    end_time = data.get('end_time')
    duration = data.get('duration', 60)
    user_ids = data.get('user_ids', [])
    db = get_supabase()
    
    update_data = {}
    if start_time:
        update_data['start_time'] = start_time
    if end_time:
        update_data['end_time'] = end_time
    if duration:
        update_data['duration'] = duration
    
    # 更新考试信息
    if update_data:
        db.table("exams").update(update_data).eq("id", exam_id).execute()
    
    # 更新考生分配（先删除旧关联，再插入新关联）
    db.table("exam_assignments").delete().eq("exam_id", exam_id).execute()
    for uid in user_ids:
        db.table("exam_assignments").insert({"exam_id": exam_id, "user_id": uid}).execute()
    
    # 发送邮件通知（如果有效期已设置且状态变为已创建/进行中）
    if start_time and end_time:
        # 获取考试标题
        exam_res = db.table("exams").select("title").eq("id", exam_id).execute()
        exam_title = exam_res.data[0]['title'] if exam_res.data else "考试"
        reviewer = exam_res.data[0].get('reviewer', '管理员') if exam_res.data else "管理员"

        for uid in user_ids:
            user_res = db.table("users").select("email, name_en").eq("id", uid).execute()
            if user_res.data:
                email = user_res.data[0]['email']
                try:
                    send_bilingual_notification(
                        email=email,
                        scenario=EmailScenario.EXAM_ASSIGNMENT,
                        params={
                            "name": user_res.data[0].get('name_en', '用户'),
                            "exam_title": exam_title,
                            "start_display": _format_time(start_time),
                            "end_display": _format_time(end_time),
                            "duration": str(duration),
                            "reviewer": reviewer,
                            "host_url": request.host_url,
                        },
                        host_url=request.host_url,
                        auth_module=auth
                    )
                except Exception as e:
                    logger.warning(f"发送邮件失败: {e}")
    
    return jsonify({"success": True})

    if start_time and end_time:
        now = datetime.now(timezone.utc)
        start_dt = datetime.fromisoformat(start_time)
        end_dt = datetime.fromisoformat(end_time)
        if now < start_dt:
            status = "created"
        elif now > end_dt:
            status = "closed"
        else:
            status = "active"
    else:
        status = "draft"
    update_data['status'] = status

@admin_exam_bp.route('/admin/exam/<int:exam_id>/duration', methods=['POST'])
@login_required
@admin_required
def update_exam_duration(exam_id):
    """后端 API 支持更新时长"""
    data = request.get_json()
    duration = data.get('duration')
    if not duration or not isinstance(duration, int) or duration <= 0:
        return jsonify({"success": False, "message": "无效的时长"}), 400
    db = get_supabase()
    try:
        db.table("exams").update({"duration": duration}).eq("id", exam_id).execute()
        logger.info(f"考试 {exam_id} 时长更新为 {duration} 分钟")
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

# routes/admin_exam.py - 修复 admin_exam_status 函数

@admin_exam_bp.route('/admin/exam_status/<int:exam_id>')
@login_required
@admin_required
def admin_exam_status(exam_id):
    db = get_supabase()
    current_role = session.get('role')
    is_dev = is_developer()
    allowed = get_allowed_countries()

    # ========== 1. 权限检查：是否有权查看此考试 ==========
    exam_res = db.table("exams").select("countries, country").eq("id", exam_id).maybe_single().execute()
    if not exam_res.data:
        return jsonify([])
    exam = exam_res.data
    
    # 解析考试的国家列表
    exam_countries = parse_exam_countries(exam)
    if not exam_countries and exam.get('country'):
        exam_countries = [exam.get('country')]
    
    # ✅ 关键修复：检查考试是否在管理员权限范围内
    if not is_dev:
        if current_role == 'super_admin':
            if allowed is not None and allowed:
                # 检查考试国家是否在允许范围内
                if not any(c in allowed for c in exam_countries):
                    logger.warning(f"考试 {exam_id} 不在超管权限范围内，拒绝访问")
                    return jsonify([])
        elif current_role == 'admin':
            if allowed:
                if not any(c in allowed for c in exam_countries):
                    logger.warning(f"考试 {exam_id} 不在管理员权限范围内，拒绝访问")
                    return jsonify([])
            else:
                # 无权限范围，使用用户注册国家
                user_country = session.get('user_country')
                if user_country not in exam_countries:
                    logger.warning(f"考试 {exam_id} 国家 {exam_countries} 不匹配用户国家 {user_country}，拒绝访问")
                    return jsonify([])
        else:
            return jsonify([])

    # ========== 2. 获取用户列表（带权限过滤）==========
    query = db.table("users").select("id, email, name_en, country").is_("deleted_at", "null")
    
    # 权限过滤
    if not is_dev:
        if current_role == 'super_admin':
            if allowed is not None and allowed:
                query = query.in_("country", allowed)
        elif current_role == 'admin':
            if allowed:
                query = query.in_("country", allowed)
            else:
                user_country = session.get('user_country')
                if user_country:
                    query = query.eq("country", user_country)
                else:
                    return jsonify([])
    
    users_res = query.execute()
    users = users_res.data or []

    # ========== 3. 获取考试分配关系 ==========
    assign_res = db.table("exam_assignments").select("user_id").eq("exam_id", exam_id).execute()
    assigned_user_ids = {a['user_id'] for a in (assign_res.data or [])}

    # ========== 4. 获取考试状态 ==========
    status_res = db.table("user_exam_status").select("user_id, started_at, is_submitted, submitted_at").eq("exam_id", exam_id).execute()
    status_dict = {}
    for s in (status_res.data or []):
        status_dict[s['user_id']] = s
    
    # ========== 5. 获取成绩记录 ==========
    results_res = db.table("exam_results").select("id, user_id, total_score").eq("exam_id", exam_id).execute()
    results_dict = {r['user_id']: {'result_id': r['id'], 'score': r['total_score']} for r in (results_res.data or [])}
    
    # ========== 6. 组装数据 ==========
    data = []
    for u in users:
        uid = u['id']
        
        # 判断考试状态
        if uid not in assigned_user_ids:
            exam_status = 'not_assigned'
        else:
            user_status = status_dict.get(uid, {})
            if user_status.get('is_submitted'):
                exam_status = 'submitted'
            elif user_status.get('started_at'):
                exam_status = 'in_progress'
            else:
                exam_status = 'pending'

        result_info = results_dict.get(uid, {})
        data.append({
            "user_id": uid,
            "email": u.get('email'),
            "name": u.get('name_en') or u.get('email'),
            "country": u.get('country', ''),
            "is_submitted": status_dict.get(uid, {}).get('is_submitted', False),
            "submitted_at": status_dict.get(uid, {}).get('submitted_at'),
            "score": result_info.get('score'),
            "result_id": result_info.get('result_id'),
            "exam_status": exam_status
        })
    
    return jsonify(data)

@admin_exam_bp.route('/api/admin/exam/<int:exam_id>')
@login_required
@admin_required
def api_admin_exam_detail(exam_id):
    """获取单个考试信息接口（用于模态框回显）"""
    db = get_supabase()

    # ✅ 1. 获取考试信息
    exam = db.table("exams").select("*").eq("id", exam_id).maybe_single().execute()
    if not exam.data:
        return jsonify({"error": "考试不存在"}), 404

    # ✅ 2. 权限检查
    if not can_access_exam(exam.data):
        return jsonify({"error": "无权访问此考试"}), 403
    
    return jsonify({
        "start_time": exam.data.get('start_time'),
        "end_time": exam.data.get('end_time'),
        "duration": exam.data.get('duration', 60)
    })

# routes/admin_exam.py

@admin_exam_bp.route('/api/admin/exam/<int:exam_id>/assignments')
@login_required
@admin_required
def api_admin_exam_assignments(exam_id):
    """获取考试已分配考生列表（带权限检查）"""
    db = get_supabase()
    
    # ✅ 1. 首先检查管理员是否有权查看这个考试
    exam_res = db.table("exams").select("countries, country").eq("id", exam_id).maybe_single().execute()
    if not exam_res.data:
        return jsonify({"user_ids": []})
    
    exam = exam_res.data
    
    # ✅ 使用权限检查函数
    if not can_access_exam(exam):
        return jsonify({"user_ids": []})
    
    # ✅ 2. 获取分配的用户ID，但需要过滤
    res = db.table("exam_assignments").select("user_id").eq("exam_id", exam_id).execute()
    user_ids = [row['user_id'] for row in (res.data or [])]
    
    # ✅ 3. 如果管理员有权限范围，只返回权限范围内的用户
    if not is_developer():
        allowed = get_admin_allowed_countries()
        if allowed is not None and allowed:
            # 只保留权限范围内的用户
            users_res = db.table("users").select("id").in_("id", user_ids).in_("country", allowed).execute()
            user_ids = [u['id'] for u in (users_res.data or [])]
    
    return jsonify({"user_ids": user_ids})

@admin_exam_bp.route('/api/admin/exam/<int:exam_id>/update', methods=['PUT'])
@login_required
@admin_required
def api_admin_exam_update(exam_id):
    data = request.json
    update_data = {}
    if 'start_time' in data:
        update_data['start_time'] = data['start_time'] if data['start_time'] else None
    if 'end_time' in data:
        update_data['end_time'] = data['end_time'] if data['end_time'] else None
    if 'duration' in data:
        update_data['duration'] = data['duration']

    # 根据是否有完整有效期，同步 is_active 和 status
    if update_data.get('start_time') and update_data.get('end_time'):
        update_data['is_active'] = True
        update_data['status'] = 'active'
    elif 'start_time' in update_data or 'end_time' in update_data:
        # 只清空了某一端，视为无效，设为草稿
        update_data['is_active'] = False
        update_data['status'] = 'draft'

    if not update_data:
        return jsonify({"success": False, "message": "无更新内容"}), 400
    db = get_supabase()
    db.table("exams").update(update_data).eq("id", exam_id).execute()
    return jsonify({"success": True})

@admin_exam_bp.route('/api/admin/exam/<int:exam_id>/push_with_settings', methods=['POST'])
@login_required
@admin_required
def admin_push_exam_with_settings(exam_id):
    db = get_supabase()
    data = request.json
    logger.info(f"接收到的推送数据: {data}")  # ✅ 添加这行，查看前端是否传递了 reviewer
    
    # ✅ 1. 首先检查考试权限
    exam_res = db.table("exams").select("countries, country, title").eq("id", exam_id).maybe_single().execute()
    if not exam_res.data:
        return jsonify({"success": False, "message": "考试不存在"}), 404
    
    exam = exam_res.data
    
    if not can_access_exam(exam):
        return jsonify({"success": False, "message": "无权操作此考试"}), 403
    
    # ✅ 2. 获取请求数据
    data = request.json
    user_ids = data.get('user_ids', [])
    
    # ✅ 3. 过滤用户（只保留权限范围内的用户）
    if not is_developer():
        allowed = get_admin_allowed_countries()
        if allowed is not None and allowed:
            users_res = db.table("users").select("id").in_("id", user_ids).in_("country", allowed).execute()
            user_ids = [u['id'] for u in (users_res.data or [])]
        
        # 如果没有有效用户，返回错误
        if not user_ids:
            return jsonify({"success": False, "message": "所选考生不在您的权限范围内"}), 403

    start_time_local = data.get('start_time')
    end_time_local = data.get('end_time')
    duration = data.get('duration')
    user_ids = data.get('user_ids', [])
    reviewer = data.get('reviewer', '')  # ✅ 新增：获取阅卷人

    logger.info(f"获取到的 reviewer 值: {reviewer}")  # ✅ 添加这行

    db = get_supabase()

    # 获取考试信息（用于国家和标题）
    exam_info = db.table("exams").select("country, title").eq("id", exam_id).maybe_single().execute()
    if not exam_info.data:
        return jsonify({"success": False, "message": "考试不存在"}), 404
    exam_data = exam_info.data

    # 更新考试有效期和时长
    update_data = {}
    if start_time_local is not None:
        update_data['start_time'] = start_time_local
        logger.info(f"本地开始时间: {start_time_local}, UTC: {update_data['start_time']}")
    if end_time_local is not None:
        update_data['end_time'] = end_time_local
    if duration is not None:
        update_data['duration'] = duration
    if start_time_local and end_time_local:
        update_data['status'] = 'active'
        update_data['is_active'] = True   # ✅ 关键：确保考试对学员可见
    else:
        # 如果没有有效期，视为草稿，关闭激活状态
        update_data['is_active'] = False
        update_data['status'] = 'draft'

    # ✅ 新增：更新阅卷人（如果有值）
    if reviewer and reviewer.strip():
        update_data['reviewer'] = reviewer
        logger.info(f"使用前端传递的阅卷人: {reviewer}")
    elif not exam_data.get('reviewer'):
        # 如果没有指定阅卷人，尝试根据国家自动获取默认阅卷人
        default_reviewer = get_reviewer_by_country(
            user_country=exam_data.get('country'),
            exam_reviewer=None,
            url_reviewer=None
            )
        if default_reviewer and default_reviewer != "Administrator":
            update_data['reviewer'] = default_reviewer
            logger.info(f"使用默认阅卷人: {default_reviewer}")
        else:
            # 最后的保底
            update_data['reviewer'] = "Administrator"
            logger.info(f"使用保底阅卷人: Administrator")
    else:
        # 保留考试表原有的 reviewer
        logger.info(f"保留原有阅卷人: {exam_data.get('reviewer')}")

    if update_data:
        db.table("exams").update(update_data).eq("id", exam_id).execute()
        logger.info(f"更新考试 {exam_id} 的数据: {update_data}")

    # 可选：按国家过滤考生（如果不需要，可以注释掉整个块）
    def get_user_country(uid):
        user_res = db.table("users").select("country").eq("id", uid).maybe_single().execute()
        return user_res.data.get('country') if user_res.data else None

    country = exam_data.get('country')
    if country and user_ids:
        filtered_ids = [uid for uid in user_ids if get_user_country(uid) == country]
        if not filtered_ids:
            return jsonify({"success": False, "message": "没有符合国家条件的考生"}), 400
        user_ids = filtered_ids

    # 更新考生分配
    # 不再删除全部，而是获取现有分配，然后合并
    if user_ids:
        # 获取现有分配
        existing_res = db.table("exam_assignments").select("user_id").eq("exam_id", exam_id).execute()
        existing_ids = {row['user_id'] for row in (existing_res.data or [])}
        # 需要新增的用户
        to_add = [uid for uid in user_ids if uid not in existing_ids]
        # 需要删除的用户（如果前端传递了完整列表，则删除不在新列表中的用户）
        to_remove = [uid for uid in existing_ids if uid not in user_ids]
        if to_remove:
            db.table("exam_assignments").delete().eq("exam_id", exam_id).in_("user_id", to_remove).execute()
        for uid in to_add:
            db.table("exam_assignments").insert({"exam_id": exam_id, "user_id": uid}).execute()

        # 发送邮件通知
        exam_title = exam_data.get('title', '考试')
        for uid in user_ids:
            user_res = db.table("users").select("email, name_en").eq("id", uid).execute()
            if user_res.data:
                email = user_res.data[0]['email']
                name = user_res.data[0].get('name_en', '用户')
                try:
                    send_bilingual_notification(
                        email=email,
                        scenario=EmailScenario.EXAM_ASSIGNMENT,
                        params={
                            "name": name,
                            "exam_title": exam_title,
                            "start_display": _format_time(start_time_local),
                            "end_display": _format_time(end_time_local),
                            "duration": str(duration),
                            "reviewer": reviewer,
                            "host_url": request.host_url,
                        },
                        host_url=request.host_url,
                        auth_module=auth
                    )
                except Exception as e:
                    logger.warning(f"发送邮件失败: {e}")

    return jsonify({"success": True})

@admin_exam_bp.route('/api/admin/exam/<int:exam_id>/push', methods=['POST'])
@login_required
@admin_required
def admin_push_exam(exam_id):
    data = request.json
    start_time = data.get('start_time')
    end_time = data.get('end_time')
    user_ids = data.get('user_ids', [])  # 选中的考生ID列表

    if not start_time or not end_time or not user_ids:
        return jsonify({"success": False, "message": "缺少参数"}), 400

    db = get_supabase()
    # 更新考试的起止时间和状态
    update_data = {
        "start_time": start_time,
        "end_time": end_time,
        "status": "active"
    }
    db.table("exams").update(update_data).eq("id", exam_id).execute()

    # 插入考试-用户关联记录（清除旧的再插入，或使用upsert）
    # 先删除旧的关联
    db.table("exam_assignments").delete().eq("exam_id", exam_id).execute()
    # 批量插入
    for uid in user_ids:
        db.table("exam_assignments").insert({"exam_id": exam_id, "user_id": uid}).execute()

    # 发送邮件通知（异步或同步）
    exam_res = db.table("exams").select("title").eq("id", exam_id).execute()
    exam_title = exam_res.data[0]['title'] if exam_res.data else "考试"
    reviewer = exam_res.data[0].get('reviewer', '管理员') if exam_res.data else "管理员"
    duration = data.get('duration', 60)

    for uid in user_ids:
        user_res = db.table("users").select("email, name_en").eq("id", uid).execute()
        if user_res.data:
            email = user_res.data[0]['email']
            name = user_res.data[0].get('name_en', '用户')
            # 调用邮件发送函数（已有的 auth.send_email）
            try:
                send_bilingual_notification(
                    email=email,
                    scenario=EmailScenario.EXAM_ASSIGNMENT,
                    params={
                        "name": name,
                        "exam_title": exam_title,
                        "start_display": _format_time(start_time),
                        "end_display": _format_time(end_time),
                        "duration": str(duration),
                        "reviewer": reviewer,
                        "host_url": request.host_url,
                    },
                    host_url=request.host_url,
                    auth_module=auth
                )
            except Exception as e:
                logger.warning(f"发送邮件失败: {e}")

    return jsonify({"success": True})

'''
@admin_exam_bp.route('/api/admin/exams/list')
@login_required
@admin_required
def api_admin_exams_list():
    db = get_supabase()
    include_deleted = request.args.get('include_deleted', 'false').lower() == 'true'
    allowed_countries = get_admin_allowed_countries()
    # 获取过滤参数
    country_input = request.args.get('country', '')
    name = request.args.get('name', '')
    target_status = request.args.get('status', '')
    quarter = request.args.get('quarter', '')
    creator = request.args.get('creator', '')
    reviewer = request.args.get('reviewer', '')
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 20, type=int)

    # 管理员权限范围
    allowed = get_allowed_countries()
    filter_country_code = match_country_code(country_input) if country_input else None
    q_start, q_end = quarter_to_date_range(quarter) if quarter else (None, None)
    
    # ✅ 获取当前用户角色
    current_role = session.get('role')
    is_dev = is_developer()
    is_super_admin = current_role == 'super_admin' or is_dev
    
    # ✅ 获取调试模式状态（优先从请求参数，其次从 session）
    debug_mode_param = request.args.get('debug_mode', '')
    if debug_mode_param:
        debug_mode = debug_mode_param.lower() == 'true'
        session['exam_list_debug_mode'] = debug_mode
    else:
        debug_mode = session.get('exam_list_debug_mode', False)
    
    logger.info(f"调试模式状态: {debug_mode}, 用户角色: {current_role}, is_super_admin: {is_super_admin}")
    try:
        # 1. 基础查询
        query = db.table("exams").select("*", count="exact")
        if not include_deleted:
            query = query.is_("deleted_at", "null")
        if name:
            query = query.ilike("title", f"%{name}%")
        if creator:
            users_res = db.table("users").select("id").ilike("name_en", f"%{creator}%").execute()
            creator_ids = [u['id'] for u in (users_res.data or [])]
            if creator_ids:
                query = query.in_("created_by", creator_ids)
            else:
                return jsonify({"data": [], "total": 0})
        if reviewer:
            query = query.ilike("reviewer", f"%{reviewer}%")

        res = query.execute()
        all_exams = res.data or []

        # 2. 管理员权限过滤（如果 allowed 不为 None）
        if allowed is not None:
            if not allowed:
                return jsonify({"data": [], "total": 0, "page": page, "per_page": per_page})
            
            # 获取允许国家的所有用户ID
            users_in_allowed = db.table("users").select("id").in_("country", allowed).execute()
            allowed_user_ids = [u['id'] for u in (users_in_allowed.data or [])] if users_in_allowed.data else []
            
            # 查询分配了至少一个允许国家考生的考试ID
            allowed_exam_ids = set()
            if allowed_user_ids:
                assign_res = db.table("exam_assignments").select("exam_id").in_("user_id", allowed_user_ids).execute()
                allowed_exam_ids = {a['exam_id'] for a in (assign_res.data or [])}
            
            filtered = []
            for exam in all_exams:
                # 解析考试国家列表
                exam_countries = []
                
                # 处理 countries 字段（可能是 JSON 字符串、列表或空）
                countries_val = exam.get('countries')
                if countries_val:
                    if isinstance(countries_val, str) and countries_val:
                        try:
                            parsed = json.loads(countries_val)
                            if isinstance(parsed, list):
                                exam_countries = parsed
                            elif parsed:
                                exam_countries = [parsed]
                        except:
                            pass
                    elif isinstance(countries_val, list):
                        exam_countries = countries_val
                
                # 如果没有 countries，使用旧的 country 字段
                if not exam_countries and exam.get('country'):
                    exam_countries = [exam.get('country')]
                
                # ✅ 关键：检查是否有交集
                # 将 allowed 转换为列表（如果已经是列表则直接用）
                allowed_list = allowed if isinstance(allowed, list) else [allowed] if allowed else []
                
                # 检查考试国家是否在管理员权限范围内
                matches_country = False
                for ec in exam_countries:
                    if ec in allowed_list:
                        matches_country = True
                        break
                
                # 通过国家匹配或考试分配
                if matches_country or exam['id'] in allowed_exam_ids:
                    filtered.append(exam)
                    logger.info(f"✅ 考试 {exam['id']} 通过: 国家匹配={matches_country}, 分配匹配={exam['id'] in allowed_exam_ids}")
                else:
                    logger.info(f"❌ 考试 {exam['id']} 被过滤: 国家列表={exam_countries}, 允许范围={allowed_list}")
            
            all_exams = filtered
            logger.info(f"权限过滤后剩余 {len(all_exams)} 条考试记录")

        # 3. 前端选择的额外国家筛选
        if filter_country_code:
            users_in_filter = db.table("users").select("id").eq("country", filter_country_code).execute()
            filter_user_ids = [u['id'] for u in (users_in_filter.data or [])] if users_in_filter.data else []
            filter_exam_ids = set()
            if filter_user_ids:
                assign_filter = db.table("exam_assignments").select("exam_id").in_("user_id", filter_user_ids).execute()
                filter_exam_ids = {a['exam_id'] for a in (assign_filter.data or [])}
            all_exams = [exam for exam in all_exams 
                        if exam.get('country') == filter_country_code or exam['id'] in filter_exam_ids]

        # 4. 季度过滤
        if quarter and q_start and q_end:
            q_start_dt = datetime.fromisoformat(q_start)
            q_end_dt = datetime.fromisoformat(q_end)
            filtered = []
            for exam in all_exams:
                start, end = exam.get('start_time'), exam.get('end_time')
                if start and end:
                    try:
                        start_dt = datetime.fromisoformat(start)
                        end_dt = datetime.fromisoformat(end)
                        if start_dt <= q_end_dt and end_dt >= q_start_dt:
                            filtered.append(exam)
                    except:
                        pass
            all_exams = filtered

        # 5. 构建返回数据
        now = datetime.now(timezone.utc)
        exams_with_status = []
        
        for exam in all_exams:
            try:
                exam_id = exam['id']
                dynamic_status = get_exam_status(exam)
                if target_status and dynamic_status != target_status:
                    continue
                
                # 统计题目数
                q_count = db.table("questions").select("id", count="exact").eq("exam_id", exam_id).execute().count or 0
                assigned_count = db.table("exam_assignments").select("user_id", count="exact").eq("exam_id", exam_id).execute().count or 0
                submitted_res = db.table("exam_results").select("user_id", count="exact").eq("exam_id", exam_id).execute()
                submitted_count = submitted_res.count or 0
                
                # 最高分/最低分
                max_res = db.table("exam_results").select("total_score").eq("exam_id", exam_id).order("total_score", desc=True).limit(1).execute()
                max_score = max_res.data[0]['total_score'] if max_res.data else None
                min_res = db.table("exam_results").select("total_score").eq("exam_id", exam_id).order("total_score", desc=False).limit(1).execute()
                min_score = min_res.data[0]['total_score'] if min_res.data else None
                
                # 创建人
                creator_name = ''
                if exam.get('created_by'):
                    creator_res = db.table("users").select("name_en").eq("id", exam['created_by']).maybe_single().execute()
                    creator_name = creator_res.data.get('name_en', '') if creator_res.data else ''

                # ✅ 修复：初始化 exam_countries 变量
                exam_countries = []
                
                # 优先使用 countries 字段
                countries_data = exam.get('countries')
                if countries_data:
                    if isinstance(countries_data, str) and countries_data:
                        try:
                            parsed = json.loads(countries_data)
                            exam_countries = parsed if isinstance(parsed, list) else [parsed]
                        except:
                            exam_countries = []
                    elif isinstance(countries_data, list):
                        exam_countries = countries_data
                
                # 如果没有 countries，使用旧的 country 字段
                if not exam_countries:
                    old_country = exam.get('country')
                    if old_country:
                        exam_countries = [old_country]
                
                # 根据管理员权限过滤国家（只保留有权管理的国家）
                if allowed is not None:
                    filtered_countries = [c for c in exam_countries if c in allowed]
                else:
                    filtered_countries = exam_countries
                
                countries_display = ', '.join(filtered_countries) if filtered_countries else '-'
        
                # ✅ 添加权限字段
                exam['is_super_admin'] = is_super_admin
                exam['debug_mode'] = debug_mode
                
                # 判断是否显示推送设置按钮
                # 条件1: 超管或开发者
                # 条件2: 调试模式开启
                # 条件3: 考试状态为 closed
                can_show_debug_push = is_super_admin and debug_mode and dynamic_status == 'closed'
                logger.info(f"考试 {exam.get('id')} 状态={dynamic_status}, can_show_debug_push={can_show_debug_push}")

                exams_with_status.append({
                    "id": exam_id,
                    "title": exam['title'],
                    "status": dynamic_status,
                    "countries_display": countries_display,
                    "countries": filtered_countries,
                    "country_count": len(filtered_countries),
                    "start_time": exam.get('start_time'),
                    "end_time": exam.get('end_time'),
                    "duration": exam.get('duration', 60),
                    "question_count": q_count,
                    "assigned_count": assigned_count,
                    "submitted_count": submitted_count,
                    "max_score": max_score,
                    "min_score": min_score,
                    "retake_count": 0,
                    "reviewer": exam.get('reviewer', ''),
                    "created_by_name": creator_name,
                    "quarter": exam.get('quarter', ''),
                    "deleted_at": exam.get('deleted_at'),
                    "country": exam.get('country', ''),
                    "can_show_debug_push": can_show_debug_push,
                    "is_super_admin": is_super_admin,
                    "debug_mode": debug_mode
                })
            except Exception as e:
                logger.error(f"处理考试 {exam.get('id')} 的统计数据时出错: {e}")
                continue

        total = len(exams_with_status)
        start_idx = (page - 1) * per_page
        end_idx = start_idx + per_page
        paginated = exams_with_status[start_idx:end_idx]
        
        return jsonify({"data": paginated, "total": total, "page": page, "per_page": per_page})
        
    except Exception as e:
        logger.error(f"api_admin_exams_list 执行失败: {e}", exc_info=True)
        return jsonify({"data": [], "total": 0, "page": page, "per_page": per_page, "error": str(e)}), 500
'''

@admin_exam_bp.route('/api/admin/exams/list')
@login_required
@admin_required
def api_admin_exams_list():
    db = get_supabase()
    include_deleted = request.args.get('include_deleted', 'false').lower() == 'true'
    
    # 获取过滤参数
    name = request.args.get('name', '')
    target_status = request.args.get('status', '')
    quarter = request.args.get('quarter', '')
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 20, type=int)
    
    allowed_countries = get_admin_allowed_countries()
    is_super_admin = session.get('role') == 'super_admin' or is_developer()
    debug_mode = session.get('exam_list_debug_mode', False)
    
    try:
        # ========== 1. 基础查询 ==========
        query = db.table("exams").select("*")
        if not include_deleted:
            query = query.is_("deleted_at", "null")
        if name:
            query = query.ilike("title", f"%{name}%")
        
        all_exams = query.execute().data or []
        
        # ========== 2. 权限过滤 ==========
        if allowed_countries is not None and allowed_countries:
            filtered = []
            for exam in all_exams:
                exam_countries = parse_exam_countries(exam)
                if not exam_countries and exam.get('country'):
                    exam_countries = [exam.get('country')]
                if any(c in allowed_countries for c in exam_countries):
                    filtered.append(exam)
            all_exams = filtered
        
        # ========== 3. 状态过滤 ==========
        if target_status:
            all_exams = [e for e in all_exams if get_exam_status(e) == target_status]
        
        # ========== 4. 季度过滤 ==========
        if quarter:
            q_start, q_end = quarter_to_date_range(quarter)
            if q_start and q_end:
                q_start_dt = datetime.fromisoformat(q_start)
                q_end_dt = datetime.fromisoformat(q_end)
                temp = []
                for exam in all_exams:
                    start, end = exam.get('start_time'), exam.get('end_time')
                    if start and end:
                        try:
                            start_dt = datetime.fromisoformat(start)
                            end_dt = datetime.fromisoformat(end)
                            if start_dt <= q_end_dt and end_dt >= q_start_dt:
                                temp.append(exam)
                        except:
                            pass
                all_exams = temp
        
        # ========== 5. ✅ 批量获取统计数据（关键修复）==========
        if all_exams:
            exam_ids = [exam['id'] for exam in all_exams]
            
            # 批量获取题目数量
            questions_counts = {}
            try:
                for exam_id in exam_ids:
                    count = db.table("questions").select("id", count="exact").eq("exam_id", exam_id).execute().count or 0
                    questions_counts[exam_id] = count
            except Exception as e:
                logger.warning(f"获取题目数量失败: {e}")
                questions_counts = {eid: 0 for eid in exam_ids}
            
            # 批量获取分配数量
            assigned_counts = {}
            try:
                for exam_id in exam_ids:
                    count = db.table("exam_assignments").select("user_id", count="exact").eq("exam_id", exam_id).execute().count or 0
                    assigned_counts[exam_id] = count
            except Exception as e:
                logger.warning(f"获取分配数量失败: {e}")
                assigned_counts = {eid: 0 for eid in exam_ids}
            
            # 批量获取提交数量
            submitted_counts = {}
            try:
                for exam_id in exam_ids:
                    count = db.table("exam_results").select("user_id", count="exact").eq("exam_id", exam_id).execute().count or 0
                    submitted_counts[exam_id] = count
            except Exception as e:
                logger.warning(f"获取提交数量失败: {e}")
                submitted_counts = {eid: 0 for eid in exam_ids}
        else:
            questions_counts = assigned_counts = submitted_counts = {}
        
        # ========== 6. 构建返回数据 ==========
        exams_with_status = []
        for exam in all_exams:
            exam_id = exam['id']
            
            # 从缓存中获取统计数据
            q_count = questions_counts.get(exam_id, 0)
            assigned_count = assigned_counts.get(exam_id, 0)
            submitted_count = submitted_counts.get(exam_id, 0)
            
            # 解析国家
            exam_countries = parse_exam_countries(exam)
            if not exam_countries and exam.get('country'):
                exam_countries = [exam.get('country')]
            
            # 根据管理员权限过滤国家显示
            if allowed_countries is not None:
                filtered_countries = [c for c in exam_countries if c in allowed_countries]
            else:
                filtered_countries = exam_countries
            
            countries_display = ', '.join(filtered_countries) if filtered_countries else '-'
            status = get_exam_status(exam)
            can_show_debug_push = is_super_admin and debug_mode and status == 'closed'
            
            exams_with_status.append({
                "id": exam_id,
                "title": exam.get('title', ''),
                "status": status,
                "countries_display": countries_display,
                "start_time": exam.get('start_time'),
                "end_time": exam.get('end_time'),
                "duration": exam.get('duration', 60),
                "question_count": q_count,
                "assigned_count": assigned_count,
                "submitted_count": submitted_count,
                "max_score": None,
                "min_score": None,
                "retake_count": 0,
                "reviewer": exam.get('reviewer', ''),
                "deleted_at": exam.get('deleted_at'),
                "can_show_debug_push": can_show_debug_push
            })
        
        # 分页
        total = len(exams_with_status)
        start_idx = (page - 1) * per_page
        end_idx = start_idx + per_page
        paginated = exams_with_status[start_idx:end_idx]
        
        return jsonify({
            "data": paginated,
            "total": total,
            "page": page,
            "per_page": per_page
        })
        
    except Exception as e:
        logger.error(f"api_admin_exams_list 错误: {e}", exc_info=True)
        return jsonify({"data": [], "total": 0, "error": str(e)}), 500

@admin_exam_bp.route('/api/admin/exams/stats')
@login_required
@admin_required
def api_admin_exams_stats():
    db = get_supabase()
    status_counts = {status: 0 for status in ['draft', 'active', 'closed']}
    for status in status_counts.keys():
        res = db.table("exams").select("id", count="exact").eq("status", status).execute()
        status_counts[status] = res.count or 0
    return jsonify(status_counts)

@admin_exam_bp.route('/api/admin/exam/<int:exam_id>/scores')
@login_required
@admin_required
def api_admin_exam_scores(exam_id):
    db = get_supabase()
    allowed = get_allowed_countries()
    search = request.args.get('search', '').strip()
    submit_method = request.args.get('submit_method', '')
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 20, type=int)

    # 使用权限函数获取当前用户信息
    current_user_role = session.get('role', 'user')
    current_user_id = session.get('user_id')
    is_dev = is_developer()
    is_super_admin = current_user_role == 'super_admin' or is_dev

    # 1. 获取该考试的所有成绩记录
    query = db.table("exam_results").select("*").eq("exam_id", exam_id).is_("deleted_at", "null")
    if submit_method:
        query = query.eq("submit_method", submit_method)
    results_all = query.execute().data or []

    if not results_all:
        return jsonify({"data": [], "total": 0, "page": page, "per_page": per_page})

    # 2. 获取所有相关用户的详细信息（避免多次请求）
    user_ids = list(set(r['user_id'] for r in results_all))
    users_res = db.table("users").select("id, name_cn, name_en, email, country, wh_id, is_partner").in_("id", user_ids).execute()
    users_dict = {u['id']: u for u in (users_res.data or [])}

    # 3. 国家权限过滤（基于用户的国家）
    if allowed is not None:
        if not allowed:
            return jsonify({"data": [], "total": 0, "page": page, "per_page": per_page})
        filtered_results = []
        for r in results_all:
            user = users_dict.get(r['user_id'])
            if user and user.get('country') in allowed:
                filtered_results.append(r)
        results_all = filtered_results

    # 4. 搜索过滤（基于用户姓名或邮箱）
    if search:
        search_lower = search.lower()
        filtered_results = []
        for r in results_all:
            user = users_dict.get(r['user_id'])
            if user:
                name = user.get('name_cn') or user.get('name_en') or ''
                email = user.get('email') or ''
                if search_lower in name.lower() or search_lower in email.lower():
                    filtered_results.append(r)
        results_all = filtered_results

    # 5. 按提交时间倒序排序
    results_all.sort(key=lambda x: x.get('created_at', ''), reverse=True)

    # 6. 内存分页
    total = len(results_all)
    start = (page - 1) * per_page
    end = start + per_page
    paginated = results_all[start:end]

    # 7. 构造返回数据
    scores = []
    for r in paginated:
        user = users_dict.get(r['user_id'], {})
        user_country = user.get('country', '')
        
        # 使用权限函数判断是否有删除权限
        can_delete = is_super_admin  # 超管和开发者都可以删除
        
        # 获取考试信息以获取阅卷人
        exam_res = db.table("exams").select("reviewer").eq("id", exam_id).maybe_single().execute()
        if exam_res is not None and hasattr(exam_res, 'data') and exam_res.data:
            reviewer = exam_res.data.get('reviewer', '-')
        else:
            reviewer = '-'
        
        # 获取开始时间（从 user_exam_status 表）
        status_res = db.table("user_exam_status").select("started_at").eq("user_id", r['user_id']).eq("exam_id", exam_id).maybe_single().execute()
        if status_res is not None and hasattr(status_res, 'data') and status_res.data:
            started_at = status_res.data.get('started_at')
        else:
            started_at = None
        
        scores.append({
            "user_id": r['user_id'],
            "name": user.get('name_cn') or user.get('name_en') or user.get('email', ''),
            "email": user.get('email', ''),
            "country": user_country,
            "status": "Submitted",
            "started_at": started_at,
            "submitted_at": r.get('created_at'),
            "submit_method": r.get('submit_method', 'manual'),
            "time_used": r.get('time_used'),
            "score": r.get('total_score', 0),
            "result_id": r['id'],
            "reviewer": reviewer,
            "can_delete": can_delete  # 权限字段
        })

    return jsonify({"data": scores, "total": total, "page": page, "per_page": per_page})

@admin_exam_bp.route('/admin/exam/<int:exam_id>/scores')
@login_required
@admin_required
def admin_exam_scores_page(exam_id):
    db = get_supabase()
    
    # 获取考试标题
    exam_res = db.table("exams") \
        .select("title") \
        .eq("id", exam_id) \
        .maybe_single() \
        .execute()
    
    exam_title = exam_res.data.get('title', f'考试 #{exam_id}') if exam_res.data else f'考试 #{exam_id}'
    
    # ✅ 使用权限函数获取用户角色 
    is_dev = is_developer()
    is_super_admin = session.get('role') == 'super_admin' or is_dev
    
    return render_template(
        'admin/list_exams_scores.html',
        exam_id=exam_id,
        exam_title=exam_title,
        is_super_admin=is_super_admin  # 传递是否超管/开发者
    )

@admin_exam_bp.route('/api/admin/exam/result/<int:result_id>', methods=['DELETE'])
@login_required
@admin_required
def api_admin_delete_exam_result(result_id):
    """删除考试成绩记录（软删除，保留审计日志）"""
    db = get_supabase()
    user_id = session['user_id']
    
    # 1. 先获取成绩记录
    result_res = db.table("exam_results").select("*").eq("id", result_id).execute()
    if not result_res.data:
        return jsonify({"success": False, "message": "成绩记录不存在"}), 404
    
    result = result_res.data[0]
    exam_id = result.get('exam_id')
    
    # 2. 再获取考试信息（单独查询，不使用关联）
    exam_res = db.table("exams").select("country").eq("id", exam_id).maybe_single().execute()
    exam_country = exam_res.data.get('country') if exam_res.data else None
    
    # 3. 权限检查
    allowed = get_admin_allowed_countries()
    if allowed is not None and exam_country not in allowed:
        return jsonify({"success": False, "message": "无权删除此考试成绩"}), 403
    
    try:
        # 4. 软删除
        db.table("exam_results").update({
            "deleted_at": datetime.now(timezone.utc).isoformat(),
            "deleted_by": user_id
        }).eq("id", result_id).execute()
        
        # 5. 同步更新 user_exam_status 表，允许重新考试
        db.table("user_exam_status").update({
            "is_submitted": False,
            "reset_at": datetime.now(timezone.utc).isoformat(),
            "submitted_at": None
        }).eq("user_id", result['user_id']).eq("exam_id", exam_id).execute()
        
        logger.info(f"考试成绩已删除: result_id={result_id}, 操作人={user_id}")
        return jsonify({"success": True, "message": "成绩记录已删除，考生可重新考试"})
    except Exception as e:
        logger.error(f"删除成绩记录失败: {e}")
        return jsonify({"success": False, "message": str(e)}), 500

@admin_exam_bp.route('/api/admin/exam/result/batch_delete', methods=['POST'])
@login_required
@admin_required
def api_admin_batch_delete_exam_results():
    """批量删除考试成绩记录（支持软删除和永久删除）"""
    # ✅ 权限检查：只有超管或开发者可以删除
    if not is_developer() and session.get('role') != 'super_admin':
        return jsonify({
            "success": False, 
            "message": "只有超级管理员可以删除考试成绩"
        }), 403

    data = request.json
    result_ids = data.get('ids', [])
    delete_type = data.get('delete_type', 'soft')  # 'soft' 或 'hard'
    
    if not result_ids:
        return jsonify({"success": False, "message": "请选择要删除的成绩记录"}), 400
    
    db = get_supabase()
    user_id = session['user_id']
    success_count = 0
    fail_count = 0
    errors = []
    
    allowed = get_admin_allowed_countries()
    
    for result_id in result_ids:
        try:
            # 获取成绩记录
            result_res = db.table("exam_results").select("*").eq("id", result_id).execute()
            if not result_res.data:
                fail_count += 1
                errors.append(f"记录 {result_id} 不存在")
                continue
            
            result = result_res.data[0]
            exam_id = result.get('exam_id')
            
            # 获取考试国家
            exam_res = db.table("exams").select("country").eq("id", exam_id).maybe_single().execute()
            exam_country = exam_res.data.get('country') if exam_res.data else None
            
            # 权限检查
            if allowed is not None and exam_country not in allowed:
                fail_count += 1
                errors.append(f"记录 {result_id}: 无权限删除")
                continue
            
            if delete_type == 'hard':
                # 永久删除
                db.table("exam_results").delete().eq("id", result_id).execute()
            else:
                # 软删除
                db.table("exam_results").update({
                    "deleted_at": datetime.now(timezone.utc).isoformat(),
                    "deleted_by": user_id
                }).eq("id", result_id).execute()
                
                # 重置考试状态（仅软删除时）
                db.table("user_exam_status").update({
                    "is_submitted": False,
                    "reset_at": datetime.now(timezone.utc).isoformat(),
                    "submitted_at": None
                }).eq("user_id", result['user_id']).eq("exam_id", exam_id).execute()
            
            success_count += 1
        except Exception as e:
            fail_count += 1
            errors.append(f"记录 {result_id}: {str(e)}")
    
    return jsonify({
        "success": True,
        "success_count": success_count,
        "fail_count": fail_count,
        "errors": errors[:10]
    })

@admin_exam_bp.route('/api/common/quarters')
def api_quarters():
    """根据数据库中考试的有效期动态生成季度选项列表"""
    db = get_supabase()
    # 查询所有考试的 start_time 和 end_time（只取非空）
    res = db.table("exams").select("start_time, end_time").execute()
    quarters = set()
    for exam in res.data or []:
        for field in ['start_time', 'end_time']:
            time_str = exam.get(field)
            if not time_str:
                continue
            try:
                # 解析 ISO 8601 字符串（可能带时区，如 2026-04-28T15:00:00+00:00）
                # 使用 dateutil.parser 或简单切片
                # 这里我们直接取年份和月份
                if 'T' in time_str:
                    # 格式如 "2026-04-28T15:00:00+00:00" 或 "2026-04-28T15:00:00Z"
                    date_part = time_str.split('T')[0]
                else:
                    date_part = time_str[:10]  # 假设是 YYYY-MM-DD
                year, month = int(date_part[:4]), int(date_part[5:7])
                quarter = (month - 1) // 3 + 1
                quarters.add(f"{year}Q{quarter}")
            except Exception as e:
                print(f"解析时间出错: {time_str}, {e}")
                continue
    # 按年份和季度排序
    sorted_quarters = sorted(list(quarters), key=lambda x: (int(x[:4]), int(x[5])))
    return jsonify(sorted_quarters)

@admin_exam_bp.route('/api/admin/questions/stats')
@login_required
@admin_required
def admin_questions_stats():
    """题库统计API"""
    db = get_supabase()
    allowed = get_allowed_countries()
    
    try:
        if allowed is not None and allowed:
            exams_in_allowed = db.table("exams").select("id").in_("country", allowed).execute()
            exam_ids = [e['id'] for e in (exams_in_allowed.data or [])]
            if exam_ids:
                questions_count = db.table("questions").select("id", count="exact").in_("exam_id", exam_ids).execute().count or 0
            else:
                questions_count = 0
        else:
            questions_count = db.table("questions").select("id", count="exact").execute().count or 0
        
        return jsonify({"total_questions": questions_count})
    except Exception as e:
        logger.error(f"题库统计失败: {e}")
        return jsonify({"total_questions": 0}), 500

@admin_exam_bp.route('/admin/exam/<int:exam_id>/candidate_status')
@login_required
@admin_required
def admin_candidate_exam_status(exam_id):
    """考生考试状态页面（独立页面）"""
    db = get_supabase()
    
    # 获取考试标题
    exam_res = db.table("exams").select("title").eq("id", exam_id).maybe_single().execute()
    exam_title = exam_res.data.get('title', f'考试 #{exam_id}') if exam_res.data else f'考试 #{exam_id}'
    
    return render_template(
        'admin/candidate_exam_status.html',
        exam_id=exam_id,
        exam_title=exam_title
    )

# routes/admin_exam.py - 添加快速分配接口

@admin_exam_bp.route('/api/admin/exam/<int:exam_id>/quick_assign', methods=['POST'])
@login_required
@admin_required
def admin_quick_assign_exam(exam_id):
    """快速分配考生（直接通知，无需重新设置有效期）"""
    try:
        data = request.json
        user_ids = data.get('user_ids', [])
        
        if not user_ids:
            return jsonify({"success": False, "message": "请选择要分配的考生"}), 400
        
        db = get_supabase()
        
        # 1. 获取考试信息（检查是否已配置）
        exam_res = db.table("exams").select("start_time, end_time, duration, title, reviewer, countries").eq("id", exam_id).maybe_single().execute()
        if not exam_res.data:
            return jsonify({"success": False, "message": "考试不存在"}), 404
        
        exam = exam_res.data
        
        # 2. 检查考试是否已配置有效期
        if not exam.get('start_time') or not exam.get('end_time'):
            return jsonify({"success": False, "message": "考试尚未配置有效期，请先完成推送设置"}), 400
        
        # 3. 权限检查
        if not can_access_exam(exam):
            return jsonify({"success": False, "message": "无权操作此考试"}), 403
        
        # 4. 获取现有分配
        existing_res = db.table("exam_assignments").select("user_id").eq("exam_id", exam_id).execute()
        existing_ids = {row['user_id'] for row in (existing_res.data or [])}
        
        # 5. 只添加未分配的考生
        to_add = [uid for uid in user_ids if uid not in existing_ids]
        
        if not to_add:
            return jsonify({"success": False, "message": "所选考生已全部分配"}), 400
        
        # 6. 添加分配记录
        for uid in to_add:
            db.table("exam_assignments").insert({"exam_id": exam_id, "user_id": uid}).execute()
        
        # 7. 发送邮件通知
        exam_title = exam.get('title', '考试')
        start_time = exam.get('start_time')
        end_time = exam.get('end_time')
        duration = exam.get('duration', 60)
        reviewer = exam.get('reviewer', '管理员')
        
        for uid in to_add:
            user_res = db.table("users").select("email, name_en").eq("id", uid).execute()
            if user_res.data:
                email = user_res.data[0]['email']
                name = user_res.data[0].get('name_en', '用户')
                try:
                    send_bilingual_notification(
                        email=email,
                        scenario=EmailScenario.EXAM_ASSIGNMENT,
                        params={
                            "name": name,
                            "exam_title": exam_title,
                            "start_display": _format_time(start_time),
                            "end_display": _format_time(end_time),
                            "duration": str(duration),
                            "reviewer": reviewer,
                            "host_url": request.host_url,
                        },
                        host_url=request.host_url,
                        auth_module=auth
                    )
                except Exception as e:
                    logger.warning(f"发送邮件失败: {e}")
        
        logger.info(f"快速分配: 考试 {exam_id}, 新增 {len(to_add)} 名考生")
        
        return jsonify({
            "success": True,
            "message": f"成功分配 {len(to_add)} 名考生",
            "added_count": len(to_add)
        })
        
    except Exception as e:
        logger.error(f"快速分配失败: {e}", exc_info=True)
        return jsonify({"success": False, "message": str(e)}), 500

@admin_exam_bp.route('/api/admin/exam/<int:exam_id>/batch_remove', methods=['POST'])
@login_required
@admin_required
def admin_batch_remove_exam_assignment(exam_id):
    """批量移除考生分配（删除分配关系，不删除成绩）"""
    try:
        data = request.json
        user_ids = data.get('user_ids', [])
        
        if not user_ids:
            return jsonify({"success": False, "message": "请选择要移除的考生"}), 400
        
        db = get_supabase()
        
        # 1. 获取考试信息（权限检查）
        exam_res = db.table("exams").select("countries, title").eq("id", exam_id).maybe_single().execute()
        if not exam_res.data:
            return jsonify({"success": False, "message": "考试不存在"}), 404
        
        exam = exam_res.data
        
        # 2. 权限检查
        if not can_access_exam(exam):
            return jsonify({"success": False, "message": "无权操作此考试"}), 403
        
        # 3. 只移除已分配的考生（保留未分配的，避免误删）
        existing_res = db.table("exam_assignments").select("user_id").eq("exam_id", exam_id).execute()
        existing_ids = {row['user_id'] for row in (existing_res.data or [])}
        
        # 只删除确实存在的分配关系
        to_remove = [uid for uid in user_ids if uid in existing_ids]
        
        if not to_remove:
            return jsonify({"success": False, "message": "jsonify_selected_candidate_unassigned_removed", "params": []}), 400
        
        # 4. 删除分配关系
        for uid in to_remove:
            db.table("exam_assignments").delete().eq("exam_id", exam_id).eq("user_id", uid).execute()
        
        # 5. 可选：同时清除用户的考试状态（让考生可以重新开始）
        for uid in to_remove:
            db.table("user_exam_status").delete().eq("exam_id", exam_id).eq("user_id", uid).execute()
            db.table("user_exam_drafts").delete().eq("exam_id", exam_id).eq("user_id", uid).execute()
        
        logger.info(f"批量移除: 考试 {exam_id}, 移除 {len(to_remove)} 名考生")
        
        return jsonify({
            "success": True,
            "message": f"成功移除 {len(to_remove)} 名考生的分配",
            "removed_count": len(to_remove)
        })
        
    except Exception as e:
        logger.error(f"批量移除失败: {e}", exc_info=True)
        return jsonify({"success": False, "message": str(e)}), 500

@admin_exam_bp.route('/api/admin/exam/debug_mode', methods=['GET', 'POST'])
@login_required
def exam_debug_mode():
    """获取或设置考试列表调试模式（仅超管/开发者可用）"""
    # 权限检查
    if not is_developer() and session.get('role') != 'super_admin':
        if request.method == 'POST':
            return jsonify({"success": False, "message": "权限不足"}), 403
        else:
            return jsonify({"debug_mode": False, "can_debug": False})
    
    if request.method == 'POST':
        data = request.json
        enabled = data.get('enabled', False)
        session['exam_list_debug_mode'] = enabled
        logger.info(f"调试模式已{'开启' if enabled else '关闭'}，用户: {session.get('user_id')}")
        return jsonify({"success": True, "debug_mode": enabled})
    else:
        # GET 请求
        debug_mode = session.get('exam_list_debug_mode', False)
        return jsonify({
            "debug_mode": debug_mode,
            "can_debug": True,
            "role": session.get('role')
        })

@admin_exam_bp.route('/api/admin/exam/<int:exam_id>/reopen', methods=['POST'])
@login_required
@admin_required
def reopen_exam_for_testing(exam_id):
    """
    重新打开已关闭的考试用于测试（仅超管/开发者可用）
    重置考试的有效期，清空所有考生的提交状态
    """
    # 权限检查
    if not is_developer() and session.get('role') != 'super_admin':
        return jsonify({"success": False, "message": "权限不足，仅超管或开发者可操作"}), 403
    
    # 检查调试模式是否开启
    if not session.get('exam_list_debug_mode', False):
        return jsonify({"success": False, "message": "请先开启调试模式"}), 400
    
    data = request.json
    new_start_time = data.get('start_time')
    new_end_time = data.get('end_time')
    new_duration = data.get('duration')
    
    if not new_start_time or not new_end_time:
        return jsonify({"success": False, "message": "请设置新的有效期"}), 400
    
    db = get_supabase()
    
    try:
        # 1. 更新考试的有效期和时长
        update_data = {
            "start_time": new_start_time,
            "end_time": new_end_time,
            "status": "active",  # 重新激活
            "is_active": True
        }
        if new_duration:
            update_data["duration"] = new_duration
        
        db.table("exams").update(update_data).eq("id", exam_id).execute()
        
        # 2. 获取该考试的所有考生
        assignments = db.table("exam_assignments").select("user_id").eq("exam_id", exam_id).execute()
        user_ids = [a['user_id'] for a in (assignments.data or [])]
        
        # 3. 重置所有考生的考试状态（清除提交记录，保留分配关系）
        for user_id in user_ids:
            # 检查是否存在状态记录
            status_res = db.table("user_exam_status").select("id").eq("user_id", user_id).eq("exam_id", exam_id).maybe_single().execute()
            
            if status_res and status_res.data:
                # 重置状态
                db.table("user_exam_status").update({
                    "is_submitted": False,
                    "submitted_at": None,
                    "reset_at": datetime.now(timezone.utc).isoformat(),
                    "started_at": None  # 清空开始时间，让考生重新开始
                }).eq("id", status_res.data['id']).execute()
            else:
                # 创建状态记录
                db.table("user_exam_status").insert({
                    "user_id": user_id,
                    "exam_id": exam_id,
                    "is_submitted": False,
                    "reset_at": datetime.now(timezone.utc).isoformat()
                }).execute()
        
        # 4. 可选：软删除旧的考试成绩（保留历史，标记为测试数据）
        # 或者保留但让考生可以重新考试
        
        logger.info(f"考试 {exam_id} 已重新打开用于测试，有效期更新为 {new_start_time} - {new_end_time}")
        
        return jsonify({
            "success": True,
            "message": f"考试已重新打开，{len(user_ids)} 名考生可以重新考试",
            "affected_users": len(user_ids)
        })
        
    except Exception as e:
        logger.error(f"重新打开考试失败: {e}")
        return jsonify({"success": False, "message": str(e)}), 500

