# routes/admin_message.py
"""
管理员消息盒子 API
"""
import logging
import httpx
from functools import lru_cache
from datetime import datetime, timedelta
from flask import Blueprint, request, jsonify, session
from routes.helpers import login_required, admin_required
from services.db import get_supabase, get_supabase_admin
from utils.permissions import is_developer
from utils.manage_messages import (
    get_unread_message_count,
    get_recent_messages,
    mark_message_read,
    mark_all_messages_read,
    log_admin_message
)

message_bp = Blueprint('admin_message', __name__, url_prefix='/api/admin')

logger = logging.getLogger(__name__)

# 简单缓存
_unread_count_cache = {
    'count': 0,
    'timestamp': None,
    'ttl_seconds': 30
}


@message_bp.route('/messages')
@login_required
@admin_required
def get_admin_messages():
    """
    获取管理员消息列表
    
    Query Parameters:
        page: 页码（默认 1）
        per_page: 每页数量（默认 20）
        unread_only: 是否只返回未读（默认 false）
        category: 按分类过滤
        level: 按级别过滤
    """
    db = get_supabase()
    
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 20, type=int)
    unread_only = request.args.get('unread_only', 'false').lower() == 'true'
    category = request.args.get('category', '')
    level = request.args.get('level', '')
    
    try:
        # 构建查询
        admin_db = get_supabase_admin()
        query = admin_db.table("admin_messages").select("*").order("created_at", desc=True)
        
        if unread_only:
            query = query.eq("is_read", False)
        if category:
            query = query.eq("category", category)
        if level:
            query = query.eq("level", level)
        
        # 获取总数
        count_result = query.execute()
        total = len(count_result.data or [])
        
        # 分页
        start = (page - 1) * per_page
        end = start + per_page - 1
        result = query.range(start, end).execute()
        
        # 获取创建人姓名（如果有 created_by）
        messages = result.data or []
        user_ids = [m.get('created_by') for m in messages if m.get('created_by')]
        user_names = {}
        
        if user_ids:
            users_res = db.table("users").select("id, name_en, name_cn").in_("id", user_ids).execute()
            for u in (users_res.data or []):
                user_names[u['id']] = u.get('name_cn') or u.get('name_en') or u.get('id')
        
        # 附加用户姓名
        for m in messages:
            m['created_by_name'] = user_names.get(m.get('created_by'), '系统')
            # 格式化时间
            if m.get('created_at'):
                try:
                    from datetime import datetime
                    dt = datetime.fromisoformat(m['created_at'].replace('Z', '+00:00'))
                    m['created_at_local'] = dt.strftime('%Y-%m-%d %H:%M')
                except:
                    m['created_at_local'] = m['created_at']
        
        return jsonify({
            "success": True,
            "data": messages,
            "total": total,
            "page": page,
            "per_page": per_page
        })
        
    except Exception as e:
        logger.error(f"获取消息列表失败: {e}")
        return jsonify({"success": False, "message": str(e)}), 500

@message_bp.route('/messages/unread_count')
@login_required
@admin_required
def get_unread_count():
    """获取未读消息数量（带超时保护）"""
    global _unread_count_cache
    
    # 检查缓存是否有效
    now = datetime.now()
    if (_unread_count_cache['timestamp'] is not None and 
        (now - _unread_count_cache['timestamp']).total_seconds() < _unread_count_cache['ttl_seconds']):
        return jsonify({"success": True, "count": _unread_count_cache['count']})
    
    try:
        admin_db = get_supabase_admin()
        
        result = admin_db.table("admin_messages").select("id", count="exact").eq("is_read", False).execute()

        # 安全获取 count
        count = result.count if hasattr(result, 'count') and result.count is not None else 0

        # 更新缓存
        _unread_count_cache['count'] = count
        _unread_count_cache['timestamp'] = now
        
        return jsonify({"success": True, "count": count})

    except Exception as e:
        error_msg = str(e)
        if any(keyword in error_msg.lower() for keyword in ['timeout', 'timed out', 'connect']):
            logger.warning(f"获取未读数量超时，使用缓存值: {e}")
            # 返回缓存值（即使过期）
            return jsonify({"success": True, "count": _unread_count_cache['count']})
        else:
            logger.error(f"获取未读数量失败: {e}")
            return jsonify({"success": True, "count": 0})

