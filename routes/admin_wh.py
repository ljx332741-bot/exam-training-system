# routes/admin_wh.py
import logging
import json
import re
import uuid
import openpyxl
from . import admin_wh_bp
from datetime import datetime, timezone
from flask import request, jsonify, render_template, send_file, session
from services.db import get_supabase, get_supabase_admin
from routes.helpers import login_required, admin_required
from utils.import_helper import generate_import_template, validate_country_and_wh_id, parse_excel_rows, format_import_result, extract_country_from_wh_id
from utils.permissions import filter_users_by_permission, get_admin_allowed_countries, can_view_user

logger = logging.getLogger(__name__)

@admin_wh_bp.route('/admin/wh')
@login_required
@admin_required
def wh_info_page():
    """库房信息管理页面"""
    return render_template('admin/wh_info.html')

@admin_wh_bp.route('/api/admin/wh/list', methods=['GET'])
@login_required
@admin_required
def get_wh_list():
    """获取库房列表（支持筛选）"""
    db = get_supabase_admin()
    
    search = request.args.get('search', '').strip()
    country = request.args.get('country', '')
    wh_type = request.args.get('wh_type', '')
    include_deleted = request.args.get('include_deleted', 'false').lower() == 'true'
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 20, type=int)
    
    allowed_countries = get_admin_allowed_countries()
    
    # ✅ 修复：使用 filter 进行模糊搜索
    query = db.table("wh_info").select("*")
    
    if not include_deleted:
        # 未删除列表：只显示未删除的记录
        query = query.is_("deleted_at", "null")
    else:
        # 已删除列表：只显示已删除的记录（排除未删除的）
        query = query.not_.is_("deleted_at", "null")
    
    # ✅ 使用多个 filter 条件而不是 or_
    if search:
        query = query.filter("wh_id", "ilike", f"%{search}%")
        # 注意：Supabase 不支持一次查询多个字段的 OR，需要分别处理
        # 简化处理：只搜索 wh_id 和 wh_name_en
        # 更复杂的需求可以前端处理
    
    if country:
        query = query.eq("country_code", country)
    
    if wh_type:
        query = query.eq("wh_type", wh_type)
    
    if allowed_countries is not None:
        query = query.in_("country_code", allowed_countries)
    
    total_res = query.execute()
    total = len(total_res.data or [])
    
    start = (page - 1) * per_page
    end = start + per_page - 1
    res = query.range(start, end).order("wh_id").execute()
    
    return jsonify({
        "data": res.data or [],
        "total": total,
        "page": page,
        "per_page": per_page
    })

@admin_wh_bp.route('/api/admin/wh', methods=['POST'])
@login_required
@admin_required
def create_wh():
    """创建库房（带权限控制）"""
    data = request.json
    wh_id = data.get('wh_id', '').strip().upper()  # ✅ 自动转大写
    
    if not wh_id:
        return jsonify({"success": False, "message": "库房编码不能为空"}), 400
    
    # ✅ 获取当前用户权限范围
    from utils.permissions import get_allowed_countries, is_developer
    
    current_role = session.get('role')
    is_dev = is_developer()
    allowed_countries = get_allowed_countries()
    
    # ✅ 从库房ID提取国家代码（使用现有函数）
    extracted_country = extract_country_from_wh_id(wh_id)
    
    logger.info(f"创建库房: wh_id={wh_id}, 提取国家={extracted_country}, 用户角色={current_role}, 权限范围={allowed_countries}")
    
    # ✅ 权限校验（非开发者）
    if not is_dev:
        if current_role in ('admin', 'super_admin'):
            # 检查是否有权限范围限制
            if allowed_countries is not None and len(allowed_countries) > 0:
                # 必须能提取到国家代码
                if not extracted_country:
                    return jsonify({
                        "success": False, 
                        "message": f"库房编码 {wh_id} 格式无效，前2-3位应为国家代码（如 NP、LK、CN）"
                    }), 400
                
                # 检查提取的国家是否在权限范围内
                if extracted_country not in allowed_countries:
                    return jsonify({
                        "success": False, 
                        "message": f"无权创建国家代码为 {extracted_country} 的库房。您的权限范围仅限: {', '.join(allowed_countries)}"
                    }), 403
    
    # ✅ 使用管理员客户端绕过 RLS
    db = get_supabase_admin()
    
    # 检查未删除的库房
    existing = db.table("wh_info").select("id").eq("wh_id", wh_id).is_("deleted_at", "null").execute()
    if existing.data:
        return jsonify({"success": False, "message": f"库房编码 {wh_id} 已存在"}), 400
    
    # ✅ 检查已删除的库房
    existing_deleted = db.table("wh_info").select("id, deleted_at").eq("wh_id", wh_id).not_.is_("deleted_at", "null").execute()
    if existing_deleted.data:
        return jsonify({
            "success": False, 
            "message": f"库房编码 {wh_id} 在已删除清单中存在，请先恢复后再操作",
            "deleted_exists": True,
            "deleted_id": existing_deleted.data[0]['id']
        }), 400
    
    # 确定国家代码（优先使用手动填写，否则从 wh_id 提取）
    country_code = data.get('country_code') or extracted_country
    
    insert_data = {
        "wh_id": wh_id,
        "wh_name_cn": data.get('wh_name_cn', ''),
        "wh_name_en": data.get('wh_name_en', ''),
        "wh_type": data.get('wh_type', ''),
        "country_code": country_code,
        "is_active": data.get('is_active', True),
        "created_by": session.get('user_id'),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "remark": data.get('remark', '')
    }
    
    result = db.table("wh_info").insert(insert_data).execute()
    return jsonify({"success": True, "data": result.data[0] if result.data else None, "id": result.data[0]['id'] if result.data else None})

