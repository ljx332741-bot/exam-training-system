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
    try:
        from services.db import get_supabase_admin
        db = get_supabase_admin()
        
        res = db.table("privacy_agreements").select("*").eq("id", agreement_id).execute()
        
        if res and hasattr(res, 'data') and res.data and len(res.data) > 0:
            return jsonify({"success": True, "data": res.data[0]})
        
        return jsonify({"success": False, "message": "jsonify_agreement_does_not_exist", "params": []}), 404
    except Exception as e:
        import traceback
        print(f"❌ 错误: {traceback.format_exc()}")
        logger.error(f"获取协议失败: {e}", exc_info=True)
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
        return jsonify({"success": False, "message": "jsonify_fillin_info_completely", "params": []}), 400
    
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
        return jsonify({"success": False, "message": "jsonify_fillin_info_completely", "params": []}), 400
    
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
        return jsonify({"success": False, "message": "jsonify_ agreement_does_not_exist", "params": []}), 404
    
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
        return jsonify({"success": False, "message": "jsonify_ agreement_does_not_exist", "params": []}), 404
    
    if agreement.get('is_active'):
        return jsonify({"success": False, "message": "jsonify_active_version_cannot_delete", "params": []}), 400
    
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

@admin_privacy_bp.route('/api/admin/privacy/users', methods=['GET'])
@login_required
@admin_required
def api_admin_privacy_users():
    """
    获取隐私声明相关的用户清单
    GET /api/admin/privacy/users?agreement_id=1&type=confirmed&per_page=10000
    """
    from utils.permissions import filter_users_by_permission, get_admin_allowed_countries

    agreement_id = request.args.get('agreement_id', type=int)
    list_type = request.args.get('type', 'total')
    per_page = request.args.get('per_page', 10000, type=int)

    if not agreement_id:
        return jsonify({"success": False, "message": "缺少协议ID"}), 400

    db = get_supabase_admin()
    allowed_countries = get_admin_allowed_countries()
    current_user_id = session.get('user_id')

    # 1. 获取所有用户（未删除）
    user_res = db.table("users").select("*").is_("deleted_at", "null").execute()
    all_users = user_res.data or []

    # 2. 获取该协议的确认记录
    ack_res = db.table("user_agreement_acks") \
        .select("user_id, agreement_id, acknowledged_at") \
        .eq("agreement_id", agreement_id) \
        .execute()
    ack_map = {item['user_id']: item for item in (ack_res.data or [])}

    # 3. 获取协议版本信息
    agreement = PrivacyService.get_agreement_by_id(agreement_id)
    agreement_version = agreement.get('version') if agreement else None

    # 4. 根据类型筛选用户
    result_users = []
    for user in all_users:
        user_id = user['id']
        is_confirmed = user_id in ack_map

        if list_type == 'confirmed' and not is_confirmed:
            continue
        if list_type == 'unconfirmed' and is_confirmed:
            continue
        # rate 类型：全部显示（前端会展示确认状态）

        # 构建返回数据
        user_data = {
            'id': user_id,
            'name_en': user.get('name_en', ''),
            'email': user.get('email', ''),
            'country': user.get('country', ''),
            'user_status': user.get('user_status', ''),
            'is_resign': user.get('is_resign', False),
            'is_rehire': user.get('is_rehire', False),
            'rehire_at': user.get('rehire_at'),
            'resigned_at': user.get('resigned_at'),
            'last_login_at': user.get('last_login_at'),
            'created_at': user.get('created_at'),
            'agreement_version': agreement_version,
            'acknowledged_at': ack_map[user_id]['acknowledged_at'] if is_confirmed else None
        }
        result_users.append(user_data)

    # 5. 权限过滤（与用户列表一致）
    filtered_users = filter_users_by_permission(result_users, allowed_countries, current_user_id)

    # 6. 排序：已确认的按确认时间倒序，未确认的按创建时间倒序
    confirmed_list = [u for u in filtered_users if u['acknowledged_at']]
    unconfirmed_list = [u for u in filtered_users if not u['acknowledged_at']]
    confirmed_list.sort(key=lambda x: x['acknowledged_at'], reverse=True)
    unconfirmed_list.sort(key=lambda x: x['created_at'] or '', reverse=True)

    if list_type == 'confirmed':
        sorted_users = confirmed_list
    elif list_type == 'unconfirmed':
        sorted_users = unconfirmed_list
    else:  # total 或 rate
        sorted_users = confirmed_list + unconfirmed_list

    # 分页
    total = len(sorted_users)
    if per_page and per_page > 0:
        # 简单分页（实际上前端自己做分页）
        pass

    return jsonify({
        "success": True,
        "data": sorted_users,
        "total": total,
        "agreement_version": agreement_version
    })