@message_bp.route('/messages/<int:message_id>/read', methods=['POST'])
@login_required
@admin_required
def mark_read(message_id):
    """标记单条消息为已读"""
    try:
        success = mark_message_read(message_id)
        if success:
            return jsonify({"success": True, "message": "已标记为已读"})
        else:
            return jsonify({"success": False, "message": "消息不存在"}), 404
    except Exception as e:
        logger.error(f"标记消息已读失败: {e}")
        return jsonify({"success": False, "message": str(e)}), 500


@message_bp.route('/messages/read_all', methods=['POST'])
@login_required
@admin_required
def mark_all_read():
    """标记所有消息为已读"""
    try:
        count = mark_all_messages_read()
        return jsonify({"success": True, "count": count, "message": f"已标记 {count} 条消息为已读"})
    except Exception as e:
        logger.error(f"标记全部已读失败: {e}")
        return jsonify({"success": False, "message": str(e)}), 500


@message_bp.route('/messages/<int:message_id>', methods=['DELETE'])
@login_required
@admin_required
def delete_message(message_id):
    """删除单条消息（仅超管/开发者可用）"""
    # 添加调试日志
    print(f"🔍🔍🔍 DELETE 路由被调用了！message_id={message_id}")
    logger.info(f"🔍 DELETE 路由被调用了！message_id={message_id}")

    if not is_developer() and session.get('role') != 'super_admin':
        return jsonify({"success": False, "message": "权限不足"}), 403
    
    db = get_supabase_admin()
    try:
        # 添加调试日志
        logger.info(f"🔍 尝试删除消息 ID: {message_id}")

        # 先检查消息是否存在
        check_res = db.table("admin_messages").select("id").eq("id", message_id).execute()
        # 打印查询结果
        logger.info(f"🔍 check_res.data: {check_res.data}")
        logger.info(f"🔍 check_res 完整: {check_res}")
        
        if not check_res.data:
            logger.warning(f"❌ 消息 ID {message_id} 不存在")
            return jsonify({"success": False, "message": "消息不存在"}), 404
        
        result = db.table("admin_messages").delete().eq("id", message_id).execute()
        if result.data:
            logger.info(f"消息 {message_id} 已删除，操作人: {session.get('user_id')}")
            return jsonify({"success": True, "message": "消息已删除"})
        else:
            return jsonify({"success": False, "message": "消息不存在"}), 404
    except Exception as e:
        logger.error(f"删除消息失败: {e}")
        return jsonify({"success": False, "message": str(e)}), 500

@message_bp.route('/messages/batch_delete', methods=['POST'])
@login_required
@admin_required
def batch_delete_messages():
    """批量删除消息（仅超管/开发者可用）"""
    if not is_developer() and session.get('role') != 'super_admin':
        return jsonify({"success": False, "message": "权限不足"}), 403
    
    data = request.json
    message_ids = data.get('ids', [])
    
    if not message_ids:
        return jsonify({"success": False, "message": "请选择要删除的消息"}), 400
    
    db = get_supabase_admin()
    
    try:
        # 批量删除（一条 SQL 语句）
        result = db.table("admin_messages").delete().in_("id", message_ids).execute()
        
        deleted_count = len(result.data) if result.data else 0
        
        logger.info(f"批量删除消息: {deleted_count} 条，操作人: {session.get('user_id')}")
        
        return jsonify({
            "success": True,
            "deleted_count": deleted_count,
            "message": "batch_delete_success",
            "params": [deleted_count]
        })
    except Exception as e:
        logger.error(f"批量删除消息失败: {e}")
        return jsonify({"success": False, "message": str(e)}), 500
