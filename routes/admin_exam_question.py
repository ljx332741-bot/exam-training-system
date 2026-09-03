# routes/admin_exam_question.py
import os, json, logging, uuid
from datetime import datetime, timezone
from flask import Blueprint, render_template, request, jsonify, session, current_app as app
from services.db import get_supabase
from utils.permissions import get_admin_allowed_countries, is_developer
from routes.helpers import login_required, admin_required

logger = logging.getLogger(__name__)

admin_exam_q_bp = Blueprint('admin_exam_q', __name__)

# ==================== 页面路由 ====================

@admin_exam_q_bp.route('/admin/exam/<int:exam_id>/questions')
@login_required
@admin_required
def admin_exam_questions_page(exam_id):
    """题库管理页面"""
    db = get_supabase()
    
    # 获取考试基本信息（用于页面标题）
    exam_res = db.table("exams").select("title, status").eq("id", exam_id).maybe_single().execute()
    if not exam_res.data:
        return render_template('admin/exam_question.html', exam_id=exam_id, exam_title="考试不存在", readonly=False)
    
    exam = exam_res.data
    exam_title = exam.get('title', f'考试 #{exam_id}')
    
    # 检查是否为只读模式（通过URL参数或考试状态）
    readonly = request.args.get('readonly', 'false').lower() == 'true'
    
    # 如果考试状态为进行中(active)或已关闭(closed)，自动设为只读
    if exam.get('status') in ['active', 'closed']:
        readonly = True
    
    return render_template('admin/exam_question.html', 
                         exam_id=exam_id, 
                         exam_title=exam_title,
                         readonly=readonly)

# ==================== API接口 ====================

@admin_exam_q_bp.route('/api/admin/exam/<int:exam_id>/info')
@login_required
@admin_required
def get_exam_info(exam_id):
    """获取考试基本信息"""
    db = get_supabase()
    
    try:
        # 获取考试信息
        exam_res = db.table("exams").select(
            "id, title, countries, pass_score, max_retake, reviewer, quarter, total_score, questions_count, status"
        ).eq("id", exam_id).maybe_single().execute()
        
        if not exam_res.data:
            return jsonify({"error": "考试不存在"}), 404
        
        exam = exam_res.data
        
        # 解析 countries JSON
        if exam.get('countries'):
            if isinstance(exam['countries'], str):
                try:
                    exam['countries'] = json.loads(exam['countries'])
                except:
                    exam['countries'] = []
        else:
            exam['countries'] = []
        
        # 权限检查：验证用户是否有权查看此考试
        if not check_exam_permission(exam):
            return jsonify({"error": "无权查看此考试"}), 403
        
        return jsonify(exam)
        
    except Exception as e:
        logger.error(f"获取考试信息失败: {e}")
        return jsonify({"error": str(e)}), 500

# routes/admin_exam_question.py 添加

@admin_exam_q_bp.route('/api/admin/exam/<int:exam_id>', methods=['PUT'])
@login_required
@admin_required
def update_exam_info(exam_id):
    """更新考试基本信息"""
    data = request.json
    db = get_supabase()
    
    try:
        # 验证权限
        exam_res = db.table("exams").select("countries, status").eq("id", exam_id).maybe_single().execute()
        if not exam_res.data:
            return jsonify({"error": "考试不存在"}), 404
        
        if not check_exam_permission(exam_res.data):
            return jsonify({"error": "无权修改此考试"}), 403
        
        # 只允许草稿和未开始状态修改基本信息
        exam_status = exam_res.data.get('status')
        if exam_status not in ['draft', 'created']:
            return jsonify({"error": f"考试状态为 '{exam_status}'，不允许修改基本信息"}), 403
        
        # 构建更新数据
        update_data = {}
        allowed_fields = ['title', 'pass_score', 'max_retake', 'reviewer', 'quarter', 'countries']
        
        for field in allowed_fields:
            if field in data:
                if field == 'countries':
                    # 处理国家列表
                    countries = data[field]
                    if isinstance(countries, str):
                        countries = [c.strip() for c in countries.split(',') if c.strip()]
                    elif not isinstance(countries, list):
                        countries = []
                    update_data[field] = countries
                elif field in ['pass_score', 'max_retake']:
                    update_data[field] = int(data[field]) if data[field] else 0
                else:
                    update_data[field] = data[field]
        
        if not update_data:
            return jsonify({"error": "没有要更新的字段"}), 400
        
        # 执行更新
        db.table("exams").update(update_data).eq("id", exam_id).execute()
        
        logger.info(f"用户 {session.get('user_id')} 更新了考试 {exam_id} 的基本信息")
        
        return jsonify({"success": True, "message": "更新成功"})
        
    except Exception as e:
        logger.error(f"更新考试信息失败: {e}")
        return jsonify({"error": str(e)}), 500

@admin_exam_q_bp.route('/api/admin/exam/<int:exam_id>/questions')
@login_required
@admin_required
def get_exam_questions(exam_id):
    """获取考试所有题目"""
    db = get_supabase()
    
    try:
        # 先验证权限
        exam_res = db.table("exams").select("countries, status").eq("id", exam_id).maybe_single().execute()
        if not exam_res.data:
            return jsonify({"error": "考试不存在"}), 404
        
        if not check_exam_permission(exam_res.data):
            return jsonify({"error": "无权查看此考试"}), 403
        
        # 获取题目列表
        questions_res = db.table("questions").select("*").eq("exam_id", exam_id).order("num").execute()
        questions = questions_res.data or []
        
        # 处理题目数据
        processed_questions = []
        for q in questions:
            # 解析 options JSON
            if q.get('options'):
                if isinstance(q['options'], str):
                    try:
                        q['options'] = json.loads(q['options'])
                    except:
                        q['options'] = {}
                # 清理空选项
                if isinstance(q['options'], dict):
                    q['options'] = {k: v for k, v in q['options'].items() if v and v.strip()}
            else:
                q['options'] = {}
            
            # 确保必要字段存在
            q['type_label'] = get_type_label(q.get('type', 'single'))
            q['type_label_en'] = get_type_label_en(q.get('type', 'single'))
            
            processed_questions.append(q)
        
        return jsonify(processed_questions)
        
    except Exception as e:
        logger.error(f"获取题目列表失败: {e}")
        return jsonify({"error": str(e)}), 500

