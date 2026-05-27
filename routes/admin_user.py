# routes/admin_user.py
import os, json, logging, uuid, secrets, string, sys, openpyxl, re
from datetime import datetime, timezone, timedelta, date
from flask import  (
    Flask, render_template, request, redirect, url_for, 
    session, flash, jsonify, send_file, make_response
)
from . import admin_user_bp
from services import auth, exam, export
from services.db import get_supabase
from services.auth import hash_password
from utils.email_notifier import send_bilingual_notification, EmailScenario, _format_time
from utils.permissions import (is_developer, apply_country_filter, can_view_user, get_admin_allowed_countries, 
    can_modify_user, parse_countries_input, filter_users_by_permission
)
from routes.helpers import login_required, admin_required, get_current_user
from utils.import_helper import parse_excel_rows, validate_country_and_wh_id, generate_import_template, format_import_result

logger = logging.getLogger(__name__)

@admin_user_bp.route('/admin/users')
@login_required
@admin_required
def admin_user_list():
    return render_template('admin/list_users.html', is_developer=is_developer())

@admin_user_bp.route('/api/admin/users/<user_id>')
@login_required
@admin_required
def api_admin_user_detail(user_id):
    """后端单用户查询接口"""
    db = get_supabase()
    res = db.table("users").select("*").eq("id", user_id).maybe_single().execute()
    if not res.data:
        return jsonify({"error": "用户不存在"}), 404
    return jsonify(res.data)

@admin_user_bp.route('/api/admin/users')
@login_required
@admin_required
def api_admin_users():
    """获取用户列表（带完整权限控制）"""
    logger.info("=" * 50)
    logger.info("调用 api_admin_users")
    logger.info(f"当前用户 role={session.get('role')}, user_country={session.get('user_country')}")

    db = get_supabase()
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 20, type=int)
    search = request.args.get('search', '').strip()
    country = request.args.get('country', '')
    user_status = request.args.get('status', '')
    
    # 批量查询支持
    if 'ids' in request.args:
        ids = request.args.get('ids').split(',')
        if ids:
            query = db.table("users").select("*").in_("id", ids)
            query = apply_country_filter(query, 'country')
            res = query.execute()
            users = [u for u in (res.data or []) if can_view_user(u)]
            return jsonify({"data": users, "total": len(users)})
    
    allowed_countries = get_admin_allowed_countries()
    current_user_id = session.get('user_id')
    current_role = session.get('role')
    
    logger.info(f"allowed_countries = {allowed_countries}")

    # ✅ 基础查询：先获取所有符合条件的用户（不做国家过滤）
    query = db.table("users").select("*", count="exact").is_("deleted_at", "null")
    
    # 非开发者不能看到受保护账号
    if not is_developer():
        query = query.eq("is_protected", False)
    
    # 角色过滤：非超管不能看到超管和开发者
    if current_role != 'super_admin' and not is_developer():
        query = query.neq("role", "super_admin").neq("role", "developer")
    
    if user_status:
        query = query.eq("user_status", user_status)
    
    if country and country != '___NONE___':
        if ',' in country:
            codes = [c.strip() for c in country.split(',') if c.strip()]
            query = query.in_("country", codes)
        else:
            query = query.eq("country", country)
    
    # 如果有搜索条件，先应用搜索
    if search:
        search_lower = search.lower()
        # 先获取所有数据（不带分页），然后在内存中过滤
        all_res = query.execute()
        all_users = all_res.data or []
        # 内存搜索
        all_users = [u for u in all_users if 
                     (u.get('email') and search_lower in u['email'].lower()) or
                     (u.get('name_cn') and search_lower in u['name_cn'].lower()) or
                     (u.get('name_en') and search_lower in u['name_en'].lower())]
    else:
        # 先获取所有数据（不带分页）
        all_res = query.execute()
        all_users = all_res.data or []
    
    # ✅ 在内存中进行国家权限过滤
    filtered_users = filter_users_by_permission(all_users)
    
    # 按创建时间倒序排序
    filtered_users.sort(key=lambda x: x.get('created_at', ''), reverse=True)
    
    # 内存分页
    total = len(filtered_users)
    start = (page - 1) * per_page
    end = start + per_page
    paginated = filtered_users[start:end]
    
    return jsonify({
        "data": paginated,
        "total": total,
        "page": page,
        "per_page": per_page
    })