@admin_wh_bp.route('/api/admin/wh/<int:wh_id>', methods=['PUT'])
@login_required
@admin_required
def update_wh(wh_id):
    """更新库房信息（带权限控制）"""
    data = request.json
    db = get_supabase_admin()
    
    # 检查库房是否存在
    existing = db.table("wh_info").select("id, wh_id, country_code").eq("id", wh_id).is_("deleted_at", "null").execute()
    if not existing.data:
        return jsonify({"success": False, "message": "库房不存在"}), 404
    
    existing_wh = existing.data[0]
    new_wh_id = data.get('wh_id', '').strip().upper()
    
    # ✅ 获取当前用户权限范围
    from utils.permissions import get_allowed_countries, is_developer
    
    current_role = session.get('role')
    is_dev = is_developer()
    allowed_countries = get_allowed_countries()
    
    # ✅ 如果修改了 wh_id，需要检查新 wh_id 的国家权限
    if new_wh_id and new_wh_id != existing_wh.get('wh_id'):
        # 从库房ID提取国家代码
        extracted_country = extract_country_from_wh_id(new_wh_id)
        
        if not is_dev:
            if allowed_countries is not None and len(allowed_countries) > 0:
                if not extracted_country:
                    return jsonify({
                        "success": False, 
                        "message": f"库房编码 {new_wh_id} 格式无效，前2-3位应为国家代码"
                    }), 400
                
                if extracted_country not in allowed_countries:
                    return jsonify({
                        "success": False, 
                        "message": f"无权将库房国家改为 {extracted_country}。您的权限范围仅限: {', '.join(allowed_countries)}"
                    }), 403
        
        # 检查新 wh_id 是否已存在
        existing_new = db.table("wh_info").select("id").eq("wh_id", new_wh_id).is_("deleted_at", "null").neq("id", wh_id).execute()
        if existing_new.data:
            return jsonify({"success": False, "message": f"库房编码 {new_wh_id} 已存在"}), 400
    
    # ✅ 如果修改了国家代码，也需要检查权限
    new_country_code = data.get('country_code')
    if new_country_code and new_country_code != existing_wh.get('country_code'):
        if not is_dev and allowed_countries is not None and len(allowed_countries) > 0:
            if new_country_code not in allowed_countries:
                return jsonify({
                    "success": False, 
                    "message": f"无权将库房国家改为 {new_country_code}。您的权限范围仅限: {', '.join(allowed_countries)}"
                }), 403
    
    update_data = {
        "wh_name_cn": data.get('wh_name_cn', ''),
        "wh_name_en": data.get('wh_name_en', ''),
        "wh_type": data.get('wh_type', ''),
        "country_code": new_country_code or existing_wh.get('country_code', ''),
        "is_active": data.get('is_active', True),
        "remark": data.get('remark', ''),
        "updated_at": datetime.now(timezone.utc).isoformat()
    }
    
    # 如果修改了 wh_id，也要更新
    if new_wh_id and new_wh_id != existing_wh.get('wh_id'):
        update_data["wh_id"] = new_wh_id
    
    result = db.table("wh_info").update(update_data).eq("id", wh_id).execute()
    return jsonify({"success": True})

