# routes/admin_cms.py - 确保蓝图名称正确

import logging
from datetime import datetime, timezone
from flask import Blueprint, request, jsonify, session, render_template, redirect, url_for
from routes.helpers import login_required, admin_required
from services.db import get_supabase, get_supabase_admin
from utils.permissions import is_developer, get_admin_allowed_countries

logger = logging.getLogger(__name__)

# ⚠️ 关键：蓝图名称必须与 __init__.py 中注册的一致
# 在 __init__.py 中：app.register_blueprint(admin_cms_bp)
admin_cms_bp = Blueprint('admin_cms', __name__)


# ============================================================
# 辅助函数：安全获取数据
# ============================================================
def safe_get_data(result):
    if result is None:
        return None
    if hasattr(result, 'data'):
        return result.data
    if isinstance(result, dict) and 'data' in result:
        return result['data']
    return None


# ============================================================
# 1. 获取 CMS 页面内容（公开接口，无需登录）
# ============================================================
@admin_cms_bp.route('/api/cms/page/<page_key>', methods=['GET'])
def get_cms_page(page_key):
    """获取指定页面的内容（公开接口）"""
    db = get_supabase_admin()
    
    try:
        res = db.table("cms_pages") \
            .select("*") \
            .eq("page_key", page_key) \
            .eq("is_active", True) \
            .execute()
        
        data = safe_get_data(res)
        
        if not data or len(data) == 0:
            return jsonify({
                "success": False,
                "message": "页面不存在"
            }), 404
        
        return jsonify({
            "success": True,
            "data": data[0]
        })
    except Exception as e:
        logger.error(f"获取CMS页面失败: {e}")
        return jsonify({"success": False, "message": str(e)}), 500


# ============================================================
# 2. 获取所有 CMS 页面列表（管理员）
# ============================================================
@admin_cms_bp.route('/api/admin/cms/pages', methods=['GET'])
@login_required
@admin_required
def get_cms_pages():
    """获取所有CMS页面列表（管理员）"""
    db = get_supabase_admin()
    
    if not is_developer() and session.get('role') != 'super_admin':
        return jsonify({"success": False, "message": "权限不足"}), 403
    
    try:
        res = db.table("cms_pages") \
            .select("*") \
            .order("created_at", desc=True) \
            .execute()
        
        data = safe_get_data(res)
        
        return jsonify({
            "success": True,
            "data": data or []
        })
    except Exception as e:
        logger.error(f"获取CMS页面列表失败: {e}")
        return jsonify({"success": False, "message": str(e)}), 500


# ============================================================
# 3. 获取单个页面详情（管理员）
# ============================================================
@admin_cms_bp.route('/api/admin/cms/page/<page_key>/detail', methods=['GET'])
@login_required
@admin_required
def get_cms_page_detail(page_key):
    """获取单个CMS页面详情（管理员）"""
    db = get_supabase_admin()
    
    if not is_developer() and session.get('role') not in ['super_admin', 'admin']:
        return jsonify({"success": False, "message": "权限不足"}), 403
    
    try:
        res = db.table("cms_pages") \
            .select("*") \
            .eq("page_key", page_key) \
            .execute()
        
        data = safe_get_data(res)
        
        if not data or len(data) == 0:
            return jsonify({
                "success": False,
                "message": "页面不存在"
            }), 404
        
        return jsonify({
            "success": True,
            "data": data[0]
        })
    except Exception as e:
        logger.error(f"获取CMS页面详情失败: {e}")
        return jsonify({"success": False, "message": str(e)}), 500


# ============================================================
# 4. 更新 CMS 页面内容（管理员）
# ============================================================
@admin_cms_bp.route('/api/admin/cms/page/<page_key>', methods=['PUT'])
@login_required
@admin_required
def update_cms_page(page_key):
    """更新CMS页面内容（管理员）"""
    db = get_supabase_admin()
    data = request.json
    user_id = session.get('user_id')
    
    current_role = session.get('role')
    is_dev = is_developer()
    
    if not is_dev and current_role != 'super_admin':
        if page_key != 'info_security':
            return jsonify({"success": False, "message": "权限不足，仅可编辑信息安全制度"}), 403
        
        allowed_countries = get_admin_allowed_countries()
        if allowed_countries is not None and not allowed_countries:
            return jsonify({"success": False, "message": "权限不足"}), 403
    
    required_fields = ['title_zh', 'title_en', 'content_zh', 'content_en']
    for field in required_fields:
        if field not in data or not data[field]:
            return jsonify({
                "success": False,
                "message": f"缺少必填字段: {field}"
            }), 400
    
    try:
        existing_res = db.table("cms_pages") \
            .select("id") \
            .eq("page_key", page_key) \
            .execute()
        
        existing_data = safe_get_data(existing_res)
        now = datetime.now(timezone.utc).isoformat()
        
        if existing_data and len(existing_data) > 0:
            existing_id = existing_data[0]['id']
            update_data = {
                "title_zh": data['title_zh'],
                "title_en": data['title_en'],
                "content_zh": data['content_zh'],
                "content_en": data['content_en'],
                "updated_at": now,
                "updated_by": user_id,
                "version": data.get('version', '1.0')
            }
            
            if 'is_active' in data:
                update_data['is_active'] = data['is_active'] in [True, 'true', 'True']
            
            result = db.table("cms_pages") \
                .update(update_data) \
                .eq("id", existing_id) \
                .execute()
            
            result_data = safe_get_data(result)
            
            logger.info(f"CMS页面已更新: {page_key}, 操作人: {user_id}")
            
            return jsonify({
                "success": True,
                "message": "更新成功",
                "data": result_data[0] if result_data and len(result_data) > 0 else None
            })
        else:
            insert_data = {
                "page_key": page_key,
                "title_zh": data['title_zh'],
                "title_en": data['title_en'],
                "content_zh": data['content_zh'],
                "content_en": data['content_en'],
                "version": data.get('version', '1.0'),
                "is_active": data.get('is_active', True) in [True, 'true', 'True'],
                "created_at": now,
                "created_by": user_id,
                "updated_at": now,
                "updated_by": user_id
            }
            
            result = db.table("cms_pages") \
                .insert(insert_data) \
                .execute()
            
            result_data = safe_get_data(result)
            
            logger.info(f"CMS页面已创建: {page_key}, 操作人: {user_id}")
            
            return jsonify({
                "success": True,
                "message": "创建成功",
                "data": result_data[0] if result_data and len(result_data) > 0 else None
            })
            
    except Exception as e:
        logger.error(f"更新CMS页面失败: {e}")
        return jsonify({"success": False, "message": str(e)}), 500


# ============================================================
# 5. 管理员 CMS 管理页面
# ============================================================
@admin_cms_bp.route('/admin/cms')
@login_required
@admin_required
def admin_cms_page():
    """CMS管理页面"""
    if not is_developer() and session.get('role') not in ['super_admin', 'admin']:
        return redirect(url_for('admin_dashboard'))
    
    return render_template('admin/cms_management.html')

# ============================================================
# 3. 测试路由
# ============================================================
@admin_cms_bp.route('/api/cms/health', methods=['GET'])
def cms_health():
    """CMS 健康检查"""
    return jsonify({
        "success": True,
        "message": "CMS API is working!",
        "timestamp": datetime.now(timezone.utc).isoformat()
    })