@admin_user_bp.route('/api/admin/users', methods=['POST'])
@login_required
@admin_required
def api_admin_add_user():
    """管理员添加用户（带角色权限控制）"""
    data = request.json
    email = data.get('email', '').strip().lower() or None
    name_en = data.get('name_en', '').strip()
    role = data.get('role', 'user')
    admin_countries = data.get('admin_countries', '[]')
    user_status = data.get('user_status', 'imported')
    
    db = get_supabase()
    
    # ========== 角色权限校验 ==========
    current_role = session.get('role')
    
    # 开发者可以创建任何角色
    if not is_developer():
        # 非开发者不能创建开发者角色
        if role == 'developer':
            return jsonify({"success": False, "message": "cannot_create_developer", "params": []}), 403
        
        # 超管可以创建超管、管理员、用户
        if current_role == 'super_admin':
            if role not in ['super_admin', 'admin', 'user']:
                return jsonify({"success": False, "message": "invalid_role", "params": []}), 400
        # 管理员只能创建用户
        elif current_role == 'admin':
            if role != 'user':
                return jsonify({"success": False, "message": "admin_can_only_create_user", "params": []}), 403
        else:
            return jsonify({"success": False, "message": "permission_denied", "params": []}), 403
    
    # ========== 超管/管理员权限范围校验 ==========
    if role in ['super_admin', 'admin']:
        # 解析权限范围
        if isinstance(admin_countries, str):
            try:
                countries_list = json.loads(admin_countries)
            except:
                countries_list = parse_countries_input(admin_countries)
        else:
            countries_list = admin_countries
        
        # 权限范围不能为空
        if not countries_list or len(countries_list) == 0:
            return jsonify({
                "success": False, 
                "message": "admin_countries_required",
                "params": [role]
            }), 400
        
        # 存储为 JSON 字符串
        admin_countries_json = json.dumps(countries_list)
    else:
        admin_countries_json = None
    
    # ========== 姓名必填校验 ==========
    if not name_en:
        return jsonify({"success": False, "message": "name_cannot_empty", "params": []}), 400
    
    # ========== 邮箱条件校验 ==========
    if not email and user_status != 'imported':
        return jsonify({"success": False, "message": "mail_cannot_empty", "params": []}), 400
    
    # ========== 检查用户是否已存在 ==========
    existing_users = db.table("users").select("*").eq("name_en", name_en).execute()
    existing_users_list = existing_users.data or []
    
    # 分离活跃用户和已删除用户
    active_users = [u for u in existing_users_list if u.get('deleted_at') is None]
    deleted_users = [u for u in existing_users_list if u.get('deleted_at') is not None]
    
    # 检查活跃用户重复
    birthday = data.get('birthday', '') or None
    employee_id = data.get('employee_id', '').strip() or None
    
    for active in active_users:
        active_birthday = active.get('birthday')
        active_employee_id = active.get('employee_id')
        
        if (birthday and active_birthday == birthday) or (employee_id and active_employee_id == employee_id):
            return jsonify({"success": False, "message": "duplicate_user_found", "params": []}), 400
        if not birthday and not employee_id:
            return jsonify({"success": False, "message": "duplicate_user_found", "params": []}), 400
    
    # 处理已删除用户的恢复
    if deleted_users:
        existing_deleted = deleted_users[0]
        update_data = {
            "email": email,
            "user_status": user_status,
            "is_active": False if user_status == 'imported' else True,
            "deleted_at": None,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "role": role,
            "admin_countries": admin_countries_json,
            "created_by": session['user_id']
        }
        
        # 更新其他字段（如果提供）
        if birthday:
            update_data["birthday"] = birthday
        if employee_id:
            update_data["employee_id"] = employee_id
        if data.get('company'):
            update_data["company"] = data.get('company')
        if data.get('department'):
            update_data["department"] = data.get('department')
        if data.get('country'):
            update_data["country"] = data.get('country')
        if data.get('phone'):
            update_data["phone"] = data.get('phone')
        if data.get('role'):
            update_data["role"] = data.get('role')
        if data.get('wh_type'):
            update_data["wh_type"] = data.get('wh_type')
        if data.get('wh_id'):
            update_data["wh_id"] = data.get('wh_id')
        if data.get('wh_name_en'):
            update_data["wh_name_en"] = data.get('wh_name_en')
        if data.get('is_partner'):
            update_data["is_partner"] = data.get('is_partner') == 'Y'
        
        try:
            db.table("users").update(update_data).eq("id", existing_deleted['id']).execute()
            logger.info(f"恢复已删除用户: {name_en}, ID: {existing_deleted['id']}")
            
            # 如果需要发送邮件通知（仅当有邮箱时）
            if email:
                temp_password = ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(10))
                password_hash = auth.hash_password(temp_password)
                db.table("users").update({"password_hash": password_hash}).eq("id", existing_deleted['id']).execute()
                try:
                    send_bilingual_notification(
                        email=email,
                        scenario=EmailScenario.USER_CREATED,
                        params={
                            "name": name_en or email,
                            "email": email,
                            "temp_password": temp_password,
                            "host_url": request.host_url,
                        },
                        host_url=request.host_url,
                        auth_module=auth
                    )
                except Exception as e:
                    logger.warning(f"发送邮件失败: {e}")
            
            return jsonify({"success": True, "user_id": existing_deleted['id'], "restored": True})
        except Exception as e:
            logger.error(f"恢复用户失败: {e}")
            return jsonify({"success": False, "message": str(e)}), 500

    # 邮箱唯一性检查
    if email:
        exist = db.table("users").select("id").eq("email", email).is_("deleted_at", "null").execute()
        if exist.data:
            return jsonify({"success": False, "message": "email_already_registered", "params": []}), 400

    # 生成用户ID和密码
    user_id = str(uuid.uuid4())
    temp_password = ''
    password_hash = ''
    if email:
        alphabet = string.ascii_letters + string.digits
        temp_password = ''.join(secrets.choice(alphabet) for _ in range(10))
        password_hash = auth.hash_password(temp_password)

    # 准备插入数据
    insert_data = {
        "id": user_id,
        "email": email,
        "password_hash": password_hash,
        "name_en": name_en,
        "company": data.get('company', ''),
        "department": data.get('department', ''),
        "employee_id": employee_id,
        "birthday": birthday,
        "country": data.get('country', ''),
        "phone": data.get('phone', ''),
        "role": role,
        "admin_countries": admin_countries_json,
        "user_status": user_status,
        "is_partner": data.get('is_partner', 'N') == 'Y',
        "wh_type": data.get('wh_type', ''),
        "wh_id": data.get('wh_id', ''),
        "wh_name_en": data.get('wh_name_en', ''),
        "is_active": False if user_status == 'imported' else True,
        "created_by": session['user_id'],
        "is_protected": (role == 'developer')  # 开发者账号自动保护
    }
    try:
        db.table("users").insert(insert_data).execute()
        # 发送邮件通知（仅当邮箱存在）
        if email:
            try:
                send_bilingual_notification(
                    email=email,
                    scenario=EmailScenario.USER_CREATED,
                    params={
                        "name": name_en or email,
                        "email": email,
                        "temp_password": temp_password,
                        "host_url": request.host_url,
                    },
                    host_url=request.host_url,
                    auth_module=auth
                )
            except Exception as e:
                logger.warning(f"发送邮件失败: {e}")
        
        logger.info(f"管理员添加用户: {email or '无邮箱'}, 状态: {user_status}")
        return jsonify({"success": True, "user_id": user_id, "temp_password": temp_password})
    except Exception as e:
        logger.error(f"添加用户失败: {e}")
        return jsonify({"success": False, "message": str(e)}), 500

