# routes/api_privacy.py
"""
隐私声明 API（用户端）
"""
import logging
from datetime import datetime, timezone  # ✅ 添加导入
from flask import Blueprint, request, jsonify, session
from services.db import get_supabase, get_supabase_admin
from routes.helpers import login_required
from services.privacy import PrivacyService

logger = logging.getLogger(__name__)

privacy_api_bp = Blueprint('privacy_api', __name__)


@privacy_api_bp.route('/api/privacy/status')
@login_required
def api_privacy_status():
    """
    获取用户隐私声明状态
    GET /api/privacy/status
    Response: {
        "needs_acknowledgment": bool,
        "agreement": {...} or null
    }
    """
    user_id = session.get('user_id')
    result = PrivacyService.check_user_needs_acknowledgment(user_id)
    return jsonify(result)


@privacy_api_bp.route('/api/privacy/agree', methods=['POST'])
@login_required
def api_privacy_agree():
    """
    用户确认隐私声明
    POST /api/privacy/agree
    Body: { "agreement_id": 1 }
    """
    user_id = session.get('user_id')
    data = request.json
    agreement_id = data.get('agreement_id')
    
    if not agreement_id:
        return jsonify({"success": False, "message": "缺少协议ID"}), 400
    
    agreement = PrivacyService.get_agreement_by_id(agreement_id)
    if not agreement:
        return jsonify({"success": False, "message": "协议不存在"}), 404
    
    if not agreement.get('is_active'):
        return jsonify({"success": False, "message": "该协议版本已过期"}), 400
    
    try:
        result = PrivacyService.acknowledge_agreement(
            user_id=user_id,
            agreement_id=agreement_id,
            ip_address=request.remote_addr,
            user_agent=request.headers.get('User-Agent')
        )
        return jsonify(result)
    except Exception as e:
        logger.error(f"确认协议失败: {e}")
        return jsonify({"success": False, "message": str(e)}), 500


@privacy_api_bp.route('/api/privacy/current')
def api_privacy_current():
    """获取当前有效的隐私声明内容（无需登录）"""
    agreement = PrivacyService.get_active_agreement()
    if agreement:
        return jsonify({
            "success": True,
            "data": {
                "id": agreement['id'],
                "version": agreement['version'],
                "title": agreement['title'],
                "content": agreement['content'],
                "title_zh": agreement.get('title_zh', agreement.get('title', '')),
                "title_en": agreement.get('title_en', agreement.get('title', '')),
                "content_zh": agreement.get('content_zh', agreement.get('content', '')),
                "content_en": agreement.get('content_en', agreement.get('content', '')),
                "created_at": agreement.get('created_at'),
                "updated_at": agreement.get('updated_at')
            }
        })
    return jsonify({"success": False, "message": "暂无隐私声明"}), 404

# routes/api_privacy.py - 修复 get_current_user 函数

@privacy_api_bp.route('/api/auth/me')
@login_required
def get_current_user():
    """
    获取当前用户信息（包含隐私签署状态）
    GET /api/auth/me
    """
    try:
        db = get_supabase()
        user_id = session.get('user_id')
        
        if not user_id:
            return jsonify({"success": False, "message": "未登录"}), 401
        
        # 1. 获取用户基本信息（包含隐私字段）
        res = db.table("users").select(
            "id, name_en, name_cn, email, country, role, user_status, "
            "is_resign, is_partner, wh_id, wh_name_en, company, department, employee_id, "
            "privacy_acknowledged_at, privacy_agreement_id"
        ).eq("id", user_id).maybe_single().execute()
        
        if not res or not res.data:
            return jsonify({"success": False, "message": "用户不存在"}), 404
        
        user = res.data
        
        # ✅ 如果 users 表中没有签署记录，才从 user_agreement_acks 查询
        # 如果 users 表有值，直接使用，不查询也不覆盖
        if not user.get('privacy_acknowledged_at'):
            ack_res = db.table("user_agreement_acks")\
                .select("acknowledged_at, agreement_id")\
                .eq("user_id", user_id)\
                .order("acknowledged_at", desc=True)\
                .limit(1)\
                .execute()
            
            if ack_res.data and len(ack_res.data) > 0:
                user['privacy_acknowledged_at'] = ack_res.data[0].get('acknowledged_at')
                user['privacy_agreement_id'] = ack_res.data[0].get('agreement_id')
            # ✅ 如果 user_agreement_acks 也没有，保持 None
            # 不要在这里设置 None，因为已经是 None 了
        
        # ✅ 如果 users 表已经有值，直接使用，不做任何覆盖
        # 不要执行 else 分支
        
        return jsonify({"success": True, "user": user})
        
    except Exception as e:
        logger.error(f"获取用户信息失败: {e}")
        return jsonify({"success": False, "message": str(e)}), 500