# routes/admin_privacy.py - 在文件末尾添加

@admin_privacy_bp.route('/api/admin/privacy/sync_user_status', methods=['POST'])
@login_required
@admin_required
def api_admin_sync_user_privacy_status():
    """
    管理员：手动同步所有用户的隐私签署状态到 users 表
    用于修复历史数据不一致问题
    """
    try:
        db = get_supabase_admin()
        
        # 获取所有有签署记录的用户
        ack_res = db.table("user_agreement_acks")\
            .select("user_id, agreement_id, acknowledged_at")\
            .execute()
        
        if not ack_res.data:
            return jsonify({
                "success": True, 
                "message": "没有签署记录需要同步",
                "synced_count": 0
            })
        
        # 按用户分组，取最新的签署记录
        user_latest = {}
        for record in ack_res.data:
            user_id = record['user_id']
            if user_id not in user_latest:
                user_latest[user_id] = record
            else:
                if record['acknowledged_at'] > user_latest[user_id]['acknowledged_at']:
                    user_latest[user_id] = record
        
        # 批量更新 users 表
        synced_count = 0
        for user_id, record in user_latest.items():
            result = db.table("users").update({
                "privacy_acknowledged_at": record['acknowledged_at'],
                "privacy_agreement_id": record['agreement_id']
            }).eq("id", user_id).execute()
            
            if result.data:
                synced_count += 1
        
        logger.info(f"管理员同步隐私状态: 更新了 {synced_count} 个用户")
        
        return jsonify({
            "success": True,
            "message": f"成功同步 {synced_count} 个用户的签署状态",
            "synced_count": synced_count
        })
        
    except Exception as e:
        logger.error(f"同步用户隐私状态失败: {e}")
        return jsonify({"success": False, "message": str(e)}), 500


@admin_privacy_bp.route('/api/admin/privacy/user_status/<user_id>', methods=['GET'])
@login_required
@admin_required
def api_admin_get_user_privacy_status(user_id):
    """
    管理员：查看指定用户的隐私签署状态
    """
    try:
        db = get_supabase_admin()
        
        # 获取用户基本信息
        user_res = db.table("users").select(
            "id, name_en, email, privacy_acknowledged_at, privacy_agreement_id"
        ).eq("id", user_id).maybe_single().execute()
        
        if not user_res.data:
            return jsonify({"success": False, "message": "用户不存在"}), 404
        
        user = user_res.data
        
        # 获取签署历史
        ack_res = db.table("user_agreement_acks")\
            .select("agreement_id, acknowledged_at, ip_address, user_agent")\
            .eq("user_id", user_id)\
            .order("acknowledged_at", desc=True)\
            .execute()
        
        return jsonify({
            "success": True,
            "data": {
                "user": user,
                "history": ack_res.data or [],
                "is_signed": user.get('privacy_acknowledged_at') is not None
            }
        })
        
    except Exception as e:
        logger.error(f"获取用户隐私状态失败: {e}")
        return jsonify({"success": False, "message": str(e)}), 500


@admin_privacy_bp.route('/api/admin/privacy/fix_user/<user_id>', methods=['POST'])
@login_required
@admin_required
def api_admin_fix_user_privacy(user_id):
    """
    管理员：修复单个用户的隐私签署状态
    从 user_agreement_acks 同步到 users 表
    """
    try:
        db = get_supabase_admin()
        
        # 获取该用户最新的签署记录
        ack_res = db.table("user_agreement_acks")\
            .select("agreement_id, acknowledged_at")\
            .eq("user_id", user_id)\
            .order("acknowledged_at", desc=True)\
            .limit(1)\
            .execute()
        
        if not ack_res.data:
            return jsonify({
                "success": False, 
                "message": "该用户没有签署记录"
            }), 404
        
        latest = ack_res.data[0]
        
        # 更新 users 表
        result = db.table("users").update({
            "privacy_acknowledged_at": latest['acknowledged_at'],
            "privacy_agreement_id": latest['agreement_id']
        }).eq("id", user_id).execute()
        
        logger.info(f"管理员修复用户 {user_id} 的隐私状态: 协议ID={latest['agreement_id']}")
        
        return jsonify({
            "success": True,
            "message": "用户状态已修复",
            "data": {
                "privacy_acknowledged_at": latest['acknowledged_at'],
                "privacy_agreement_id": latest['agreement_id']
            }
        })
        
    except Exception as e:
        logger.error(f"修复用户隐私状态失败: {e}")
        return jsonify({"success": False, "message": str(e)}), 500