@admin_user_bp.route('/api/admin/users/import', methods=['POST'])
@login_required
@admin_required
def api_admin_import_users():
    """批量导入用户（带角色权限校验）"""
    if 'file' not in request.files:
        return jsonify({"success": False, "message": "jsonify_no_file_selected", "params": []}), 400
    
    file = request.files['file']
    if not file.filename.endswith(('.xlsx', '.xls')):
        return jsonify({"success": False, "message": "jsonify_only_supports_files", "params": []}), 400
    
    # 定义表头映射
    header_map = {
        '国家': 'country',
        '邮箱': 'email',
        '姓名': 'name_en',
        '角色': 'role',
        '服务商?': 'is_partner',
        '公司': 'company',
        '部门': 'department',
        '库房类型': 'wh_type',
        '库房ID': 'wh_id',
        '库房名称(EN)': 'wh_name_en',
        '工号': 'employee_id',
        '手机号': 'phone',
        '生日': 'birthday',
        '权限范围': 'admin_countries'
    }
    
    # 解析 Excel，获取有效数据行
    valid_rows, parse_errors, headers = parse_excel_rows(file, header_map, ['name_en'])
    
    if parse_errors:
        return jsonify({
            "success": False,
            "message": "文件解析失败",
            "errors": parse_errors
        }), 400
    
    db = get_supabase()
    allowed_countries = get_admin_allowed_countries()
    
    success_count = 0
    error_rows = []
    
    for row_idx, user_data in valid_rows:
        # 处理空字符串
        for field in ['birthday', 'employee_id', 'email', 'phone', 'company', 'department', 'wh_type', 'wh_id', 'wh_name_en', 'country']:
            if user_data.get(field) == '':
                user_data[field] = None
        
        if user_data.get('email'):
            user_data['email'] = user_data['email'].lower()
        
        name_en = user_data.get('name_en', '')
        if not name_en:
            error_rows.append(f"第{row_idx}行: 姓名不能为空")
            continue
        
        # 国家与库房ID一致性校验
        country_input = user_data.get('country')
        wh_id = user_data.get('wh_id')
        final_country, is_valid, error_msg = validate_country_and_wh_id(country_input, wh_id)
        
        if not is_valid:
            error_rows.append(f"第{row_idx}行: {error_msg}")
            continue
        
        # 权限校验
        if final_country:
            if allowed_countries is not None and final_country not in allowed_countries:
                error_rows.append(f"第{row_idx}行: 国家 {final_country} 不在您的权限范围内")
                continue
        
        # 角色处理
        role = user_data.get('role', 'user').lower()
        if role not in ['user', 'admin', 'super_admin', 'developer']:
            role = 'user'
        user_data['role'] = role
        
        # 角色权限校验
        if role in ['admin', 'super_admin']:
            admin_countries_raw = user_data.get('admin_countries', '')
            if not admin_countries_raw:
                error_rows.append(f"第{row_idx}行: {role}角色必须填写权限范围")
                continue
            if ',' in admin_countries_raw:
                countries_list = [c.strip().upper() for c in admin_countries_raw.split(',')]
            else:
                countries_list = [admin_countries_raw.strip().upper()]
            if allowed_countries is not None:
                invalid_countries = [c for c in countries_list if c not in allowed_countries]
                if invalid_countries:
                    error_rows.append(f"第{row_idx}行: 权限范围包含无权管理的国家: {', '.join(invalid_countries)}")
                    continue
            user_data['admin_countries'] = json.dumps(countries_list)
        else:
            user_data['admin_countries'] = None
        
        # 服务商处理
        is_partner_val = user_data.get('is_partner', 'N')
        user_data['is_partner'] = is_partner_val.upper() in ('Y', 'YES', 'TRUE', '1')
        
        # 设置默认密码
        DEFAULT_PLACEHOLDER_HASH = hash_password('__IMPORTED_USER_PLACEHOLDER__')
        user_data['password_hash'] = DEFAULT_PLACEHOLDER_HASH
        
        # 设置默认值
        user_data['id'] = str(uuid.uuid4())
        user_data['user_status'] = 'imported'
        user_data['is_active'] = False
        user_data['created_by'] = session['user_id']
        user_data['is_protected'] = (role == 'developer')
        user_data['created_at'] = datetime.now(timezone.utc).isoformat()
        user_data['country'] = final_country or None
        
        # 移除空值字段
        user_data = {k: v for k, v in user_data.items() if v != '' and v is not None}
        
        try:
            # 检查是否已存在同名用户
            existing = db.table("users").select("id, deleted_at").eq("name_en", name_en).execute()
            if existing.data:
                existing_user = existing.data[0]
                if existing_user.get('deleted_at') is None:
                    error_rows.append(f"第{row_idx}行: 用户 {name_en} 已存在")
                    continue
                else:
                    # 恢复已删除的用户
                    update_data = {k: v for k, v in user_data.items() if k not in ['id', 'created_at']}
                    update_data['deleted_at'] = None
                    update_data['deleted_by'] = None
                    db.table("users").update(update_data).eq("id", existing_user['id']).execute()
                    success_count += 1
                    continue
            
            db.table("users").insert(user_data).execute()
            success_count += 1
        except Exception as e:
            error_rows.append(f"第{row_idx}行: 插入失败 - {str(e)}")
            logger.error(f"导入用户失败第{row_idx}行: {e}")
    
    result = format_import_result(success_count, error_rows)
    return jsonify(result)