@admin_exam_q_bp.route('/api/admin/question/<int:question_id>', methods=['PUT'])
@login_required
@admin_required
def update_question(question_id):
    """更新单个题目"""
    data = request.json
    db = get_supabase()
    
    try:
        # 获取题目信息
        q_res = db.table("questions").select("exam_id").eq("id", question_id).maybe_single().execute()
        if not q_res.data:
            return jsonify({"error": "题目不存在"}), 404
        
        exam_id = q_res.data['exam_id']
        
        # 验证权限
        exam_res = db.table("exams").select("countries, status").eq("id", exam_id).maybe_single().execute()
        if not exam_res.data:
            return jsonify({"error": "考试不存在"}), 404
        
        # 检查考试状态：只有草稿(draft)和未开始(created)状态才能编辑
        exam_status = exam_res.data.get('status')
        if exam_status not in ['draft', 'created']:
            return jsonify({"error": f"考试状态为 '{exam_status}'，不允许编辑题目"}), 403
        
        if not check_exam_permission(exam_res.data):
            return jsonify({"error": "无权修改此考试"}), 403
        
        # 构建更新数据
        update_data = {}
        allowed_fields = ['content', 'answer', 'score', 'type', 'num']
        
        for field in allowed_fields:
            if field in data:
                update_data[field] = data[field]
        
        # 特殊处理 options 字段
        if 'options' in data:
            options = data['options']
            if isinstance(options, dict):
                # 清理空选项
                options = {k: v for k, v in options.items() if v and v.strip()}
                # 如果是字符串格式，尝试解析
                if isinstance(options, str):
                    try:
                        options = json.loads(options)
                    except:
                        options = {}
                update_data['options'] = json.dumps(options) if options else None
            else:
                update_data['options'] = None
        
        if not update_data:
            return jsonify({"error": "没有要更新的字段"}), 400
        
        # ========== 修复：移除 updated_at 字段 ==========
        # questions 表没有 updated_at 列，所以不更新这个字段
        
        # 执行更新
        db.table("questions").update(update_data).eq("id", question_id).execute()
        
        logger.info(f"用户 {session.get('user_id')} 更新了题目 {question_id}")
        
        return jsonify({"success": True, "message": "更新成功"})
        
    except Exception as e:
        error_msg = str(e)
        logger.error(f"更新题目失败: {error_msg}")
        
        # 处理 Supabase 错误
        if 'PGRST204' in error_msg:
            return jsonify({"error": "数据库字段不存在，请检查更新数据"}), 400
        
        return jsonify({"error": error_msg}), 500

@admin_exam_q_bp.route('/api/admin/question/<int:question_id>', methods=['GET'])
@login_required
@admin_required
def get_question_detail(question_id):
    """获取单个题目详情"""
    db = get_supabase()
    
    try:
        q_res = db.table("questions").select("*").eq("id", question_id).maybe_single().execute()
        if not q_res.data:
            return jsonify({"error": "题目不存在"}), 404
        
        q = q_res.data
        
        # 解析 options
        if q.get('options'):
            if isinstance(q['options'], str):
                try:
                    q['options'] = json.loads(q['options'])
                except:
                    q['options'] = {}
        
        return jsonify(q)
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ==================== 辅助函数 ====================

def check_exam_permission(exam):
    """检查用户是否有权限访问此考试"""
    # 开发者不受限
    if is_developer():
        return True
    
    # 获取用户权限国家
    allowed_countries = get_admin_allowed_countries()
    
    # 如果没有权限限制，默认有权限
    if allowed_countries is None:
        return True
    
    # 如果权限列表为空，无权限
    if not allowed_countries:
        return False
    
    # 获取考试国家列表
    exam_countries = exam.get('countries', [])
    if isinstance(exam_countries, str):
        try:
            exam_countries = json.loads(exam_countries)
        except:
            exam_countries = []
    
    # 检查是否有交集
    return any(c in allowed_countries for c in exam_countries)

def get_type_label(type_code):
    """获取题型中文标签"""
    labels = {
        'single': '单选题',
        'multi': '多选题',
        'judge': '判断题'
    }
    return labels.get(type_code, type_code)

def get_type_label_en(type_code):
    """获取题型英文标签"""
    labels = {
        'single': 'Single Choice',
        'multi': 'Multiple Choice',
        'judge': 'True/False'
    }
    return labels.get(type_code, type_code)

def get_type_icon(type_code):
    """获取题型图标"""
    icons = {
        'single': 'fa-circle',
        'multi': 'fa-check-square',
        'judge': 'fa-gavel'
    }
    return icons.get(type_code, 'fa-question-circle')

def update_question_count(exam_id):
    """更新考试的题目计数"""
    db = get_supabase()
    try:
        # 统计题目数量
        count_res = db.table("questions").select("id", count="exact").eq("exam_id", exam_id).execute()
        count = count_res.count if hasattr(count_res, 'count') else 0
        
        # 更新 exams 表
        db.table("exams").update({"questions_count": count}).eq("id", exam_id).execute()
        logger.info(f"更新考试 {exam_id} 的题目计数为 {count}")
        return True
    except Exception as e:
        logger.error(f"更新题目计数失败: {e}")
        return False
