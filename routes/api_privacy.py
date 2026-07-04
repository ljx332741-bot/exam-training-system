# routes/api_privacy.py
"""
隐私声明 API（用户端）
"""
import logging
from flask import Blueprint, request, jsonify, session

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
                "created_at": agreement.get('created_at')
            }
        })
    return jsonify({"success": False, "message": "暂无隐私声明"}), 404