# ✅ 添加模板下载接口
@admin_user_bp.route('/api/admin/users/import/template', methods=['GET'])
@login_required
@admin_required
def download_user_import_template():
    """下载用户导入模板"""
    try:
        buffer = generate_import_template('user')
    except ImportError:
        # 备用模板生成
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "用户导入模板"
        headers = ['国家', '邮箱', '姓名', '角色', '服务商?', '公司', '部门', '库房类型', '库房ID', '库房名称(EN)', '工号', '手机号', '生日', '权限范围']
        for col, header in enumerate(headers, 1):
            ws.cell(row=1, column=col, value=header)
        buffer = BytesIO()
        wb.save(buffer)
        buffer.seek(0)
    
    return send_file(
        buffer,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name=f"用户导入模板_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    )

@admin_user_bp.route('/api/admin/users/<user_id>', methods=['PUT'])
@login_required
@admin_required
def api_admin_edit_user(user_id):
    """编辑用户信息（带完整权限控制）"""
    data = request.json
    db = get_supabase()
    
    # 获取目标用户信息
    target_user_res = db.table("users").select("*").eq("id", user_id).maybe_single().execute()
    if not target_user_res.data:
        return jsonify({"success": False, "message": "user_not_found", "params": []}), 404
    target_user = target_user_res.data
    
    # ========== 权限检查 ==========
    # 开发者可以编辑任何用户
    if not is_developer():
        # 受保护账号只能本人编辑
        if target_user.get('is_protected') and user_id != session['user_id']:
            return jsonify({"success": False, "message": "protected_account", "params": []}), 403
        
        current_role = session.get('role')
        target_role = target_user.get('role', 'user')
        
        # 超管不能编辑开发者
        if target_role == 'developer':
            return jsonify({"success": False, "message": "cannot_edit_developer", "params": []}), 403
        
        # 管理员不能编辑超管
        if current_role == 'admin' and target_role in ['super_admin', 'admin']:
            return jsonify({"success": False, "message": "cannot_edit_admin", "params": []}), 403
        
        # 管理员不能修改角色字段
        if current_role == 'admin' and 'role' in data and data['role'] != target_role:
            return jsonify({"success": False, "message": "cannot_change_role", "params": []}), 403
    
    # ========== 构建更新数据 ==========
    update_data = {}
    
    # 基础字段（所有角色可修改自己）
    allowed_fields = ['name_en', 'company', 'department', 'employee_id', 'birthday', 
                      'country', 'phone', 'wh_type', 'wh_id', 'wh_name_en', 'user_status', 'is_partner']
    
    # 角色和权限范围字段（需要更高权限）
    if 'role' in data:
        new_role = data['role']
        # 开发者可以修改任何角色
        if is_developer():
            update_data['role'] = new_role
            # 如果改为开发者，自动设置保护
            if new_role == 'developer':
                update_data['is_protected'] = True
        # 超管可以修改角色（但不能改为开发者）
        elif session.get('role') == 'super_admin':
            if new_role != 'developer':
                update_data['role'] = new_role
            else:
                return jsonify({"success": False, "message": "cannot_set_developer", "params": []}), 403
    
    # 权限范围字段
    if 'admin_countries' in data:
        if is_developer() or session.get('role') == 'super_admin':
            countries_input = data['admin_countries']
            if isinstance(countries_input, str):
                try:
                    countries_list = json.loads(countries_input)
                except:
                    countries_list = parse_countries_input(countries_input)
            else:
                countries_list = countries_input
            
            if countries_list:
                update_data['admin_countries'] = json.dumps(countries_list)
            else:
                update_data['admin_countries'] = None
    
    # 邮箱字段（需要唯一性检查）
    if 'email' in data:
        email_val = data['email'].strip().lower() or None
        if email_val:
            conflict = db.table("users").select("id").eq("email", email_val).neq("id", user_id).execute()
            if conflict.data:
                return jsonify({"success": False, "message": "email_already_used", "params": []}), 400
        update_data['email'] = email_val
    
    # 普通字段
    for field in allowed_fields:
        if field in data:
            val = data[field]
            if field == 'birthday':
                val = val if val else None
            elif field == 'is_partner':
                val = True if val in ('Y', 'true', True, '1') else False
            update_data[field] = val
    
    if not update_data:
        return jsonify({"success": False, "message": "no_fields_to_update", "params": []}), 400
    
    try:
        db.table("users").update(update_data).eq("id", user_id).execute()
        
        # 如果更新的是当前用户，同步 session
        if user_id == session.get('user_id'):
            if 'role' in update_data:
                session['role'] = update_data['role']
            if 'admin_countries' in update_data:
                session['admin_countries'] = update_data['admin_countries']
        
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@admin_user_bp.route('/api/admin/users/<user_id>', methods=['DELETE'])
@login_required
@admin_required
def api_admin_delete_user(user_id):
    """删除用户：已导入 → 硬删除；已注册 → 软删除"""
    if user_id == session['user_id']:
        return jsonify({"success": False, "message": "不能删除自己的账号"}), 400
    db = get_supabase()

    # 获取目标用户信息（包括 is_protected）
    target_res = db.table("users").select("role, user_status, is_protected").eq("id", user_id).maybe_single().execute()
    if not target_res.data:
        return jsonify({"success": False, "message": "用户不存在"}), 404
    target = target_res.data

    if target.get('is_protected') and user_id != session['user_id']:
        return jsonify({"success": False, "message": "该账号被保护，无法删除"}), 403

    # 权限检查（开发者保护）
    current_user = get_current_user()
    if not can_modify_user(target, current_user, 'delete'):
        return jsonify({"success": False, "message": "该账号被保护，无法删除"}), 403

    target_role = target.get('role', 'user')
    current_role = session.get('role')

    # 权限控制：普通管理员不能删除管理员或超管
    if current_role != 'super_admin' and target_role in ('admin', 'super_admin'):
        return jsonify({"success": False, "message": "权限不足，无法删除管理员"}), 403

    user_status = target.get('user_status', 'registered')

    # ========= 已导入用户：硬删除 =========
    if user_status == 'imported':
        try:
            # 清理可能存在的考试分配记录（防止外键冲突）
            db.table("exam_assignments").delete().eq("user_id", user_id).execute()
            # 物理删除用户
            db.table("users").delete().eq("id", user_id).execute()
            logger.info(f"硬删除已导入用户 {user_id}")
            return jsonify({"success": True, "hard_delete": True})
        except Exception as e:
            # 如果硬删除失败（例如仍有其他关联记录），回退为软删除
            logger.error(f"硬删除失败，尝试软删除: {e}")
            try:
                db.table("users").update({"deleted_at": datetime.now(timezone.utc).isoformat()}).eq("id", user_id).execute()
                return jsonify({"success": True, "hard_delete": False, "fallback": True, "message": "用户存在关联记录，已转为软删除"})
            except Exception as soft_err:
                logger.error(f"软删除也失败: {soft_err}")
                return jsonify({"success": False, "message": str(soft_err)}), 500

    # ========= 已注册用户：软删除 =========
    try:
        db.table("users").update({"deleted_at": datetime.now(timezone.utc).isoformat()}).eq("id", user_id).execute()
        logger.info(f"软删除用户 {user_id}")
        return jsonify({"success": True, "hard_delete": False})
    except Exception as e:
        logger.error(f"软删除失败: {e}")
        return jsonify({"success": False, "message": str(e)}), 500

@admin_user_bp.route('/api/admin/users/deleted')
@login_required
@admin_required
def api_admin_deleted_users():
    """获取已删除用户列表（返回所有数据，由前端进行搜索和分页）"""
    db = get_supabase()
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 20, type=int)
    search = request.args.get('search', '').strip()
    country = request.args.get('country', '')
    
    # 查询所有已删除用户（不分页，返回全部数据）
    query = db.table("users").select("*").not_.is_("deleted_at", "null")
    
    # 只应用国家过滤（因为国家过滤比较简单）
    if country:
        query = query.eq("country", country)
    
    # 权限过滤
    allowed = get_admin_allowed_countries()
    if allowed is not None:
        if not allowed:
            return jsonify({"data": [], "total": 0, "page": page, "per_page": per_page})
        query = query.in_("country", allowed)
    
    # 按删除时间倒序
    res = query.order("deleted_at", desc=True).execute()
    all_users = res.data or []
    
    # 获取删除人姓名
    deleted_by_ids = [u.get('deleted_by') for u in all_users if u.get('deleted_by')]
    if deleted_by_ids:
        users_res = db.table("users").select("id, name_en").in_("id", deleted_by_ids).execute()
        name_map = {u['id']: u.get('name_en', '') for u in (users_res.data or [])}
        for u in all_users:
            u['deleted_by_name'] = name_map.get(u.get('deleted_by'), u.get('deleted_by', ''))
    
    # ✅ 前端内存过滤：返回所有数据，让前端进行搜索和分页
    return jsonify({
        "data": all_users, 
        "total": len(all_users), 
        "page": 1, 
        "per_page": len(all_users)
    })

