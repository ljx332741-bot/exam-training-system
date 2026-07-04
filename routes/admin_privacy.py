# routes/admin_privacy.py
"""
隐私声明管理（管理员端）
"""
import logging
from datetime import datetime, timezone
from flask import Blueprint, request, jsonify, session, render_template

from routes.helpers import login_required, admin_required
from services.db import get_supabase_admin
from services.privacy import PrivacyService

logger = logging.getLogger(__name__)

admin_privacy_bp = Blueprint('admin_privacy', __name__)


@admin_privacy_bp.route('/admin/privacy')
@login_required
@admin_required
def admin_privacy_page():
    """隐私声明管理页面"""
    return render_template('admin/privacy_management.html')


@admin_privacy_bp.route('/api/admin/privacy/agreements', methods=['GET'])
@login_required
@admin_required
def api_admin_get_agreements():
    """获取所有隐私声明版本"""
    try:
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 20, type=int)
        offset = (page - 1) * per_page
        
        result = PrivacyService.get_all_agreements(limit=per_page, offset=offset)
        return jsonify({
            "success": True,
            "data": result.get("data", []),
            "total": result.get("total", 0),
            "page": page,
            "per_page": per_page
        })
    except Exception as e:
        logger.error(f"获取隐私声明列表失败: {e}", exc_info=True)
        return jsonify({
            "success": False,
            "message": str(e),
            "data": [],
            "total": 0
        }), 500


@admin_privacy_bp.route('/api/admin/privacy/agreements/<int:agreement_id>', methods=['GET'])
@login_required
@admin_required
def api_admin_get_agreement(agreement_id):
    """获取单个隐私声明版本详情"""
    print(f"🔍 调用 api_admin_get_agreement, agreement_id={agreement_id}")
    try:
        from services.db import get_supabase_admin
        db = get_supabase_admin()
        
        # ✅ 使用 admin 客户端
        res = db.table("privacy_agreements").select("*").eq("id", agreement_id).execute()
        print(f"   admin 查询结果: {res.data if res and hasattr(res, 'data') else 'None'}")
        
        if res and hasattr(res, 'data') and res.data and len(res.data) > 0:
            return jsonify({"success": True, "data": res.data[0]})
        
        return jsonify({"success": False, "message": "协议不存在"}), 404
    except Exception as e:
        import traceback
        print(f"❌ 错误: {traceback.format_exc()}")
        logger.error(f"获取协议失败: {e}")
        return jsonify({"success": False, "message": str(e)}), 500


@admin_privacy_bp.route('/api/admin/privacy/agreements', methods=['POST'])
@login_required
@admin_required
def api_admin_create_agreement():
    """创建新版隐私声明"""
    data = request.json
    user_id = session.get('user_id')
    
    version = data.get('version')
    title = data.get('title')
    content = data.get('content')
    changelog = data.get('changelog', '')
    
    if not all([version, title, content]):
        return jsonify({"success": False, "message": "请填写完整信息"}), 400
    
    try:
        result = PrivacyService.create_agreement(
            version=version,
            title=title,
            content=content,
            created_by=user_id,
            changelog=changelog
        )
        return jsonify({"success": True, "data": result})
    except Exception as e:
        logger.error(f"创建隐私声明失败: {e}")
        return jsonify({"success": False, "message": str(e)}), 500


@admin_privacy_bp.route('/api/admin/privacy/agreements/<int:agreement_id>', methods=['PUT'])
@login_required
@admin_required
def api_admin_update_agreement(agreement_id):
    """更新隐私声明（不创建新版本）"""
    data = request.json
    user_id = session.get('user_id')
    
    title = data.get('title')
    content = data.get('content')
    changelog = data.get('changelog', '')
    
    if not all([title, content]):
        return jsonify({"success": False, "message": "请填写完整信息"}), 400
    
    try:
        result = PrivacyService.update_agreement(
            agreement_id=agreement_id,
            title=title,
            content=content,
            updated_by=user_id,
            changelog=changelog
        )
        return jsonify({"success": True, "data": result})
    except Exception as e:
        logger.error(f"更新隐私声明失败: {e}")
        return jsonify({"success": False, "message": str(e)}), 500


@admin_privacy_bp.route('/api/admin/privacy/agreements/<int:agreement_id>/activate', methods=['POST'])
@login_required
@admin_required
def api_admin_activate_agreement(agreement_id):
    """激活指定版本的隐私声明（所有用户需要重新确认）"""
    db = get_supabase_admin()
    
    agreement = PrivacyService.get_agreement_by_id(agreement_id)
    if not agreement:
        return jsonify({"success": False, "message": "协议不存在"}), 404
    
    try:
        # 将所有协议设为非活跃
        db.table("privacy_agreements") \
            .update({"is_active": False}) \
            .execute()
        
        # 激活指定协议
        now = datetime.now(timezone.utc).isoformat()
        db.table("privacy_agreements") \
            .update({
                "is_active": True,
                "updated_at": now
            }) \
            .eq("id", agreement_id) \
            .execute()
        
        logger.info(f"隐私声明版本激活: ID={agreement_id}")
        return jsonify({"success": True, "message": "已激活"})
    except Exception as e:
        logger.error(f"激活协议失败: {e}")
        return jsonify({"success": False, "message": str(e)}), 500


@admin_privacy_bp.route('/api/admin/privacy/agreements/<int:agreement_id>', methods=['DELETE'])
@login_required
@admin_required
def api_admin_delete_agreement(agreement_id):
    """删除隐私声明版本（仅限非活跃版本）"""
    db = get_supabase_admin()
    
    agreement = PrivacyService.get_agreement_by_id(agreement_id)
    if not agreement:
        return jsonify({"success": False, "message": "协议不存在"}), 404
    
    if agreement.get('is_active'):
        return jsonify({"success": False, "message": "不能删除当前活跃版本"}), 400
    
    try:
        db.table("privacy_agreements").delete().eq("id", agreement_id).execute()
        return jsonify({"success": True, "message": "已删除"})
    except Exception as e:
        logger.error(f"删除协议失败: {e}")
        return jsonify({"success": False, "message": str(e)}), 500


@admin_privacy_bp.route('/api/admin/privacy/stats')
@login_required
@admin_required
def api_admin_privacy_stats():
    """获取隐私声明统计信息"""
    db = get_supabase_admin()
    
    # 总用户数
    try:
        user_res = db.table("users").select("id", count="exact").is_("deleted_at", "null").execute()
        total_users = user_res.count if user_res and hasattr(user_res, 'count') else 0
    except:
        total_users = 0
    
    # 当前活跃版本
    active = PrivacyService.get_active_agreement()
    
    # 已确认当前版本的用户数
    confirmed = 0
    if active:
        try:
            ack_res = db.table("user_agreement_acks") \
                .select("user_id", count="exact") \
                .eq("agreement_id", active['id']) \
                .execute()
            confirmed = ack_res.count if ack_res and hasattr(ack_res, 'count') else 0
        except:
            confirmed = 0
    
    return jsonify({
        "success": True,
        "data": {
            "total_users": total_users,
            "confirmed_users": confirmed,
            "unconfirmed_users": total_users - confirmed,
            "active_version": active['version'] if active else None,
            "confirmation_rate": round(confirmed / total_users * 100, 1) if total_users > 0 else 0
        }
    })