@admin_wh_bp.route('/api/admin/wh/<int:wh_id>', methods=['DELETE'])
@login_required
@admin_required
def delete_wh(wh_id):
    """删除库房（支持永久删除和软删除）"""
    permanent = request.args.get('permanent', 'false').lower() == 'true'
    db = get_supabase_admin()
    
    existing = db.table("wh_info").select("id, wh_id").eq("id", wh_id).execute()
    if not existing.data:
        return jsonify({"success": False, "message": "库房不存在"}), 404
    
    wh_code = existing.data[0].get('wh_id', '')
    
    # ✅ 检查是否有关联用户（包括软删除的用户）
    has_users, user_count, user_type = check_wh_has_users(wh_code)
    
    if permanent and has_users:
        return jsonify({
            "success": False, 
            "message": f"库房 {wh_code} 有关联的 {user_count} 个{user_type}，无法永久删除。请先清理关联用户。"
        }), 400
    
    if permanent:
        db.table("wh_info").delete().eq("id", wh_id).execute()
    else:
        db.table("wh_info").update({
            "deleted_at": datetime.now(timezone.utc).isoformat(),
            "deleted_by": session.get('user_id')
        }).eq("id", wh_id).execute()
    
    return jsonify({"success": True})

@admin_wh_bp.route('/api/admin/wh/<int:wh_id>/restore', methods=['POST'])
@login_required
@admin_required
def restore_wh(wh_id):
    """恢复软删除的库房"""
    
    #db = get_supabase()
    db = get_supabase_admin()  # ✅ 使用管理员客户端
    
    db.table("wh_info").update({
        "deleted_at": None,
        "deleted_by": None
    }).eq("id", wh_id).execute()
    return jsonify({"success": True})

@admin_wh_bp.route('/api/admin/wh/import', methods=['POST'])
@login_required
@admin_required
def import_wh():
    """批量导入库房（带权限校验和国家一致性校验）"""
    if 'file' not in request.files:
        return jsonify({"success": False, "message": "请选择文件"}), 400
    
    file = request.files['file']
    if not file.filename.endswith(('.xlsx', '.xls')):
        return jsonify({"success": False, "message": "只支持 Excel 文件"}), 400
    # 定义表头映射
    header_map = {
        'wh_id': 'wh_id',
        '库房ID': 'wh_id',
        'wh_name_cn': 'wh_name_cn',
        '库房名称(CN)': 'wh_name_cn',
        'wh_name_en': 'wh_name_en',
        '库房名称(EN)': 'wh_name_en',
        'wh_type': 'wh_type',
        '库房类型': 'wh_type',
        'country_code': 'country_code',
        '国家代码': 'country_code'
    }
    
    # 解析 Excel，获取有效数据行
    valid_rows, parse_errors, headers = parse_excel_rows(file, header_map, ['wh_id'])
    
    if parse_errors:
        return jsonify({
            "success": False,
            "message": "文件解析失败",
            "errors": parse_errors
        }), 400
    
    db = get_supabase_admin()
    allowed_countries = get_admin_allowed_countries()
    
    success_count = 0
    update_count = 0   # ✅ 新增：记录更新的数量
    error_rows = []
    
    for row_idx, wh_data in valid_rows:
        wh_id = wh_data.get('wh_id', '').upper().strip()
        if not wh_id:
            error_rows.append(f"第{row_idx}行: 库房ID不能为空")
            continue
        
        # 校验国家与库房ID的一致性
        country_input = wh_data.get('country_code', '')
        final_country, is_valid, error_msg = validate_country_and_wh_id(country_input, wh_id)
        
        if not is_valid:
            error_rows.append(f"第{row_idx}行: {error_msg}")
            continue
        
        # 权限校验
        if allowed_countries is not None and final_country:
            if final_country not in allowed_countries:
                error_rows.append(f"第{row_idx}行: 国家 {final_country} 不在您的权限范围内")
                continue
        
        try:
            # 检查是否存在（包括已删除的）
            existing = db.table("wh_info").select("id, deleted_at").eq("wh_id", wh_id).execute()
            
            if existing.data:
                existing_record = existing.data[0]
                update_count += 1
                # 更新记录（无论是软删除还是正常状态）
                db.table("wh_info").update({
                    "wh_name_cn": wh_data.get('wh_name_cn', ''),
                    "wh_name_en": wh_data.get('wh_name_en', ''),
                    "wh_type": wh_data.get('wh_type', ''),
                    "country_code": final_country,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                    "deleted_at": None,  # 恢复软删除
                    "deleted_by": None
                }).eq("id", existing_record['id']).execute()
                success_count += 1
            else:
                # 插入新记录
                db.table("wh_info").insert({
                    "wh_id": wh_id,
                    "wh_name_cn": wh_data.get('wh_name_cn', ''),
                    "wh_name_en": wh_data.get('wh_name_en', ''),
                    "wh_type": wh_data.get('wh_type', ''),
                    "country_code": final_country,
                    "created_by": session.get('user_id'),
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "is_active": True
                }).execute()
                success_count += 1
        except Exception as e:
            error_rows.append(f"第{row_idx}行: 操作失败 - {str(e)}")
            logger.error(f"导入库房失败第{row_idx}行: {e}")
    
    result = format_import_result(success_count, error_rows, update_count)
    return jsonify(result)