@admin_user_bp.route('/api/admin/users/<user_id>/restore', methods=['POST'])
@login_required
@admin_required
def api_admin_restore_user(user_id):
    """恢复已删除用户"""
    db = get_supabase()
    
    # 检查用户是否存在且已删除
    user_res = db.table("users").select("*").eq("id", user_id).maybe_single().execute()
    if not user_res.data:
        return jsonify({"success": False, "message": "用户不存在"}), 404
    
    if user_res.data.get('deleted_at') is None:
        return jsonify({"success": False, "message": "用户未被删除"}), 400
    
    try:
        db.table("users").update({
            "deleted_at": None,
            "deleted_by": None
        }).eq("id", user_id).execute()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@admin_user_bp.route('/api/admin/users/batch_restore', methods=['POST'])
@login_required
@admin_required
def api_admin_batch_restore_users():
    """批量恢复已删除用户"""
    data = request.json
    user_ids = data.get('user_ids', [])
    
    if not user_ids:
        return jsonify({"success": False, "message": "请选择要恢复的用户"}), 400
    
    db = get_supabase()
    success_count = 0
    fail_count = 0
    
    for user_id in user_ids:
        try:
            db.table("users").update({
                "deleted_at": None,
                "deleted_by": None
            }).eq("id", user_id).execute()
            success_count += 1
        except Exception:
            fail_count += 1
    
    return jsonify({"success": True, "success_count": success_count, "fail_count": fail_count})

@admin_user_bp.route('/api/admin/users/<user_id>/permanent', methods=['DELETE'])
@login_required
@admin_required
def api_admin_permanent_delete_user(user_id):
    """永久删除用户（硬删除）"""
    db = get_supabase()
    
    try:
        # 先删除关联数据
        db.table("exam_assignments").delete().eq("user_id", user_id).execute()
        db.table("interview_results").delete().eq("user_id", user_id).execute()
        db.table("training_attendances").delete().eq("user_id", user_id).execute()
        # 最后删除用户
        db.table("users").delete().eq("id", user_id).execute()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@admin_user_bp.route('/api/admin/users/batch_permanent', methods=['POST'])
@login_required
@admin_required
def api_admin_batch_permanent_delete_users():
    """批量永久删除用户"""
    data = request.json
    user_ids = data.get('user_ids', [])
    
    if not user_ids:
        return jsonify({"success": False, "message": "请选择要删除的用户"}), 400
    
    db = get_supabase()
    success_count = 0
    fail_count = 0
    
    for user_id in user_ids:
        try:
            db.table("exam_assignments").delete().eq("user_id", user_id).execute()
            db.table("interview_results").delete().eq("user_id", user_id).execute()
            db.table("training_attendances").delete().eq("user_id", user_id).execute()
            db.table("users").delete().eq("id", user_id).execute()
            success_count += 1
        except Exception:
            fail_count += 1
    
    return jsonify({"success": True, "success_count": success_count, "fail_count": fail_count})