def check_wh_has_users(wh_id):
    """检查库房ID是否有关联用户（包括软删除的用户）"""
    db = get_supabase_admin()
    # 检查活跃用户
    users = db.table("users").select("id").eq("wh_id", wh_id).is_("deleted_at", "null").execute()
    if users.data:
        return True, len(users.data), "活跃用户"
    # 检查软删除的用户
    deleted_users = db.table("users").select("id").eq("wh_id", wh_id).not_.is_("deleted_at", "null").execute()
    if deleted_users.data:
        return True, len(deleted_users.data), "已删除用户"
    return False, 0, None

@admin_wh_bp.route('/api/admin/wh/batch_delete', methods=['POST'])
@login_required
@admin_required
def batch_delete_wh():
    """批量删除库房"""
    data = request.json
    ids = data.get('ids', [])
    delete_type = data.get('delete_type', 'soft')  # 'soft' or 'hard'
    
    if not ids:
        return jsonify({"success": False, "message": "请选择要删除的库房"}), 400
    
    # db = get_supabase()
    db = get_supabase_admin()  # ✅ 使用管理员客户端

    success_count = 0
    fail_count = 0
    errors = []
    
    for wh_id in ids:
        try:
            if delete_type == 'hard':
                # 永久删除
                db.table("wh_info").delete().eq("id", wh_id).execute()
            else:
                # 软删除
                db.table("wh_info").update({
                    "deleted_at": datetime.now(timezone.utc).isoformat(),
                    "deleted_by": session.get('user_id')
                }).eq("id", wh_id).execute()
            success_count += 1
        except Exception as e:
            fail_count += 1
            errors.append(str(e))
    
    return jsonify({"success": True, "success_count": success_count, "fail_count": fail_count, "errors": errors})

@admin_wh_bp.route('/api/admin/wh/batch_restore', methods=['POST'])
@login_required
@admin_required
def batch_restore_wh():
    """批量恢复软删除的库房"""
    data = request.json
    ids = data.get('ids', [])
    
    if not ids:
        return jsonify({"success": False, "message": "请选择要恢复的库房"}), 400
    
    #db = get_supabase()
    db = get_supabase_admin()  # ✅ 使用管理员客户端

    success_count = 0
    fail_count = 0
    
    for wh_id in ids:
        try:
            db.table("wh_info").update({
                "deleted_at": None,
                "deleted_by": None
            }).eq("id", wh_id).execute()
            success_count += 1
        except Exception:
            fail_count += 1
    
    return jsonify({"success": True, "success_count": success_count, "fail_count": fail_count})

@admin_wh_bp.route('/api/admin/wh/search', methods=['GET'])
@login_required
def search_wh():
    """搜索库房（用于下拉选择）"""
    q = request.args.get('q', '').strip().upper()
    if not q:
        return jsonify({"data": []})
    
    db = get_supabase_admin()
    allowed_countries = get_admin_allowed_countries()
    
    # ✅ 使用 filter 进行模糊搜索
    query = db.table("wh_info").select("*").is_("deleted_at", "null")
    
    if q:
        query = query.filter("wh_id", "ilike", f"%{q}%")
    
    if allowed_countries is not None:
        query = query.in_("country_code", allowed_countries)
    
    res = query.limit(20).execute()
    
    return jsonify({"data": res.data or []})

@admin_wh_bp.route('/api/admin/wh/import/template', methods=['GET'])
@login_required
@admin_required
def download_wh_import_template():
    """下载库房导入模板"""
    buffer = generate_import_template('wh')
    return send_file(
        buffer,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name=f"库房导入模板_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    )