@admin_user_bp.route('/api/admin/users/<user_id>/reset_password', methods=['POST'])
@login_required
@admin_required
def api_admin_reset_user_password(user_id):
    """重置用户密码（生成新密码并发送邮件）"""
    db = get_supabase()
    # 获取用户信息（包括 is_protected）
    user_res = db.table("users").select("email, name_en, is_protected").eq("id", user_id).maybe_single().execute()
    if not user_res.data:
        return jsonify({"success": False, "message": "用户不存在"}), 404
    target_user = user_res.data
    if target_user.get('is_protected') and user_id != session['user_id']:
        return jsonify({"success": False, "message": "该账号被保护，无法重置密码"}), 403

    # 权限检查（开发者保护）
    current_user = get_current_user()
    if not can_modify_user(target_user, current_user, 'reset_password'):
        return jsonify({"success": False, "message": "该账号被保护，无法重置密码"}), 403

    email = target_user.get('email')
    if not email:
        return jsonify({"success": False, "message": "该用户没有邮箱，无法重置密码"}), 400

    new_password = ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(10))
    password_hash = auth.hash_password(new_password)
    
    try:
        db.table("users").update({"password_hash": password_hash}).eq("id", user_id).execute()
        # 发送邮件
        send_bilingual_notification(
            email=email,
            scenario=EmailScenario.PASSWORD_RESET,
            params={
                "name": target_user.get('name_en') or email,
                "new_password": new_password,
                "host_url": request.host_url,
            },
            host_url=request.host_url,
            auth_module=auth
        )
        return jsonify({"success": True, "new_password": new_password})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@admin_user_bp.route('/api/admin/refresh_permissions')
@login_required
@admin_required
def refresh_permissions():
    db = get_supabase()
    user_id = session['user_id']
    res = db.table("users").select("admin_countries, role, country").eq("id", user_id).maybe_single().execute()
    if res.data:
        user = res.data
        session['admin_countries'] = user.get('admin_countries')
        session['user_country'] = user.get('country')
        session['role'] = user.get('role')
        
        # ✅ 解析 admin_countries 返回给前端
        admin_countries = user.get('admin_countries')
        if admin_countries and isinstance(admin_countries, str):
            try:
                admin_countries = json.loads(admin_countries)
            except:
                admin_countries = []
        
        return jsonify({
            "success": True, 
            "admin_countries": admin_countries,
            "role": user.get('role'),
            "country": user.get('country')
        })
    return jsonify({"success": False, "message": "用户不存在"}), 404
