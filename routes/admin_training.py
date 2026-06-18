# routes/admin_training.py
import logging
import json
import pdfkit
import openpyxl
import traceback
from io import BytesIO
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from . import admin_training_bp
from config import Config
from services.db import get_supabase, get_supabase_admin
from services import auth
from datetime import datetime, timezone, timedelta
from services.export import find_wkhtmltopdf
from utils.training_helpers import get_training_country_templates_status, _save_country_template
from routes.helpers import login_required, admin_required, get_attendance_data, get_training_status, upload_signature, parse_exam_countries
from flask import render_template, request, redirect, send_file, url_for, session, flash, jsonify, make_response
from utils.common import match_country_code, quarter_to_date_range
from utils.email_notifier import  _send_training_notifications
from utils.permissions import get_admin_allowed_countries, get_allowed_countries, is_developer
logger = logging.getLogger(__name__)


@admin_training_bp.route('/admin/trainings')
@login_required
@admin_required
def admin_trainings():
    return render_template('admin/list_trainings.html')

@admin_training_bp.route('/api/admin/trainings', methods=['GET', 'POST', 'PUT', 'DELETE'])
@login_required
@admin_required
def api_admin_trainings():
    # 完整复制原 app.py api_admin_trainings 逻辑
    # 注意：将 upload_signature 替换为 helpers.upload_signature
    db = get_supabase_admin()
    
    if request.method == 'GET':
        # 获取过滤参数
        country_filter = request.args.get('country', '')   # 前端选择的国家筛选
        name = request.args.get('name', '')
        quarter = request.args.get('quarter', '')
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 20, type=int)

        # ✅ 添加调试日志
        logger.info("=" * 50)
        logger.info("调用 api_admin_trainings (GET)")
        logger.info(f"当前用户: role={session.get('role')}, user_country={session.get('user_country')}")
        logger.info(f"admin_countries={session.get('admin_countries')}")
        
        # ✅ 获取管理员的权限范围
        
        # 1. 获取所有培训（先不应用权限过滤，因为培训表的 country 字段可能为空）
        query = db.table("trainings").select("*")
        
        if name:
            query = query.ilike("name", f"%{name}%")

        res = query.execute()
        all_trainings = res.data or []
        
        logger.info(f"初始查询到 {len(all_trainings)} 条培训记录")
        
        # ✅ 2. 获取管理员允许的国家列表
        allowed_countries = get_admin_allowed_countries()
        logger.info(f"allowed_countries = {allowed_countries}")
        
        # ✅ 3. 根据权限范围过滤培训记录
        if allowed_countries is not None:
            if not allowed_countries:
                # 没有权限，返回空
                logger.warning("allowed_countries 为空，返回空列表")
                filtered_trainings = []
            else:
                # 获取允许国家下的所有用户ID（用于通过学员签到关联的培训）
                users_in_allowed = db.table("users").select("id").in_("country", allowed_countries).execute()
                allowed_user_ids = [u['id'] for u in (users_in_allowed.data or [])] if users_in_allowed.data else []
                logger.info(f"允许国家下的用户ID数量: {len(allowed_user_ids)}")
                
                # 查询存在允许国家学员签到的培训ID
                allowed_training_ids = set()
                if allowed_user_ids:
                    attend_res = db.table("training_attendances").select("training_id").in_("user_id", allowed_user_ids).execute()
                    allowed_training_ids = {a['training_id'] for a in (attend_res.data or [])}
                    logger.info(f"通过学员签到关联的培训ID: {allowed_training_ids}")
                
                # 过滤：培训自身 country 在允许列表中 或 培训ID在 allowed_training_ids 中
                filtered_trainings = []
                for training in all_trainings:
                    training_country = training.get('country')
                    if training_country and training_country in allowed_countries:
                        filtered_trainings.append(training)
                    elif training['id'] in allowed_training_ids:
                        filtered_trainings.append(training)
                
                logger.info(f"权限过滤后剩余 {len(filtered_trainings)} 条培训记录")
        else:
            # 无限制（开发者或超管）
            filtered_trainings = all_trainings
            logger.info("无权限限制，返回所有培训")
        
        # 4. 前端选择的额外国家筛选（与权限取交集）
        if country_filter:
            filter_country_code = match_country_code(country_filter) if country_filter else None
            if filter_country_code:
                # 获取该国家下的用户ID
                users_in_filter = db.table("users").select("id").eq("country", filter_country_code).execute()
                filter_user_ids = [u['id'] for u in (users_in_filter.data or [])] if users_in_filter.data else []
                filter_training_ids = set()
                if filter_user_ids:
                    attend_filter = db.table("training_attendances").select("training_id").in_("user_id", filter_user_ids).execute()
                    filter_training_ids = {a['training_id'] for a in (attend_filter.data or [])}
                
                # 过滤
                temp_filtered = []
                for training in filtered_trainings:
                    if training.get('country') == filter_country_code or training['id'] in filter_training_ids:
                        temp_filtered.append(training)
                filtered_trainings = temp_filtered
                logger.info(f"前端国家筛选后剩余 {len(filtered_trainings)} 条培训记录")

        # 5. 季度过滤（内存中）
        if quarter:
            q_start, q_end = quarter_to_date_range(quarter)
            if q_start and q_end:
                q_start_dt = datetime.fromisoformat(q_start)
                q_end_dt = datetime.fromisoformat(q_end)
                temp_filtered = []
                for training in filtered_trainings:
                    start = training.get('start_time')
                    end = training.get('end_time')
                    if start and end:
                        try:
                            start_dt = datetime.fromisoformat(start)
                            end_dt = datetime.fromisoformat(end)
                            if start_dt <= q_end_dt and end_dt >= q_start_dt:
                                temp_filtered.append(training)
                        except:
                            pass
                filtered_trainings = temp_filtered
                logger.info(f"季度过滤后剩余 {len(filtered_trainings)} 条培训记录")

        # 6. 按创建时间倒序排序
        filtered_trainings.sort(key=lambda x: x.get('created_at', ''), reverse=True)

        # 7. 手动分页
        total = len(filtered_trainings)
        start_idx = (page - 1) * per_page
        end_idx = start_idx + per_page
        paginated = filtered_trainings[start_idx:end_idx]

        # 8. 补充签到人数和动态状态
        now = datetime.now(timezone.utc)
        for t in paginated:
            # ✅ 修改：只统计在职人员的签到
            signed_count = db.table("training_attendances") \
                .select("id, user_id", count="exact") \
                .eq("training_id", t['id']) \
                .execute()
            
            # 获取签到用户的ID列表
            signed_user_ids = [s['user_id'] for s in (signed_count.data or [])]
            
            # ✅ 只统计在职人员的签到
            if signed_user_ids:
                active_users = db.table("users").select("id").in_("id", signed_user_ids).eq("is_resign", False).execute()
                active_user_ids = [u['id'] for u in (active_users.data or [])]
                t['signed_count'] = len(active_user_ids)
            else:
                t['signed_count'] = 0

            # ✅ 计算动态状态
            start_time = t.get('start_time')
            end_time = t.get('end_time')
            
            if not start_time or not end_time:
                t['dynamic_status'] = 'draft'
            else:
                try:
                    # 解析时间
                    start_dt = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
                    end_dt = datetime.fromisoformat(end_time.replace('Z', '+00:00'))
                    
                    if now < start_dt:
                        t['dynamic_status'] = 'pending'
                    elif now > end_dt:
                        t['dynamic_status'] = 'closed'
                    else:
                        t['dynamic_status'] = 'active'
                except Exception as e:
                    logger.warning(f"解析培训 {t['id']} 时间失败: {e}")
                    t['dynamic_status'] = 'draft'
            
            # 检查是否有多个国家的模板（用于前端禁用表头录入按钮）
            if not t.get('country'):
                att_users = db.table("training_attendances").select("user_id").eq("training_id", t['id']).execute()
                user_ids = [u['user_id'] for u in (att_users.data or [])] if att_users.data else []
                countries = set()
                if user_ids:
                    users_res = db.table("users").select("country").in_("id", user_ids).execute()
                    countries = {u['country'] for u in (users_res.data or []) if u.get('country')}
                
                if len(countries) > 1:
                    t['has_inconsistent_templates'] = True
                else:
                    ct_res = db.table("training_country_templates").select("country, header_template").eq("training_id", t['id']).execute()
                    templates = ct_res.data or []
                    if not templates:
                        t['has_inconsistent_templates'] = False
                    else:
                        unique_templates = set()
                        for ct in templates:
                            tpl = ct.get('header_template', {})
                            unique_templates.add(json.dumps(tpl, sort_keys=True))
                        t['has_inconsistent_templates'] = len(unique_templates) > 1
            else:
                t['has_inconsistent_templates'] = False

        logger.info(f"最终返回 {len(paginated)} 条培训记录")
        return jsonify({
            "data": paginated,
            "total": total,
            "page": page,
            "per_page": per_page
        })

    elif request.method == 'POST':
        # 创建培训（也需要权限校验）
        data = request.json
        name = data.get('name')
        if not name:
            return jsonify({"success": False, "message": "jsonify_training_name_cannot_empty", "params": []}), 400
        
        # ✅ 从数据库实时获取管理员的权限范围，而不是依赖 session
        current_user_id = session.get('user_id')
        current_role = session.get('role')
        
        # 获取用户的权限范围（直接从数据库）
        user_res = db.table("users").select("admin_countries, country").eq("id", current_user_id).maybe_single().execute()
        user_data = user_res.data if user_res and user_res.data else {}
        
        # 计算允许的国家列表
        allowed = None
        if current_role == 'developer':
            # ✅ developer 无限制
            allowed = None
        elif current_role == 'super_admin':
            admin_countries = user_data.get('admin_countries')
            if admin_countries:
                try:
                    allowed = json.loads(admin_countries) if isinstance(admin_countries, str) else admin_countries
                except:
                    allowed = None
            # 超管如果没有设置权限范围，则无限制
        elif current_role == 'admin':
            admin_countries = user_data.get('admin_countries')
            if admin_countries:
                try:
                    allowed = json.loads(admin_countries) if isinstance(admin_countries, str) else admin_countries
                except:
                    allowed = None
            
            # 如果没有设置权限范围，使用用户所在国家
            if not allowed:
                user_country = user_data.get('country')
                if user_country:
                    allowed = [user_country]
                else:
                    allowed = []
        
        training_country = data.get('country')
        
        # 详细日志
        logger.info("=" * 50)
        logger.info("创建培训 POST 请求")
        logger.info(f"用户ID: {current_user_id}, 角色: {current_role}")
        logger.info(f"数据库 admin_countries: {user_data.get('admin_countries')}")
        logger.info(f"数据库 user_country: {user_data.get('country')}")
        logger.info(f"计算后 allowed: {allowed}")
        logger.info(f"请求中的 training_country: {training_country}")
        
        # # 权限检查
        if allowed is not None:
            if not allowed:
                logger.warning(f"管理员没有任何国家权限，禁止创建培训")
                return jsonify({"success": False, "message": "jsonify_no_country_permission", "params": []}), 403
            
            # 检查指定的国家是否在允许范围内
            if not training_country:
                # 未指定国家，但管理员有权限范围，拒绝
                logger.warning(f"未指定国家，但管理员权限范围={allowed}")
                return jsonify({"success": False, "message": "jsonify_country_required", "params": []}), 400
            
            if training_country not in allowed:
                logger.warning(f"权限拒绝: {training_country} 不在 {allowed} 中")
                return jsonify({
                    "success": False, 
                    "message": "jsonify_no_authority_creat_training_this_county", "params": []
                }), 403
        else:
            logger.info("无权限限制（超管或开发者）")

        # 如果没有传递国家，且管理员有默认国家，则使用默认国家
        if not training_country and allowed and len(allowed) == 1:
            training_country = allowed[0]
            logger.info(f"自动使用默认国家: {training_country}")
        
        start_time = data.get('start_time')
        end_time = data.get('end_time')

        if not start_time:
            start_time = datetime.now(timezone.utc).isoformat()
        if not end_time:
            end_time = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
            
        res = db.table("trainings").insert({
            "name": name,
            "start_time": start_time,
            "end_time": end_time,
            "header_template": data.get('header_template', {}),
            "country": training_country,
            "quarter": data.get('quarter', ''),
            "is_active": False
        }).execute()
        
        logger.info(f"创建培训成功: id={res.data[0]['id']}, name={name}")
        return jsonify({"success": True, "id": res.data[0]['id']})
    
    elif request.method == 'PUT':
        # 更新培训（需要权限校验）
        logger.info(f"========== PUT 请求收到 ==========")
        logger.info(f"请求数据: {request.json}")
        data = request.json
        tid = data.get('id')
        if tid is None or tid == 'None' or str(tid).lower() == 'null':
            return jsonify({"success": False, "message": "无效的培训ID"}), 400
        
        try:
            tid = int(tid)
        except (ValueError, TypeError):
            return jsonify({"success": False, "message": "培训ID必须是整数"}), 400
        
        # ✅ 获取原培训信息，检查权限
        original = db.table("trainings").select("country").eq("id", tid).maybe_single().execute()
        if original.data:
            allowed = get_admin_allowed_countries()
            original_country = original.data.get('country')
            if allowed is not None and original_country and original_country not in allowed:
                return jsonify({"success": False, "message": "jsonify_no_authority_modify_training", "params": []}), 403
        
        # 处理 header_template 保存
        country_code = data.get('country_code')
        header_template = data.get('header_template')
        if header_template is not None:
            if country_code:
                # 检查国家权限
                allowed = get_admin_allowed_countries()
                if allowed is not None and country_code not in allowed:
                    return jsonify({"success": False, "message": "jsonify_no_authorith_set_up_header_template", "params": []}), 403
                # ✅ 调用辅助函数保存模板
                _save_country_template(db, tid, country_code, header_template)
            else:
                db.table("trainings").update({"header_template": header_template}).eq("id", tid).execute()
            #return jsonify({"success": True})
    
        # ✅ 记录是否推送（用于发送邮件）
        is_push = data.get('is_active', False)
        start_time = data.get('start_time')
        end_time = data.get('end_time')
        user_ids = data.get('user_ids')  # 选中的用户ID列表
        
        update_data = {}
        if 'name' in data:
            update_data['name'] = data['name']
        if 'header_template' in data:
            update_data['header_template'] = data['header_template']
        if 'start_time' in data and data['start_time'] is not None:
            update_data['start_time'] = data['start_time']
        if 'end_time' in data and data['end_time'] is not None:
            update_data['end_time'] = data['end_time']
        if 'is_active' in data:
            update_data['is_active'] = data['is_active']
        if 'country' in data:
            # 检查新国家权限
            new_country = data['country']
            allowed = get_admin_allowed_countries()
            if allowed is not None and new_country and new_country not in allowed:
                return jsonify({"success": False, "message": "jsonify_no_authorith_transfer_training_to_country", "params": []}), 403
            update_data['country'] = new_country
        if 'quarter' in data:
            update_data['quarter'] = data['quarter']
        
        if update_data:
            db.table("trainings").update(update_data).eq("id", tid).execute()
            logger.info(f"更新培训成功: id={tid}, 更新字段={list(update_data.keys())}")

        # ✅ 新增：处理培训-学员分配关系（定点推送）
        user_ids = data.get('user_ids')
        if user_ids is not None:  # 注意：空数组表示清空所有分配
            # 先删除该培训的所有现有分配
            db.table("training_assignments").delete().eq("training_id", tid).execute()
            
            # 如果有指定用户，插入新的分配关系
            if len(user_ids) > 0:
                assignments = [{"training_id": tid, "user_id": uid, "created_by": session.get('user_id')} for uid in user_ids]
                db.table("training_assignments").insert(assignments).execute()
                logger.info(f"培训 {tid} 定点推送给 {len(user_ids)} 名学员")
            else:
                logger.info(f"培训 {tid} 清空了所有分配（推送给全国）")
                        
        # 在 PUT 方法中，处理 user_ids 的地方
        logger.info(f"========== 培训推送 ==========")
        logger.info(f"培训ID: {tid}")
        logger.info(f"user_ids: {user_ids}")
        logger.info(f"user_ids 类型: {type(user_ids)}")
        logger.info(f"user_ids 长度: {len(user_ids) if user_ids else 0}")

        # 如果只是保存表头（没有推送字段），可以提前返回
        if not is_push and not start_time and not end_time and user_ids is None:
            return jsonify({"success": True})
        
        # 发送培训推送邮件通知
        if data.get('is_active') and start_time and end_time:
            # 异步发送邮件，避免阻塞请求
            import threading
            thread = threading.Thread(
                target=_send_training_notifications,
                args=(tid, start_time, end_time, user_ids, request.host_url)
            )
            thread.daemon = True
            thread.start()
            logger.info(f"培训 {tid} 邮件通知已加入发送队列")
        
        return jsonify({"success": True})
    
    elif request.method == 'DELETE':
        tid = request.args.get('id')
        if not tid:
            return jsonify({"success": False, "message": "jsonify_lack_training_id", "params": []}), 400
        
        # 删除前检查权限
        original = db.table("trainings").select("country", created_by).eq("id", tid).maybe_single().execute()
        if original.data:
            allowed = get_admin_allowed_countries()
            original_country = original.data.get('country')
            created_by = original.data.get('created_by')
            current_user_id = session.get('user_id')
            current_role = session.get('role')
            is_dev = is_developer()
            if allowed is not None and original_country and original_country not in allowed:
                return jsonify({"success": False, "message": "jsonify_no_authorith_delete_training", "params": []}), 403

            # ✅ 创建者检查（非超管/开发者）
            if not is_dev and current_role != 'super_admin':
                if created_by != current_user_id:
                    return jsonify({"success": False, "message": "jsonify_no_permmission_delete_item_created_by_others", "params": []}), 403
        
        db.table("trainings").delete().eq("id", tid).execute()
        logger.info(f"删除培训成功: id={tid}")
        return jsonify({"success": True})

@admin_training_bp.route('/api/training/attendance/<int:training_id>')
@login_required
@admin_required
def api_training_attendance(training_id):
    db = get_supabase()
    country = request.args.get('country', '')

    # ✅ 获取当前管理员的权限范围
    allowed_countries = get_admin_allowed_countries()
    
    # 获取培训基本信息
    training_res = db.table("trainings").select("*").eq("id", training_id).maybe_single().execute()
    if not training_res.data:
        return jsonify({"error": "培训不存在"}), 404
    training = training_res.data
    
    # ✅ 权限检查：培训本身的国家必须在允许范围内
    training_country = training.get('country')
    if allowed_countries is not None:
        if not allowed_countries:
            return jsonify({"attendances": [], "training": training, "header_template": {}})
        if training_country and training_country not in allowed_countries:
            # 培训不在权限范围内，返回空数据
            return jsonify({"attendances": [], "training": training, "header_template": {}})
    
    # 签到记录查询
    att_res = db.table("training_attendances") \
        .select("id, user_id, signature_url, signed_name, sign_time, users(email, name_cn, name_en, department, employee_id, country, company, is_resign)") \
        .eq("training_id", training_id) \
        .execute()

    att_list = att_res.data or []

    # ✅ 过滤掉离职人员的签到记录
    att_list = [rec for rec in att_list if not rec.get('users', {}).get('is_resign', False)]
    
    # ✅ 按国家权限过滤签到记录（基于用户的国家）
    if allowed_countries is not None:
        if not allowed_countries:
            att_list = []
        else:
            filtered_list = []
            for rec in att_list:
                user = rec.get('users', {})
                user_country = user.get('country')
                if user_country in allowed_countries:
                    filtered_list.append(rec)
            att_list = filtered_list
    
    # 手动按国家过滤
    if country:
        att_list = [rec for rec in att_list if rec.get('users', {}).get('country') == country]

    attendance_list = []
    for rec in att_list or []:
        user = rec.get('users', {})
        # 防止 user 为 None
        if user is None:
            user = {}

        attendance_list.append({
            "id": rec['id'],  # ✅ 新增签到记录ID
            "user_id": rec['user_id'],
            "department": user.get('department', ''),
            "name_cn": user.get('name_cn', ''),
            "name_en": user.get('name_en', ''),
            "employee_id": user.get('employee_id', ''),
            "signed_name": rec.get('signed_name', ''),
            "signature_url": rec.get('signature_url', ''),
            "sign_time": rec.get('sign_time'),
            "company": user.get('company', ''),
            "country": user.get('country', '')
        })

    # 获取表头模板：优先取国家模板，否则取培训主模板
    header_template = None
    if country:
        # 查询国家模板
        ct_res = db.table("training_country_templates")\
            .select("header_template")\
            .eq("training_id", training_id)\
            .eq("country", country)\
            .execute()
        if ct_res.data and len(ct_res.data) > 0:
            header_template = ct_res.data[0].get('header_template')
    if not header_template:
        header_template = training.get('header_template', {})

    return jsonify({
        "training": training,
        "attendances": attendance_list,
        "header_template": header_template
    })

@admin_training_bp.route('/api/admin/training/<int:training_id>/country_templates_status')
@login_required
@admin_required
def training_country_templates_status(training_id):
    return jsonify(get_training_country_templates_status(training_id))

@admin_training_bp.route('/api/admin/training/<int:training_id>/country_template', methods=['GET'])
@login_required
@admin_required
def get_training_country_template(training_id):
    """1. 获取国家模板接口"""
    country = request.args.get('country')
    if not country:
        return jsonify({"error": "缺少 country 参数"}), 400
    db = get_supabase()
    # 使用 execute()，不用 maybe_single()
    res = db.table("training_country_templates")\
        .select("header_template")\
        .eq("training_id", training_id)\
        .eq("country", country)\
        .execute()
    if res.data and len(res.data) > 0:
        template = res.data[0].get('header_template', {})
    else:
        template = {}
    return jsonify({"template": template})

@admin_training_bp.route('/api/admin/training/<int:training_id>/country_template', methods=['POST'])
@login_required
@admin_required
def save_training_country_template(training_id):
    """2. 保存国家模板接口 保存培训表头模板"""
    data = request.json
    country = data.get('country')
    template = data.get('template')
    
    if not training_id:
        return jsonify({"success": False, "message": "培训ID无效"}), 400
    
    if template is None:
        return jsonify({"success": False, "message": "缺少 template 参数"}), 400
    
    db = get_supabase()
    
    try:
        # 情况1：没有指定国家 → 保存到培训主表的 header_template
        if not country or country == 'null' or country == 'undefined' or country == '':
            db.table("trainings").update({
                "header_template": template
            }).eq("id", training_id).execute()
            logger.info(f"保存培训主表头: training_id={training_id}")
            return jsonify({"success": True})
        
        # 情况2：指定了国家 → 保存到国家模板表
        check_res = db.table("training_country_templates")\
            .select("id")\
            .eq("training_id", training_id)\
            .eq("country", country)\
            .execute()
        
        if check_res.data and len(check_res.data) > 0:
            # 更新现有记录
            db.table("training_country_templates")\
                .update({
                    "header_template": template, 
                    "updated_at": datetime.now(timezone.utc).isoformat()
                })\
                .eq("id", check_res.data[0]['id'])\
                .execute()
            logger.info(f"更新国家模板: training_id={training_id}, country={country}")
        else:
            # 插入新记录
            db.table("training_country_templates")\
                .insert({
                    "training_id": training_id, 
                    "country": country, 
                    "header_template": template
                })\
                .execute()
            logger.info(f"插入国家模板: training_id={training_id}, country={country}")
        
        return jsonify({"success": True})
        
    except Exception as e:
        logger.error(f"保存表头失败: {e}")
        return jsonify({"success": False, "message": str(e)}), 500

@admin_training_bp.route('/api/admin/training/attendance/<int:attendance_id>/reset-signature', methods=['POST'])
@login_required
@admin_required
def admin_reset_signature(attendance_id):
    """管理员清除指定签到记录的签名，推送重新签字到学员端"""
    db = get_supabase()
    # 获取原记录，保留签到时间
    att_res = db.table("training_attendances").select("*").eq("id", attendance_id).maybe_single().execute()
    if not att_res.data:
        return jsonify({"success": False, "message": "签到记录不存在"}), 404
    
    # 清空签名相关字段，保留 sign_time
    db.table("training_attendances").update({
        "signature_url": None,
        "signed_name": None
    }).eq("id", attendance_id).execute()
    
    logger.info(f"管理员重置签到 {attendance_id} 的签名")
    return jsonify({"success": True})

@admin_training_bp.route('/admin/training/<int:training_id>/attendance')
@login_required
@admin_required
def admin_training_attendance(training_id):
    return render_template('admin/list_training_attendance.html', training_id=training_id)

@admin_training_bp.route('/admin/training/<int:training_id>/attendance/print')
@login_required
@admin_required
def training_attendance_print(training_id):
    return render_template('admin/list_training_attendance.html', training_id=training_id, print_mode=True)

@admin_training_bp.route('/admin/training/<int:training_id>/attendance/pdf')
@login_required
@admin_required
def download_training_attendance_pdf(training_id):
    country = request.args.get('country', '')
    data = get_attendance_data(training_id, country)
    if not data:
        flash("培训不存在", "danger")
        return redirect(url_for('admin_dashboard'))

    html_content = render_template('admin/attendance_pdf.html',
                                    training=data['training'],        # ✅ 新增
                                    header=data['header_template'],
                                    attendances=data['attendances'])

    # 配置 wkhtmltopdf 路径（根据实际安装位置修改）
    wkhtmltopdf_path = find_wkhtmltopdf()   # 自动查找（支持环境变量 WKHTMLTOPDF_PATH）
    config = pdfkit.configuration(wkhtmltopdf=wkhtmltopdf_path)
    pdf = pdfkit.from_string(html_content, False, configuration=config,
                             options={
                                'page-size': 'A4',
                                'margin-top': '10mm',
                                'margin-bottom': '10mm',
                                'margin-left': '10mm',
                                'margin-right': '10mm',
                                'encoding': 'UTF-8',
                                'enable-local-file-access': None,
                                # 可选：避免因网络图片慢而超时
                                'javascript-delay': '200',
                                'no-stop-slow-scripts': None,
                             })
    response = make_response(pdf)
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = f'inline; filename=attendance_{training_id}.pdf'
    return response

@admin_training_bp.route('/api/admin/training/<int:training_id>/attendance_by_country')
@login_required
@admin_required
def api_training_attendance_by_country(training_id):
    db = get_supabase()
    allowed = get_allowed_countries()
    
    # 获取该培训的所有签到记录，并关联用户国家
    att_res = db.table("training_attendances") \
        .select("id, user_id, signed_name, signature_url, sign_time, users(country, name_cn, name_en, department, employee_id)") \
        .eq("training_id", training_id) \
        .execute()
    records = att_res.data or []
    
    # 国家权限过滤（仅保留允许国家的记录）
    if allowed is not None:
        if not allowed:
            return jsonify([])
        records = [r for r in records if r.get('users', {}).get('country') in allowed]
    
    # 按国家分组
    groups = {}
    for rec in records:
        user = rec.get('users', {})
        country = user.get('country') or '未指定'
        if country not in groups:
            groups[country] = {
                'country': country,
                'count': 0,
                'attendances': []
            }
        groups[country]['count'] += 1
        groups[country]['attendances'].append({
            'user_id': rec['user_id'],
            'department': user.get('department', ''),
            'name_cn': user.get('name_cn', ''),
            'name_en': user.get('name_en', ''),
            'employee_id': user.get('employee_id', ''),
            'signed_name': rec.get('signed_name', ''),
            'signature_url': rec.get('signature_url', ''),
            'sign_time': rec.get('sign_time')
        })
    return jsonify(list(groups.values()))

@admin_training_bp.route('/api/admin/training/attendance/<int:attendance_id>', methods=['DELETE'])
@login_required
@admin_required
def api_admin_delete_training_attendance(attendance_id):
    """删除培训签到记录"""
    db = get_supabase()
    user_id = session['user_id']
    
    # 1. 获取签到记录
    att_res = db.table("training_attendances").select("*").eq("id", attendance_id).execute()
    if not att_res.data:
        return jsonify({"success": False, "message": "签到记录不存在"}), 404
    
    attendance = att_res.data[0]
    training_id = attendance.get('training_id')
    
    # 2. 获取培训国家
    training_res = db.table("trainings").select("country").eq("id", training_id).maybe_single().execute()
    training_country = training_res.data.get('country') if training_res.data else None
    
    # 3. 权限检查
    allowed = get_admin_allowed_countries()
    if allowed is not None and training_country not in allowed:
        return jsonify({"success": False, "message": "无权删除此签到记录"}), 403
    
    try:
        # 软删除
        db.table("training_attendances").update({
            "deleted_at": datetime.now(timezone.utc).isoformat(),
            "deleted_by": user_id
        }).eq("id", attendance_id).execute()
        
        logger.info(f"培训签到记录已删除: attendance_id={attendance_id}, 操作人={user_id}")
        return jsonify({"success": True, "message": "签到记录已删除"})
    except Exception as e:
        logger.error(f"删除签到记录失败: {e}")
        return jsonify({"success": False, "message": str(e)}), 500

@admin_training_bp.route('/api/admin/training/attendance/batch_delete', methods=['POST'])
@login_required
@admin_required
def api_admin_batch_delete_training_attendances():
    """批量删除培训签到记录（支持软删除和永久删除）"""
    data = request.json
    attendance_ids = data.get('ids', [])
    delete_type = data.get('delete_type', 'soft')
    
    if not attendance_ids:
        return jsonify({"success": False, "message": "请选择要删除的签到记录"}), 400
    
    db = get_supabase()
    user_id = session['user_id']
    success_count = 0
    fail_count = 0
    errors = []
    
    allowed = get_admin_allowed_countries()
    
    for att_id in attendance_ids:
        try:
            att_res = db.table("training_attendances").select("*").eq("id", att_id).execute()
            if not att_res.data:
                fail_count += 1
                errors.append(f"记录 {att_id} 不存在")
                continue
            
            attendance = att_res.data[0]
            training_id = attendance.get('training_id')
            
            training_res = db.table("trainings").select("country").eq("id", training_id).maybe_single().execute()
            training_country = training_res.data.get('country') if training_res.data else None
            
            if allowed is not None and training_country not in allowed:
                fail_count += 1
                errors.append(f"记录 {att_id}: 无权限删除")
                continue
            
            if delete_type == 'hard':
                db.table("training_attendances").delete().eq("id", att_id).execute()
            else:
                db.table("training_attendances").update({
                    "deleted_at": datetime.now(timezone.utc).isoformat(),
                    "deleted_by": user_id
                }).eq("id", att_id).execute()
            
            success_count += 1
        except Exception as e:
            fail_count += 1
            errors.append(f"记录 {att_id}: {str(e)}")
    
    return jsonify({
        "success": True,
        "success_count": success_count,
        "fail_count": fail_count,
        "errors": errors[:10]
    })

@admin_training_bp.route('/admin/training/attendance_status_view')
@login_required
@admin_required
def training_attendance_status_view():
    """培训签到人员状态查看页面"""
    return render_template('admin/list_training_status_view.html')

@admin_training_bp.route('/api/admin/training/users_with_status')
@login_required
@admin_required
def api_training_users_with_status():
    """获取权限范围内所有培训的学员签到状态（只显示培训对应国家的学员）"""
    db = get_supabase()
    admin_db = get_supabase_admin()  # 需要管理员客户端来读取分配关系
    
    # 获取当前管理员的权限范围
    allowed_countries = get_admin_allowed_countries()
    
    # 1. 获取权限范围内的所有培训
    trainings_query = db.table("trainings").select("id, name, country").is_("deleted_at", "null")
    
    if allowed_countries is not None:
        if not allowed_countries:
            return jsonify({"data": []})
        trainings_query = trainings_query.in_("country", allowed_countries)
    
    trainings_res = trainings_query.execute()
    trainings = trainings_res.data or []
    
    if not trainings:
        return jsonify({"data": []})
    
    # 2. 获取所有培训涉及的国家列表（用于后续过滤用户）
    training_countries = list(set([t.get('country') for t in trainings if t.get('country')]))
    
    # 3. 只获取培训国家范围内的用户
    users_query = db.table("users").select("*").is_("deleted_at", "null").eq("user_status", "registered").eq("is_resign", False)
    
    if training_countries:
        users_query = users_query.in_("country", training_countries)
    
    # 额外筛选条件
    search = request.args.get('search', '')
    country = request.args.get('country', '')
    wh_id = request.args.get('wh_id', '')
    
    if search:
        users_query = users_query.or_(f"name_en.ilike.%{search}%,email.ilike.%{search}%")
    if country:
        users_query = users_query.eq("country", country)
    if wh_id:
        users_query = users_query.filter("wh_id", "ilike", f"%{wh_id}%")
    
    users_res = users_query.execute()
    users = users_res.data or []
    user_ids = [u['id'] for u in users]
    
    if not user_ids:
        return jsonify({"data": []})
    
    # 4. 获取所有培训的分配记录（关键修复）
    training_ids = [t['id'] for t in trainings]
    assign_res = admin_db.table("training_assignments").select("training_id, user_id")\
        .in_("training_id", training_ids)\
        .execute()

    # 构建分配映射 (training_id, user_id) -> assigned
    assignment_map = {}
    for a in (assign_res.data or []):
        key = f"{a['training_id']}_{a['user_id']}"
        assignment_map[key] = True
    
    # 5. 获取所有培训的签到记录
    att_res = db.table("training_attendances").select(
        "training_id, user_id, sign_time, signed_name, signature_url"
    ).in_("training_id", training_ids).in_("user_id", user_ids).is_("deleted_at", "null").execute()
    
    # 构建签到记录映射
    attendance_map = {}
    for att in (att_res.data or []):
        key = f"{att['training_id']}_{att['user_id']}"
        attendance_map[key] = {
            "sign_time": att.get('sign_time'),
            "signed_name": att.get('signed_name', ''),
            "signature_url": att.get('signature_url', '')
        }
    
    # 6. 构建返回数据（关键：只显示培训国家对应的学员，基于分配关系判断状态）
    result = []
    for training in trainings:
        training_id = training['id']
        training_name = training.get('name', '')
        training_country = training.get('country', '')
        
        # 只遍历与该培训国家匹配的用户
        for user in users:
            user_country = user.get('country', '')
            
            # 用户国家必须与培训国家一致
            if user_country != training_country:
                continue
            
            key = f"{training_id}_{user['id']}"

            is_assigned = key in assignment_map
            has_attendance = key in attendance_map
            attendance = attendance_map.get(key, {})
            sign_time = attendance.get('sign_time')
            signature_url = attendance.get('signature_url', '')
            
            # ✅ 新的状态判断逻辑
            if not is_assigned and not has_attendance:
                # 既没有分配也没有签到：未分配
                sign_status = 'not_assigned'
            elif not sign_time:
                # 有分配但未签到：待签到
                sign_status = 'pending'
            elif not signature_url:
                # 已签到但无签名：需重新签名
                sign_status = 'resign'
            else:
                # 已签到且有签名：已签到
                sign_status = 'signed'
 
            result.append({
                "training_id": training_id,
                "training_name": training_name,
                "training_country": training_country,
                "user_id": user['id'],
                "country": user_country,
                "name_en": user.get('name_en', ''),
                "email": user.get('email', ''),
                "wh_id": user.get('wh_id', ''),
                "wh_name_en": user.get('wh_name_en', ''),
                "company": user.get('company', ''),
                "sign_status": sign_status,
                "sign_time": sign_time,
                "signed_name": attendance.get('signed_name', ''),
                "signature_url": signature_url
            })
    
    # 按培训名称和学员姓名排序
    result.sort(key=lambda x: (x.get('training_name', ''), x.get('name_en', '')))
    
    return jsonify({"data": result})

@admin_training_bp.route('/api/admin/training/export_attendance_status')
@login_required
@admin_required
def export_training_attendance_status():
    """导出培训签到状态为Excel（只显示培训对应国家的学员）"""
    from io import BytesIO
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from datetime import datetime
    
    db = get_supabase()
    
    # 获取当前管理员的权限范围
    allowed_countries = get_admin_allowed_countries()
    
    # 1. 获取权限范围内的所有培训
    trainings_query = db.table("trainings").select("id, name, country").is_("deleted_at", "null")
    
    if allowed_countries is not None:
        if not allowed_countries:
            return jsonify({"error": "无权限"}), 403
        trainings_query = trainings_query.in_("country", allowed_countries)
    
    trainings_res = trainings_query.execute()
    trainings = trainings_res.data or []
    
    if not trainings:
        return jsonify({"error": "没有可导出的数据"}), 404
    
    # 2. 获取培训涉及的国家列表
    training_countries = list(set([t.get('country') for t in trainings if t.get('country')]))
    
    # 3. 获取用户
    users_query = db.table("users").select("*").is_("deleted_at", "null").eq("user_status", "registered")
    
    if training_countries:
        users_query = users_query.in_("country", training_countries)
    
    # 筛选条件
    search = request.args.get('search', '')
    country = request.args.get('country', '')
    wh_id = request.args.get('wh_id', '')
    
    if search:
        users_query = users_query.or_(f"name_en.ilike.%{search}%,email.ilike.%{search}%")
    if country:
        users_query = users_query.eq("country", country)
    if wh_id:
        users_query = users_query.filter("wh_id", "ilike", f"%{wh_id}%")
    
    users_res = users_query.execute()
    users = users_res.data or []
    user_ids = [u['id'] for u in users]
    
    # 4. 获取签到记录
    attendance_map = {}
    if user_ids:
        training_ids = [t['id'] for t in trainings]
        att_res = db.table("training_attendances").select(
            "training_id, user_id, sign_time, signed_name, signature_url"
        ).in_("training_id", training_ids).in_("user_id", user_ids).is_("deleted_at", "null").execute()
        
        for att in (att_res.data or []):
            key = f"{att['training_id']}_{att['user_id']}"
            attendance_map[key] = {
                "sign_time": att.get('sign_time'),
                "signed_name": att.get('signed_name', ''),
                "signature_url": att.get('signature_url', '')
            }
    
    # 创建工作簿
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "培训签到状态"
    
    # 表头样式
    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
    thin_border = Border(
        left=Side(style="thin"), right=Side(style="thin"),
        top=Side(style="thin"), bottom=Side(style="thin")
    )
    
    # 表头
    headers = ["序号", "培训名称", "培训国家", "学员国家", "学员姓名", "邮箱", "库房编码", "库房名称", "公司", "签到时间", "签名姓名", "签到状态"]
    for col, header in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col, value=header)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = thin_border
    
    # 数据行
    status_map = {
        'signed': '已签到',
        'pending': '待签到',
        'resign': '需重新签名'
    }
    
    row_idx = 2
    for training in trainings:
        training_name = training.get('name', '')
        training_country = training.get('country', '')
        
        for user in users:
            user_country = user.get('country', '')
            
            # ✅ 关键：只导出培训国家对应的学员
            if user_country != training_country:
                continue
            
            key = f"{training['id']}_{user['id']}"
            attendance = attendance_map.get(key, {})
            sign_time = attendance.get('sign_time')
            signature_url = attendance.get('signature_url', '')
            
            if not sign_time:
                sign_status = 'pending'
            elif not signature_url:
                sign_status = 'resign'
            else:
                sign_status = 'signed'
            
            ws.cell(row=row_idx, column=1, value=row_idx - 1)
            ws.cell(row=row_idx, column=2, value=training_name)
            ws.cell(row=row_idx, column=3, value=training_country)
            ws.cell(row=row_idx, column=4, value=user_country)
            ws.cell(row=row_idx, column=5, value=user.get('name_en', ''))
            ws.cell(row=row_idx, column=6, value=user.get('email', ''))
            ws.cell(row=row_idx, column=7, value=user.get('wh_id', ''))
            ws.cell(row=row_idx, column=8, value=user.get('wh_name_en', ''))
            ws.cell(row=row_idx, column=9, value=user.get('company', ''))
            ws.cell(row=row_idx, column=10, value=sign_time[:19] if sign_time else '')
            ws.cell(row=row_idx, column=11, value=attendance.get('signed_name', ''))
            ws.cell(row=row_idx, column=12, value=status_map.get(sign_status, '-'))
            
            row_idx += 1
    
    # 调整列宽
    column_widths = [6, 30, 12, 12, 15, 25, 15, 20, 20, 20, 15, 12]
    for col, width in enumerate(column_widths, 1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(col)].width = width
    
    buffer = BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    
    filename = f"培训签到状态_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    
    return send_file(
        buffer,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name=filename
    )

@admin_training_bp.route('/api/search/trainings')
@login_required
@admin_required
def search_trainings():
    """模糊搜索培训名称（带权限过滤 + 级联筛选 + 绑定标记）"""
    q = request.args.get('q', '').strip()
    country = request.args.get('country', '').strip()
    warehouse = request.args.get('warehouse', '').strip()
    start_date = request.args.get('start_date', '')
    end_date = request.args.get('end_date', '')
    exam_id = request.args.get('exam_id', '').strip()  # ✅ 新增：用于标记绑定关系
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 20, type=int)
    
    if not q and not country and not warehouse and not start_date and not end_date:
        per_page = 8
    
    db = get_supabase()
    allowed_countries = get_allowed_countries()
    
    # 基础查询
    query = db.table("trainings").select("*").is_("deleted_at", "null")
    
    if q:
        query = query.ilike("name", f"%{q}%")
    
    if country:
        query = query.eq("country", country)
    elif allowed_countries is not None and allowed_countries:
        query = query.in_("country", allowed_countries)
    
    if start_date:
        query = query.gte("end_time", start_date)
    if end_date:
        query = query.lte("start_time", end_date)
    
    if warehouse:
        users_res = db.table("users").select("id").eq("wh_id", warehouse).execute()
        user_ids = [u['id'] for u in (users_res.data or [])]
        if user_ids:
            assign_res = db.table("training_assignments").select("training_id").in_("user_id", user_ids).execute()
            training_ids = list(set([a['training_id'] for a in (assign_res.data or [])]))
            if training_ids:
                query = query.in_("id", training_ids)
            else:
                return jsonify([])
        else:
            return jsonify([])
    
    query = query.order("created_at", desc=True)
    res = query.range((page-1)*per_page, page*per_page-1).execute()
    trainings = res.data or []
    
    # ✅ 获取考试绑定的培训ID（用于标记）
    bound_training_ids = set()
    if exam_id:
        try:
            exam_id_int = int(exam_id)
            bindings_res = db.table("training_exam_bindings").select("training_id").eq("exam_id", exam_id_int).execute()
            bound_training_ids = set([b['training_id'] for b in (bindings_res.data or [])])
        except ValueError:
            bound_training_ids = set()
    else:
        bound_training_ids = set()
    
    # 添加额外信息
    for t in trainings:
        t['created_date'] = t.get('created_at', '')[:10] if t.get('created_at') else ''
        t['quarter'] = _get_quarter_from_date(t.get('created_at'))
        t['is_bound'] = t['id'] in bound_training_ids  # ✅ 标记是否已绑定
        t['bound_training_ids'] = list(bound_training_ids)  # 可选：返回所有绑定的培训ID
    
    # ✅ 排序：绑定的培训排在前面
    trainings.sort(key=lambda x: (not x['is_bound'], x.get('name', '')))
    
    return jsonify(trainings)

def _get_quarter_from_date(date_str):
    """从日期字符串提取季度（格式：2026Q2）"""
    if not date_str:
        return ''
    try:
        from datetime import datetime
        if isinstance(date_str, str):
            if 'T' in date_str:
                date_str = date_str.split('T')[0]
            elif ' ' in date_str:
                date_str = date_str.split(' ')[0]
        date = datetime.fromisoformat(date_str) if isinstance(date_str, str) else date_str
        year = date.year
        month = date.month
        quarter = (month - 1) // 3 + 1
        return f"{year}Q{quarter}"
    except:
        return ''

@admin_training_bp.route('/api/admin/training/bindings/by_exam')
@login_required
@admin_required
def get_training_bindings_by_exam():
    """根据考试ID获取绑定的培训列表"""
    exam_id = request.args.get('exam_id', '')
    if not exam_id:
        return jsonify({"bindings": [], "training_ids": []})
    
    try:
        exam_id_int = int(exam_id)
        #db = get_supabase()
        db = get_supabase_admin()
        # 查询该考试绑定的培训
        bindings_res = db.table("training_exam_bindings").select("training_id").eq("exam_id", exam_id_int).execute()
        training_ids = list(set([b['training_id'] for b in (bindings_res.data or [])]))
        
        # 获取培训详细信息（可选）
        trainings = []
        if training_ids:
            trainings_res = db.table("trainings").select("id, name, country").in_("id", training_ids).execute()
            trainings = trainings_res.data or []
        
        return jsonify({
            "bindings": trainings,
            "training_ids": training_ids
        })
    except Exception as e:
        logger.error(f"获取考试绑定培训失败: {e}")
        return jsonify({"bindings": [], "training_ids": []}), 500

@admin_training_bp.route('/api/search/exams')
@login_required
@admin_required
def search_exams():
    """模糊搜索考试名称（带权限过滤）"""
    q = request.args.get('q', '').strip()
    if not q:
        return jsonify([])
    
    db = get_supabase()
    allowed_countries = get_allowed_countries()
    from routes.helpers import parse_exam_countries
    
    # 获取所有匹配的考试
    res = db.table("exams").select("title, countries, country").is_("deleted_at", "null").ilike("title", f"%{q}%").execute()
    all_exams = res.data or []
    
    # ✅ 权限过滤：只返回权限范围内的考试
    filtered_names = []
    for exam in all_exams:
        exam_countries = parse_exam_countries(exam)
        
        # 无权限限制
        if allowed_countries is None:
            filtered_names.append(exam['title'])
            continue
        
        # 有权限限制：检查是否有交集
        if allowed_countries:
            if any(c in allowed_countries for c in exam_countries):
                filtered_names.append(exam['title'])
    
    # 去重并限制数量
    unique_names = list(set(filtered_names))[:10]
    return jsonify(unique_names)

@admin_training_bp.route('/api/search/warehouses')
@login_required
@admin_required
def search_warehouses():
    """模糊搜索库房编码/名称（从 users 表，带权限过滤）"""
    q = request.args.get('q', '').strip()
    if not q:
        return jsonify([])
    
    db = get_supabase()
    allowed_countries = get_allowed_countries()
    
    # 基础查询 - 从 users 表查询
    query = db.table("users").select("wh_id, wh_name_en, country").is_("deleted_at", "null")
    
    # ✅ 权限过滤：只返回权限范围内的用户对应的库房
    if allowed_countries is not None and allowed_countries:
        query = query.in_("country", allowed_countries)
    
    # 模糊查询 wh_id 或 wh_name_en
    # 注意：Supabase 不支持 OR 条件的 ilike，需要分别查询后合并
    r1 = query.ilike("wh_id", f"%{q}%").limit(20).execute()
    r2 = query.ilike("wh_name_en", f"%{q}%").limit(20).execute()
    
    # 合并去重
    seen = set()
    suggestions = []
    
    for row in (r1.data or []) + (r2.data or []):
        wh_id = row.get('wh_id', '')
        wh_name = row.get('wh_name_en', '')
        
        if not wh_id:
            continue
        
        # 生成显示标签
        if wh_name:
            label = f"{wh_id} ({wh_name})"
        else:
            label = wh_id
        
        if label and label not in seen:
            seen.add(label)
            suggestions.append(label)
    
    return jsonify(suggestions[:10])

# routes/admin_training.py - 添加获取单个培训的接口

@admin_training_bp.route('/api/admin/trainings/<int:training_id>', methods=['GET'])
@login_required
@admin_required
def api_admin_training_detail(training_id):
    """获取单个培训详情"""
    from services.db import get_supabase_admin
    from utils.permissions import get_admin_allowed_countries
    
    db = get_supabase_admin()
    allowed_countries = get_admin_allowed_countries()
    
    # 获取培训信息
    res = db.table("trainings").select("*").eq("id", training_id).maybe_single().execute()
    if not res.data:
        return jsonify({"success": False, "message": "培训不存在"}), 404
    
    training = res.data
    
    # 权限检查
    training_country = training.get('country')
    if allowed_countries is not None and training_country and training_country not in allowed_countries:
        return jsonify({"success": False, "message": "无权访问此培训"}), 403
    
    return jsonify({"success": True, "data": training})

# ==================== 正确注册到 admin_training_bp 蓝图中 ====================
@admin_training_bp.route('/admin/training/completion_report')
@login_required
@admin_required
def completion_report_page():
    """完成度报表页面"""
    return render_template('admin/training_completion_report.html')

'''
@admin_training_bp.route('/api/admin/training/completion_report')
@login_required
@admin_required
def get_completion_report():
    """获取培训完成度报表数据"""
    try:
        db = get_supabase_admin()
        
        # 获取当前管理员的权限范围
        allowed_countries = get_admin_allowed_countries()
        is_dev = is_developer()
        current_role = session.get('role')
        
        # 获取筛选参数
        name = request.args.get('name', '').strip()
        country_filter = request.args.get('country', '').strip()
        training_name = request.args.get('training_name', '').strip()
        training_id = request.args.get('training_id', '').strip()
        exam_name = request.args.get('exam_name', '').strip()
        status = request.args.get('status', '').strip()
        start_date = request.args.get('start_date', '')
        end_date = request.args.get('end_date', '')
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 20, type=int)
        
        # ========== 1. 获取培训 ==========
        trainings_query = db.table("trainings").select("*").is_("deleted_at", "null")
        
        if training_id:
            trainings_query = trainings_query.eq("id", int(training_id))
        elif training_name:
            trainings_query = trainings_query.ilike("name", f"%{training_name}%")
        
        if start_date:
            trainings_query = trainings_query.gte("start_time", start_date)
        if end_date:
            trainings_query = trainings_query.lte("end_time", end_date)
        
        all_trainings = trainings_query.execute().data or []
        
        if not is_dev and allowed_countries:
            filtered_trainings = []
            for t in all_trainings:
                training_country = t.get('country')
                if training_country and training_country in allowed_countries:
                    filtered_trainings.append(t)
            all_trainings = filtered_trainings
        
        if not all_trainings:
            return jsonify({"data": [], "total": 0, "page": 1, "per_page": per_page, "summary": {}})
        
        training_ids = [t['id'] for t in all_trainings]
        trainings_dict = {t['id']: t for t in all_trainings}
        
        # ========== 2. 获取绑定关系 ==========
        bindings_result = db.table("training_exam_bindings").select("*")\
            .in_("training_id", training_ids)\
            .is_("deleted_at", "null")\
            .execute()
        bindings = bindings_result.data if bindings_result else []
        
        if not bindings:
            return jsonify({"data": [], "total": 0, "page": 1, "per_page": per_page, "summary": {}})
        
        # ========== 3. 获取考试信息 ==========
        exam_ids = list(set([b.get('exam_id') for b in bindings]))
        exams_result = db.table("exams").select("*").in_("id", exam_ids).is_("deleted_at", "null").execute()
        exams_dict = {e['id']: e for e in (exams_result.data or [])}
        
        if exam_name:
            filtered_bindings = []
            for b in bindings:
                exam = exams_dict.get(b['exam_id'])
                if exam and exam_name.lower() in exam.get('title', '').lower():
                    filtered_bindings.append(b)
            bindings = filtered_bindings
        
        if not bindings:
            return jsonify({"data": [], "total": 0, "page": 1, "per_page": per_page, "summary": {}})
        
        # ========== 4. 获取用户 ==========
        all_countries = set()
        for t in all_trainings:
            country = t.get('country')
            if country:
                all_countries.add(country)
        
        if all_countries:
            users_query = db.table("users").select("id, name_en, name_cn, email, country, wh_id, role")\
                .in_("country", list(all_countries))\
                .eq("user_status", "registered")\
                .is_("deleted_at", "null")\
                .eq("is_resign", False)
        else:
            users_query = db.table("users").select("*")\
                .eq("user_status", "registered")\
                .is_("deleted_at", "null")\
                .eq("is_resign", False)
        
        if country_filter:
            users_query = users_query.ilike("country", f"%{country_filter}%")
        
        users_result = users_query.execute()
        all_users = users_result.data if users_result else []

        if name:
            name_lower = name.lower()
            filtered_users = []
            for u in all_users:
                name_en = (u.get('name_en') or '').lower()
                name_cn = (u.get('name_cn') or '').lower()
                email = (u.get('email') or '').lower()
                if name_lower in name_en or name_lower in name_cn or name_lower in email:
                    filtered_users.append(u)
            all_users = filtered_users

        users_by_country = {}
        for u in all_users:
            country = u.get('country')
            if country not in users_by_country:
                users_by_country[country] = []
            users_by_country[country].append(u)
        
        # ========== 5. 获取分配关系 ==========
        # 培训分配
        training_assign_res = db.table("training_assignments").select("training_id, user_id")\
            .in_("training_id", training_ids)\
            .execute()
        training_assign_map = {}
        for a in (training_assign_res.data or []):
            key = f"{a['training_id']}_{a['user_id']}"
            training_assign_map[key] = True
        
        # 考试分配
        exam_assign_res = db.table("exam_assignments").select("exam_id, user_id")\
            .in_("exam_id", exam_ids)\
            .execute()
        exam_assign_map = {}
        for a in (exam_assign_res.data or []):
            key = f"{a['exam_id']}_{a['user_id']}"
            exam_assign_map[key] = True
        
        # ========== 6. 获取完成度数据 ==========
        summary_result = db.table("training_completion_summary").select("*")\
            .in_("training_id", training_ids)\
            .in_("exam_id", exam_ids)\
            .execute()
        summary_list = summary_result.data if summary_result else []
        
        summary_index = {}
        for s in summary_list:
            key = (s.get('training_id'), s.get('exam_id'), s.get('user_id'))
            summary_index[key] = s
        
        # ========== 7. 组装数据（修复状态判断）==========
        all_data = []
        
        for binding in bindings:
            training_id_val = binding.get('training_id')
            exam_id = binding.get('exam_id')
            pass_score = binding.get('pass_score', 85)
            
            training = trainings_dict.get(training_id_val)
            exam = exams_dict.get(exam_id)
            
            if not training or not exam:
                continue
            
            training_country = training.get('country', '')
            if not training_country:
                continue
            
            users = users_by_country.get(training_country, [])
            
            # 角色过滤
            if current_role == 'admin':
                users = [u for u in users if u.get('role') not in ['super_admin', 'developer']]
            elif current_role == 'super_admin' and not is_dev:
                users = [u for u in users if u.get('role') != 'developer']
            
            for user in users:
                user_id = user.get('id')
                if not user_id:
                    continue
                
                key = (training_id_val, exam_id, user_id)
                summary_data = summary_index.get(key, {})
                
                # ✅ 获取分配关系
                training_assign_key = f"{training_id_val}_{user_id}"
                exam_assign_key = f"{exam_id}_{user_id}"
                
                has_training_assign = training_assign_key in training_assign_map
                has_exam_assign = exam_assign_key in exam_assign_map
                
                # ✅ 获取签到和考试状态
                is_signed = summary_data.get('is_signed', False)
                is_completed = summary_data.get('is_completed', False)
                is_passed = summary_data.get('is_passed', False)
                score = summary_data.get('score')
                
                # ✅ 修复：培训状态判断
                if is_signed:
                    training_status_display = 'signed'  # 已签到
                elif has_training_assign:
                    training_status_display = 'pending'  # 待签到
                else:
                    training_status_display = 'not_assigned'  # 未推送
                
                # ✅ 修复：考试状态判断
                if is_completed:
                    exam_status_display = 'completed'  # 已完成
                elif has_exam_assign:
                    exam_status_display = 'pending'  # 待考试（已分配）
                elif is_signed:
                    # 已签到但考试未分配（理论上签到后会自动分配，这里作为兜底）
                    exam_status_display = 'pending'
                else:
                    exam_status_display = 'not_assigned'  # 未推送
                
                # 状态筛选（前端筛选逻辑）
                if status:
                    if status == 'completed' and not is_passed:
                        continue
                    if status == 'incomplete' and (is_completed or is_passed):
                        continue
                    if status == 'failed' and (not is_completed or is_passed):
                        continue
                    if status == 'not_signed' and is_signed:
                        continue
                    if status == 'not_exam' and (is_completed or not is_signed):
                        continue
                
                all_data.append({
                    "training_id": training_id_val,
                    "training_name": training.get('name', ''),
                    "training_country": training_country,
                    "training_start": training.get('start_time'),
                    "training_end": training.get('end_time'),
                    "exam_id": exam_id,
                    "exam_name": exam.get('title', ''),
                    "pass_score": pass_score,
                    "user_id": user_id,
                    "user_name": user.get('name_cn') or user.get('name_en', ''),
                    "user_email": user.get('email', ''),
                    "user_country": user.get('country', ''),
                    "wh_id": user.get('wh_id', ''),
                    "user_role": user.get('role', 'user'),
                    "is_signed": is_signed,
                    "is_completed": is_completed,
                    "is_passed": is_passed,
                    "score": score,
                    "training_status": training_status_display,  # ✅ 新增
                    "exam_status": exam_status_display,          # ✅ 新增
                    "has_training_assign": has_training_assign,  # ✅ 新增
                    "has_exam_assign": has_exam_assign,          # ✅ 新增
                    "signed_at": summary_data.get('signed_at'),
                    "completed_at": summary_data.get('completed_at')
                })
        
        # 分页
        total = len(all_data)
        start_idx = (page - 1) * per_page
        end_idx = start_idx + per_page
        paginated = all_data[start_idx:end_idx]
        
        # 统计汇总
        unique_users = set()
        signed_count = 0
        completed_count = 0
        passed_count = 0
        
        for d in all_data:
            if d.get('user_id'):
                unique_users.add(d.get('user_id'))
            if d.get('is_signed'):
                signed_count += 1
            if d.get('is_completed'):
                completed_count += 1
            if d.get('is_passed'):
                passed_count += 1
        
        summary_stats = {
            "total_users": len(unique_users),
            "total_signed": signed_count,
            "total_completed": completed_count,
            "total_passed": passed_count,
            "pass_rate": round(passed_count / len(all_data) * 100, 1) if all_data else 0
        }
        
        return jsonify({
            "data": paginated,
            "total": total,
            "page": page,
            "per_page": per_page,
            "summary": summary_stats
        })
        
    except Exception as e:
        logger.error(f"报表API错误: {e}")
        logger.error(traceback.format_exc())
        return jsonify({"error": str(e), "data": [], "total": 0}), 500
'''

@admin_training_bp.route('/api/admin/training/completion_report')
@login_required
@admin_required
def get_completion_report():
    """获取培训完成度报表数据"""
    try:
        db = get_supabase_admin()
        
        # 获取当前管理员的权限范围
        allowed_countries = get_admin_allowed_countries()
        is_dev = is_developer()
        current_role = session.get('role')
        
        # 获取筛选参数
        name = request.args.get('name', '').strip()
        country_filter = request.args.get('country', '').strip()
        training_name = request.args.get('training_name', '').strip()
        training_id = request.args.get('training_id', '').strip()
        exam_name = request.args.get('exam_name', '').strip()
        status = request.args.get('status', '').strip()
        start_date = request.args.get('start_date', '')
        end_date = request.args.get('end_date', '')
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 20, type=int)
        
        # ========== 1. 获取培训 ==========
        trainings_query = db.table("trainings").select("*").is_("deleted_at", "null")
        
        if training_id:
            trainings_query = trainings_query.eq("id", int(training_id))
        elif training_name:
            trainings_query = trainings_query.ilike("name", f"%{training_name}%")
        
        if start_date:
            trainings_query = trainings_query.gte("start_time", start_date)
        if end_date:
            trainings_query = trainings_query.lte("end_time", end_date)
        
        all_trainings = trainings_query.execute().data or []
        
        if not is_dev and allowed_countries:
            filtered_trainings = []
            for t in all_trainings:
                training_country = t.get('country')
                if training_country and training_country in allowed_countries:
                    filtered_trainings.append(t)
            all_trainings = filtered_trainings
        
        if not all_trainings:
            return jsonify({"data": [], "total": 0, "page": 1, "per_page": per_page, "summary": {}})
        
        training_ids = [t['id'] for t in all_trainings]
        trainings_dict = {t['id']: t for t in all_trainings}
        
        # ========== 2. 获取绑定关系 ==========
        bindings_result = db.table("training_exam_bindings").select("*")\
            .in_("training_id", training_ids)\
            .is_("deleted_at", "null")\
            .execute()
        bindings = bindings_result.data if bindings_result else []
        
        if not bindings:
            return jsonify({"data": [], "total": 0, "page": 1, "per_page": per_page, "summary": {}})
        
        # ========== 3. 获取考试信息 ==========
        exam_ids = list(set([b.get('exam_id') for b in bindings]))
        exams_result = db.table("exams").select("*").in_("id", exam_ids).is_("deleted_at", "null").execute()
        exams_dict = {e['id']: e for e in (exams_result.data or [])}
        
        if exam_name:
            filtered_bindings = []
            for b in bindings:
                exam = exams_dict.get(b['exam_id'])
                if exam and exam_name.lower() in exam.get('title', '').lower():
                    filtered_bindings.append(b)
            bindings = filtered_bindings
        
        if not bindings:
            return jsonify({"data": [], "total": 0, "page": 1, "per_page": per_page, "summary": {}})
        
        # ========== 4. 获取用户 ==========
        all_countries = set()
        for t in all_trainings:
            country = t.get('country')
            if country:
                all_countries.add(country)
        
        if all_countries:
            users_query = db.table("users").select("id, name_en, name_cn, email, country, wh_id, role")\
                .in_("country", list(all_countries))\
                .eq("user_status", "registered")\
                .is_("deleted_at", "null")\
                .eq("is_resign", False)
        else:
            users_query = db.table("users").select("*")\
                .eq("user_status", "registered")\
                .is_("deleted_at", "null")\
                .eq("is_resign", False)
        
        if country_filter:
            users_query = users_query.ilike("country", f"%{country_filter}%")
        
        users_result = users_query.execute()
        all_users = users_result.data if users_result else []

        if name:
            name_lower = name.lower()
            filtered_users = []
            for u in all_users:
                name_en = (u.get('name_en') or '').lower()
                name_cn = (u.get('name_cn') or '').lower()
                email = (u.get('email') or '').lower()
                if name_lower in name_en or name_lower in name_cn or name_lower in email:
                    filtered_users.append(u)
            all_users = filtered_users

        users_by_country = {}
        for u in all_users:
            country = u.get('country')
            if country not in users_by_country:
                users_by_country[country] = []
            users_by_country[country].append(u)
        
        # ========== 5. 获取分配关系 ==========
        # 培训分配
        training_assign_res = db.table("training_assignments").select("training_id, user_id")\
            .in_("training_id", training_ids)\
            .execute()
        training_assign_map = {}
        for a in (training_assign_res.data or []):
            key = f"{a['training_id']}_{a['user_id']}"
            training_assign_map[key] = True
        
        # 考试分配
        exam_assign_res = db.table("exam_assignments").select("exam_id, user_id")\
            .in_("exam_id", exam_ids)\
            .execute()
        exam_assign_map = {}
        for a in (exam_assign_res.data or []):
            key = f"{a['exam_id']}_{a['user_id']}"
            exam_assign_map[key] = True
        
        # ========== 6. 获取完成度数据 ==========
        summary_result = db.table("training_completion_summary").select("*")\
            .in_("training_id", training_ids)\
            .in_("exam_id", exam_ids)\
            .execute()
        summary_list = summary_result.data if summary_result else []
        
        summary_index = {}
        for s in summary_list:
            key = (s.get('training_id'), s.get('exam_id'), s.get('user_id'))
            summary_index[key] = s
        
        # ========== 7. 组装数据（修复状态判断）==========
        all_data = []
        
        for binding in bindings:
            training_id_val = binding.get('training_id')
            exam_id = binding.get('exam_id')
            pass_score = binding.get('pass_score', 85)
            
            training = trainings_dict.get(training_id_val)
            exam = exams_dict.get(exam_id)
            
            if not training or not exam:
                continue
            
            training_country = training.get('country', '')
            if not training_country:
                continue
            
            users = users_by_country.get(training_country, [])
            
            # 角色过滤
            if current_role == 'admin':
                users = [u for u in users if u.get('role') not in ['super_admin', 'developer']]
            elif current_role == 'super_admin' and not is_dev:
                users = [u for u in users if u.get('role') != 'developer']
            
            for user in users:
                user_id = user.get('id')
                if not user_id:
                    continue
                
                key = (training_id_val, exam_id, user_id)
                summary_data = summary_index.get(key, {})
                
                # ✅ 获取分配关系
                training_assign_key = f"{training_id_val}_{user_id}"
                exam_assign_key = f"{exam_id}_{user_id}"
                
                has_training_assign = training_assign_key in training_assign_map
                has_exam_assign = exam_assign_key in exam_assign_map
                
                # ✅ 获取签到和考试状态
                is_signed = summary_data.get('is_signed', False)
                is_completed = summary_data.get('is_completed', False)
                is_passed = summary_data.get('is_passed', False)
                score = summary_data.get('score')
                
                # ✅ 修复：培训状态判断
                if is_signed:
                    training_status_display = 'signed'  # 已签到
                elif has_training_assign:
                    training_status_display = 'pending'  # 待签到
                else:
                    training_status_display = 'not_assigned'  # 未推送
                
                # ✅ 修复：考试状态判断
                if is_completed:
                    exam_status_display = 'completed'  # 已完成
                elif has_exam_assign:
                    exam_status_display = 'pending'  # 待考试（已分配）
                elif is_signed:
                    # 已签到但考试未分配（理论上签到后会自动分配，这里作为兜底）
                    exam_status_display = 'pending'
                else:
                    exam_status_display = 'not_assigned'  # 未推送
                
                # 状态筛选（前端筛选逻辑）
                if status:
                    if status == 'completed' and not is_passed:
                        continue
                    if status == 'incomplete' and (is_completed or is_passed):
                        continue
                    if status == 'failed' and (not is_completed or is_passed):
                        continue
                    if status == 'not_signed' and is_signed:
                        continue
                    if status == 'not_exam' and (is_completed or not is_signed):
                        continue
                
                all_data.append({
                    "training_id": training_id_val,
                    "training_name": training.get('name', ''),
                    "training_country": training_country,
                    "training_start": training.get('start_time'),
                    "training_end": training.get('end_time'),
                    "exam_id": exam_id,
                    "exam_name": exam.get('title', ''),
                    "pass_score": pass_score,
                    "user_id": user_id,
                    "user_name": user.get('name_cn') or user.get('name_en', ''),
                    "user_email": user.get('email', ''),
                    "user_country": user.get('country', ''),
                    "wh_id": user.get('wh_id', ''),
                    "user_role": user.get('role', 'user'),
                    "is_signed": is_signed,
                    "is_completed": is_completed,
                    "is_passed": is_passed,
                    "score": score,
                    "training_status": training_status_display,  # ✅ 新增
                    "exam_status": exam_status_display,          # ✅ 新增
                    "has_training_assign": has_training_assign,  # ✅ 新增
                    "has_exam_assign": has_exam_assign,          # ✅ 新增
                    "signed_at": summary_data.get('signed_at'),
                    "completed_at": summary_data.get('completed_at')
                })
        
        # 分页
        total = len(all_data)
        start_idx = (page - 1) * per_page
        end_idx = start_idx + per_page
        paginated = all_data[start_idx:end_idx]

        # ✅ 修复：统计汇总（基于唯一学员，避免重复计数）
        unique_users = set()
        unique_users_signed = set()      # 已签到的唯一学员
        unique_users_completed = set()   # 已完成考试的唯一学员
        unique_users_passed = set()      # 已及格的唯一学员

        # 用于计算签到率的辅助数据
        training_user_map = {}  # {training_id: set(user_ids)}

        for d in all_data:
            user_id = d.get('user_id')
            training_id = d.get('training_id')
            
            if not user_id:
                continue
            
            unique_users.add(user_id)
            
            if d.get('is_signed'):
                unique_users_signed.add(user_id)
            
            if d.get('is_completed'):
                unique_users_completed.add(user_id)
            
            if d.get('is_passed'):
                unique_users_passed.add(user_id)

        # 计算及格率（基于唯一学员）
        total_users_count = len(unique_users)
        passed_users_count = len(unique_users_passed)

        if total_users_count > 0:
            pass_rate = round(passed_users_count / total_users_count * 100, 1)
        else:
            pass_rate = 0

        # 可选：计算签到率
        signed_users_count = len(unique_users_signed)
        if total_users_count > 0:
            sign_rate = round(signed_users_count / total_users_count * 100, 1)
        else:
            sign_rate = 0

        # 可选：计算考试完成率
        completed_users_count = len(unique_users_completed)
        if total_users_count > 0:
            completion_rate = round(completed_users_count / total_users_count * 100, 1)
        else:
            completion_rate = 0

        logger.info(f"统计汇总: 总学员={total_users_count}, 已签到={signed_users_count} ({sign_rate}%), "
                    f"已完成考试={completed_users_count} ({completion_rate}%), "
                    f"已及格={passed_users_count} ({pass_rate}%)")

        summary_stats = {
            "total_users": total_users_count,
            "total_signed": signed_users_count,
            "total_completed": completed_users_count,
            "total_passed": passed_users_count,
            "pass_rate": pass_rate,
            "sign_rate": sign_rate,           # 可选：返回签到率
            "completion_rate": completion_rate  # 可选：返回完成率
        }
        
        return jsonify({
            "data": paginated,
            "total": total,
            "page": page,
            "per_page": per_page,
            "summary": summary_stats
        })
        
    except Exception as e:
        logger.error(f"报表API错误: {e}")
        logger.error(traceback.format_exc())
        return jsonify({"error": str(e), "data": [], "total": 0}), 500


@admin_training_bp.route('/api/admin/training/completion_report/export')
@login_required
@admin_required
def export_completion_report():
    """导出完成度报表 Excel"""
    import traceback
    from services.db import get_supabase_admin
    from utils.permissions import get_admin_allowed_countries, is_developer
    from io import BytesIO
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment
    
    try:
        db = get_supabase_admin()
        
        # 获取当前管理员的权限范围
        allowed_countries = get_admin_allowed_countries()
        is_dev = is_developer()
        current_role = session.get('role')
        
        # 获取筛选参数
        name = request.args.get('name', '').strip()
        country_filter = request.args.get('country', '').strip()
        training_name = request.args.get('training_name', '').strip()
        exam_name = request.args.get('exam_name', '').strip()
        status = request.args.get('status', '').strip()
        start_date = request.args.get('start_date', '')
        end_date = request.args.get('end_date', '')
        
        # ========== 1. 批量获取所有绑定关系 ==========
        bindings_result = db.table("training_exam_bindings").select("*")\
            .is_("deleted_at", "null")\
            .execute()
        bindings = bindings_result.data if bindings_result else []
        
        if not bindings:
            # 没有绑定关系，返回空Excel
            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = "培训完成度报表"
            ws.cell(row=1, column=1, value="暂无数据")
            buffer = BytesIO()
            wb.save(buffer)
            buffer.seek(0)
            return send_file(buffer, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                           as_attachment=True, download_name=f'培训完成度报表_空.xlsx')
        
        # ========== 2. 批量获取所有相关培训和考试信息 ==========
        training_ids = list(set([b.get('training_id') for b in bindings]))
        exam_ids = list(set([b.get('exam_id') for b in bindings]))
        
        trainings_result = db.table("trainings").select("*").in_("id", training_ids).is_("deleted_at", "null").execute()
        trainings_dict = {t['id']: t for t in (trainings_result.data or [])}
        
        exams_result = db.table("exams").select("*").in_("id", exam_ids).is_("deleted_at", "null").execute()
        exams_dict = {e['id']: e for e in (exams_result.data or [])}
        
        # ========== 3. 批量获取所有相关国家的用户 ==========
        all_countries = set()
        for t in trainings_dict.values():
            country = t.get('country')
            if country:
                all_countries.add(country)
        
        users_query = db.table("users").select("id, name_en, name_cn, email, country, wh_id, role")\
            .in_("country", list(all_countries)) if all_countries else db.table("users").select("*")
        users_query = users_query.eq("user_status", "registered").is_("deleted_at", "null")
        
        if name:
            users_query = users_query.or_(f"name_en.ilike.%{name}%,name_cn.ilike.%{name}%,email.ilike.%{name}%")
        if country_filter:
            users_query = users_query.ilike("country", f"%{country_filter}%")
        
        users_result = users_query.execute()
        all_users = users_result.data if users_result else []
        
        users_by_country = {}
        for u in all_users:
            country = u.get('country')
            if country not in users_by_country:
                users_by_country[country] = []
            users_by_country[country].append(u)
        
        # ========== 4. 批量获取所有完成度数据 ==========
        summary_result = db.table("training_completion_summary").select("*")\
            .in_("training_id", training_ids)\
            .in_("exam_id", exam_ids)\
            .execute()
        summary_list = summary_result.data if summary_result else []
        
        summary_index = {}
        for s in summary_list:
            key = (s.get('training_id'), s.get('exam_id'), s.get('user_id'))
            summary_index[key] = s
        
        # ========== 5. 组装数据 ==========
        all_data = []
        
        for binding in bindings:
            training_id = binding.get('training_id')
            exam_id = binding.get('exam_id')
            pass_score = binding.get('pass_score', 85)
            
            training = trainings_dict.get(training_id)
            exam = exams_dict.get(exam_id)
            
            if not training or not exam:
                continue
            
            training_country = training.get('country', '')
            
            # 权限过滤
            if not is_dev:
                if allowed_countries and training_country not in allowed_countries:
                    continue
                elif current_role == 'admin' and not allowed_countries:
                    user_country = session.get('user_country')
                    if training_country != user_country:
                        continue
            
            # 筛选条件
            if training_name and training_name.lower() not in training.get('name', '').lower():
                continue
            if exam_name and exam_name.lower() not in exam.get('title', '').lower():
                continue
            
            training_start = training.get('start_time', '')[:10] if training.get('start_time') else ''
            training_end = training.get('end_time', '')[:10] if training.get('end_time') else ''
            if start_date and training_start < start_date:
                continue
            if end_date and training_end > end_date:
                continue
            
            if not training_country:
                continue
            
            users = users_by_country.get(training_country, [])
            
            # 角色过滤
            if current_role == 'admin':
                users = [u for u in users if u.get('role') not in ['super_admin', 'developer']]
            elif current_role == 'super_admin' and not is_dev:
                users = [u for u in users if u.get('role') != 'developer']
            
            for user in users:
                user_id = user.get('id')
                if not user_id:
                    continue
                
                key = (training_id, exam_id, user_id)
                summary_data = summary_index.get(key, {})
                
                is_signed = summary_data.get('is_signed', False)
                is_completed = summary_data.get('is_completed', False)
                is_passed = summary_data.get('is_passed', False)
                score = summary_data.get('score')
                
                if status:
                    if status == 'completed' and not is_passed:
                        continue
                    if status == 'incomplete' and (is_completed or is_passed):
                        continue
                    if status == 'failed' and (not is_completed or is_passed):
                        continue
                    if status == 'not_signed' and is_signed:
                        continue
                    if status == 'not_exam' and (is_completed or not is_signed):
                        continue
                
                all_data.append({
                    "training_name": training.get('name', ''),
                    "training_country": training_country,
                    "training_start": training.get('start_time'),
                    "training_end": training.get('end_time'),
                    "exam_name": exam.get('title', ''),
                    "pass_score": pass_score,
                    "user_name": user.get('name_cn') or user.get('name_en', ''),
                    "user_email": user.get('email', ''),
                    "user_country": user.get('country', ''),
                    "wh_id": user.get('wh_id', ''),
                    "is_signed": is_signed,
                    "is_completed": is_completed,
                    "is_passed": is_passed,
                    "score": score if score is not None else '-',
                    "signed_at": summary_data.get('signed_at'),
                    "completed_at": summary_data.get('completed_at')
                })
        
        # ========== 6. 创建 Excel ==========
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "培训完成度报表"
        
        headers = ['序号', '培训名称', '培训国家', '培训开始日期', '培训结束日期', '考试名称', '及格分数',
                   '学员姓名', '学员邮箱', '学员国家', '库房编码', '签到状态', '考试状态', '得分', '是否及格', '签到时间', '完成时间']
        
        # 表头样式
        for col, header in enumerate(headers, 1):
            cell = ws.cell(row=1, column=col, value=header)
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
            cell.alignment = Alignment(horizontal="center", vertical="center")
        
        # 写入数据
        for row_idx, item in enumerate(all_data, 2):
            ws.cell(row=row_idx, column=1, value=row_idx - 1)
            ws.cell(row=row_idx, column=2, value=item.get('training_name', ''))
            ws.cell(row=row_idx, column=3, value=item.get('training_country', ''))
            ws.cell(row=row_idx, column=4, value=item.get('training_start', '')[:10] if item.get('training_start') else '')
            ws.cell(row=row_idx, column=5, value=item.get('training_end', '')[:10] if item.get('training_end') else '')
            ws.cell(row=row_idx, column=6, value=item.get('exam_name', ''))
            ws.cell(row=row_idx, column=7, value=item.get('pass_score', 85))
            ws.cell(row=row_idx, column=8, value=item.get('user_name', ''))
            ws.cell(row=row_idx, column=9, value=item.get('user_email', ''))
            ws.cell(row=row_idx, column=10, value=item.get('user_country', ''))
            ws.cell(row=row_idx, column=11, value=item.get('wh_id', ''))
            ws.cell(row=row_idx, column=12, value='已签到' if item.get('is_signed') else '未签到')
            ws.cell(row=row_idx, column=13, value='已完成' if item.get('is_completed') else '未完成')
            ws.cell(row=row_idx, column=14, value=item.get('score', '-'))
            ws.cell(row=row_idx, column=15, value='及格' if item.get('is_passed') else ('不及格' if item.get('is_completed') else '-'))
            ws.cell(row=row_idx, column=16, value=item.get('signed_at', '')[:19] if item.get('signed_at') else '')
            ws.cell(row=row_idx, column=17, value=item.get('completed_at', '')[:19] if item.get('completed_at') else '')
        
        # 调整列宽
        for col in range(1, len(headers) + 1):
            ws.column_dimensions[openpyxl.utils.get_column_letter(col)].width = 18
        
        buffer = BytesIO()
        wb.save(buffer)
        buffer.seek(0)
        
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        return send_file(
            buffer,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name=f'培训完成度报表_{timestamp}.xlsx'
        )
        
    except Exception as e:
        logger.error(f"导出Excel失败: {e}")
        logger.error(traceback.format_exc())
        return jsonify({"error": str(e)}), 500

# ==================== 培训-考试绑定管理 API ====================

@admin_training_bp.route('/api/admin/training/<int:training_id>/bindings')
@login_required
@admin_required
def get_training_bindings(training_id):
    """获取培训绑定的考试列表"""
    from utils.permissions import get_admin_allowed_countries, is_developer
    
    db = get_supabase_admin()
    
    # 获取当前用户角色和权限
    current_role = session.get('role')
    is_dev = is_developer()
    allowed_countries = get_admin_allowed_countries()
    
    # 获取培训信息（用于国家过滤）
    training_res = db.table("trainings").select("country").eq("id", training_id).maybe_single().execute()
    training_country = training_res.data.get('country') if training_res.data else None
    
    # 获取绑定关系
    bindings = db.table("training_exam_bindings").select("*")\
        .eq("training_id", training_id)\
        .is_("deleted_at", "null")\
        .order("sort_order")\
        .execute()
    
    # 获取考试信息
    result = []
    for b in (bindings.data or []):
        exam = db.table("exams").select("id, title, countries, duration")\
            .eq("id", b['exam_id'])\
            .maybe_single()\
            .execute()
        
        # 解析 countries
        countries_list = []
        if exam.data:
            countries_data = exam.data.get('countries')
            if isinstance(countries_data, str):
                try:
                    countries_list = json.loads(countries_data)
                except:
                    countries_list = []
            elif isinstance(countries_data, list):
                countries_list = countries_data
        
        result.append({
            "id": b['id'],
            "training_id": b['training_id'],
            "exam_id": b['exam_id'],
            "exam_title": exam.data['title'] if exam.data else '未知考试',
            "exam_countries": countries_list,
            "pass_score": b.get('pass_score', 85),
            "is_auto_assign": b.get('is_auto_assign', True),
            "is_required": b.get('is_required', True),
            "sort_order": b.get('sort_order', 0),
            "created_at": b.get('created_at')
        })
    
    # 获取可选考试列表（未绑定的）- 根据角色过滤
    all_exams = db.table("exams").select("id, title, countries")\
        .is_("deleted_at", "null")\
        .execute()
    
    bound_ids = [b['exam_id'] for b in (bindings.data or [])]
    available_exams = []
    
    for e in (all_exams.data or []):
        if e['id'] in bound_ids:
            continue
        
        # 解析考试的国家列表
        exam_countries = []
        countries_data = e.get('countries')
        if isinstance(countries_data, str):
            try:
                exam_countries = json.loads(countries_data)
            except:
                exam_countries = []
        elif isinstance(countries_data, list):
            exam_countries = countries_data
        
        # ========== 角色权限过滤 ==========
        can_see = False
        
        if is_dev:
            # 开发者可以看到所有考试
            can_see = True
        elif current_role == 'super_admin':
            # 超管：根据权限范围过滤
            if allowed_countries is not None and allowed_countries:
                # 检查考试国家是否在权限范围内
                if any(c in allowed_countries for c in exam_countries):
                    can_see = True
            else:
                # 无权限限制，可以看到所有
                can_see = True
        elif current_role == 'admin':
            # 管理员：根据权限范围或自己国家过滤
            if allowed_countries is not None and allowed_countries:
                if any(c in allowed_countries for c in exam_countries):
                    can_see = True
            else:
                # 无权限范围，使用用户注册国家
                user_country = session.get('user_country')
                if user_country and user_country in exam_countries:
                    can_see = True
        
        # 额外：考试国家必须与培训国家匹配（或包含培训国家）
        if can_see and training_country:
            if training_country not in exam_countries:
                can_see = False
        
        if can_see:
            available_exams.append({
                "id": e['id'],
                "title": e['title'],
                "countries": exam_countries
            })
    
    return jsonify({
        "bindings": result,
        "available_exams": available_exams
    })

@admin_training_bp.route('/api/admin/training/bind_exam', methods=['POST'])
@login_required
@admin_required
def bind_exam_to_training():
    """绑定考试到培训"""
    data = request.json
    training_id = data.get('training_id')
    exam_id = data.get('exam_id')
    pass_score = data.get('pass_score', 85)
    is_auto_assign = data.get('is_auto_assign', True)
    is_required = data.get('is_required', True)
    sort_order = data.get('sort_order', 0)

    logger.info(f"========== 绑定考试到培训 ==========")
    logger.info(f"training_id: {training_id}, exam_id: {exam_id}")
    logger.info(f"is_auto_assign: {is_auto_assign}, pass_score: {pass_score}")
    
    if not training_id or not exam_id:
        return jsonify({"success": False, "message": "参数不完整"}), 400
    
    db = get_supabase_admin()
    
    # 检查是否存在软删除的记录
    soft_deleted = db.table("training_exam_bindings").select("*")\
        .eq("training_id", training_id)\
        .eq("exam_id", exam_id)\
        .not_.is_("deleted_at", "null")\
        .execute()
    
    if soft_deleted and soft_deleted.data:
        # 恢复软删除的记录
        result = db.table("training_exam_bindings").update({
            "deleted_at": None,
            "deleted_by": None,
            "pass_score": pass_score,
            "is_auto_assign": is_auto_assign,
            "is_required": is_required,
            "sort_order": sort_order,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "updated_by": session.get('user_id')
        }).eq("id", soft_deleted.data[0]['id']).execute()

        logger.info(f"绑定创建成功: binding_id={result.data[0]['id'] if result.data else 'None'}")
        logger.info(f"绑定记录: training_id={training_id}, exam_id={exam_id}, is_auto_assign={is_auto_assign}")
        
        if result.data:
            return jsonify({"success": True, "binding_id": result.data[0]['id'], "restored": True})
        else:
            return jsonify({"success": False, "message": "恢复绑定失败"}), 500
    
    # 检查是否已有活跃绑定
    existing = db.table("training_exam_bindings").select("id")\
        .eq("training_id", training_id)\
        .eq("exam_id", exam_id)\
        .is_("deleted_at", "null")\
        .execute()
    
    if existing.data:
        return jsonify({"success": False, "message": "该考试已绑定到此培训"}), 400
    
    # 创建新绑定
    result = db.table("training_exam_bindings").insert({
        "training_id": training_id,
        "exam_id": exam_id,
        "pass_score": pass_score,
        "is_auto_assign": is_auto_assign,
        "is_required": is_required,
        "sort_order": sort_order,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "created_by": session.get('user_id')
    }).execute()
    
    if result.data:
        return jsonify({"success": True, "binding_id": result.data[0]['id']})
    else:
        return jsonify({"success": False, "message": "绑定失败"}), 500

@admin_training_bp.route('/api/admin/training/binding/<int:binding_id>', methods=['PUT'])
@login_required
@admin_required
def update_training_binding(binding_id):
    """更新绑定配置"""
    data = request.json
    db = get_supabase_admin()
    
    update_data = {}
    if 'pass_score' in data:
        update_data['pass_score'] = data['pass_score']
    if 'is_auto_assign' in data:
        update_data['is_auto_assign'] = data['is_auto_assign']
    if 'is_required' in data:
        update_data['is_required'] = data['is_required']
    if 'sort_order' in data:
        update_data['sort_order'] = data['sort_order']
    
    if not update_data:
        return jsonify({"success": False, "message": "没有要更新的字段"}), 400
    
    update_data['updated_at'] = datetime.now(timezone.utc).isoformat()
    update_data['updated_by'] = session.get('user_id')
    
    result = db.table("training_exam_bindings").update(update_data)\
        .eq("id", binding_id)\
        .is_("deleted_at", "null")\
        .execute()
    
    if result.data:
        return jsonify({"success": True})
    else:
        return jsonify({"success": False, "message": "更新失败"}), 500

@admin_training_bp.route('/api/admin/training/binding/<int:binding_id>', methods=['DELETE'])
@login_required
@admin_required
def delete_training_binding(binding_id):
    """解除绑定（硬删除）"""
    db = get_supabase_admin()
    
    # 硬删除记录
    result = db.table("training_exam_bindings").delete().eq("id", binding_id).execute()
    
    if result.data:
        return jsonify({"success": True})
    else:
        return jsonify({"success": False, "message": "解除绑定失败"}), 500

# ==================== 培训学员分配管理 API ====================

@admin_training_bp.route('/api/admin/training/<int:training_id>/assign', methods=['POST'])
@login_required
@admin_required
def assign_training_to_users(training_id):
    """分配培训给指定学员（定点推送）"""
    data = request.json
    user_ids = data.get('user_ids', [])
    
    if not user_ids:
        return jsonify({"success": False, "message": "jsonify_please_select_users", "params": []}), 400
    
    db = get_supabase_admin()  # 需要绕过 RLS
    
    # 获取当前培训信息（用于日志）
    training_res = db.table("trainings").select("name").eq("id", training_id).maybe_single().execute()
    training_name = training_res.data.get('name', '') if training_res.data else ''
    
    assigned_count = 0
    skipped_count = 0
    
    for uid in user_ids:
        # 检查是否已分配
        existing = db.table("training_assignments").select("id").eq("training_id", training_id).eq("user_id", uid).execute()
        if not existing.data:
            db.table("training_assignments").insert({
                "training_id": training_id,
                "user_id": uid,
                "created_by": session.get('user_id')
            }).execute()
            assigned_count += 1
        else:
            skipped_count += 1
    
    logger.info(f"培训 {training_id} ({training_name}) 分配给了 {assigned_count} 名学员，跳过 {skipped_count} 名已分配")
    
    return jsonify({
        "success": True, 
        "assigned_count": assigned_count,
        "skipped_count": skipped_count,
        "message": "jsonify_assign_success",
        "params": [assigned_count]
    })


@admin_training_bp.route('/api/admin/training/<int:training_id>/unassign', methods=['POST'])
@login_required
@admin_required
def unassign_training_from_users(training_id):
    """取消分配培训"""
    data = request.json
    user_ids = data.get('user_ids', [])
    
    if not user_ids:
        return jsonify({"success": False, "message": "jsonify_please_select_users", "params": []}), 400
    
    db = get_supabase_admin()  # 需要绕过 RLS
    
    # 获取当前培训信息
    training_res = db.table("trainings").select("name").eq("id", training_id).maybe_single().execute()
    training_name = training_res.data.get('name', '') if training_res.data else ''
    
    # 删除分配记录
    result = db.table("training_assignments").delete().eq("training_id", training_id).in_("user_id", user_ids).execute()
    deleted_count = len(result.data or [])
    
    # 同时删除这些学员的签到记录（如果有）
    db.table("training_attendances").delete().eq("training_id", training_id).in_("user_id", user_ids).execute()
    
    logger.info(f"培训 {training_id} ({training_name}) 取消了 {deleted_count} 名学员的分配")
    
    return jsonify({
        "success": True, 
        "unassigned_count": deleted_count,
        "message": "jsonify_unassign_success",
        "params": [deleted_count]
    })


@admin_training_bp.route('/api/admin/training/<int:training_id>/resign/<user_id>', methods=['POST'])
@login_required
@admin_required
def admin_force_resign_training(training_id, user_id):
    """管理员强制学员重新签到（清空已有签到记录，保留分配关系）"""
    db = get_supabase_admin()
    
    # 检查培训是否存在
    training_res = db.table("trainings").select("name").eq("id", training_id).maybe_single().execute()
    if not training_res.data:
        return jsonify({"success": False, "message": "jsonify_training_not_exist", "params": []}), 404
    
    # 检查用户是否存在
    user_res = db.table("users").select("name_en").eq("id", user_id).maybe_single().execute()
    if not user_res.data:
        return jsonify({"success": False, "message": "jsonify_user_not_exist", "params": []}), 404
    
    training_name = training_res.data.get('name', '')
    user_name = user_res.data.get('name_en', '')
    
    # 删除该学员的签到记录（保留分配关系）
    db.table("training_attendances").delete().eq("training_id", training_id).eq("user_id", user_id).execute()
    
    logger.info(f"培训 {training_id} ({training_name}) 学员 {user_name} ({user_id}) 被强制重新签到")
    
    return jsonify({
        "success": True,
        "message": "jsonify_resign_success",
        "params": [user_name]
    })


@admin_training_bp.route('/api/admin/training/<int:training_id>/users')
@login_required
@admin_required
def get_training_users(training_id):
    """获取培训相关的所有学员及其分配/签到状态（用于状态查看页面）"""
    db = get_supabase()
    admin_db = get_supabase_admin()
    
    # 获取培训信息
    training_res = db.table("trainings").select("country, name").eq("id", training_id).maybe_single().execute()
    if not training_res.data:
        return jsonify({"data": []})
    
    training = training_res.data
    training_name = training.get('name', '') 
    training_country = training.get('country')
    if not training_country:
        return jsonify({"data": []})
    
    # 获取该国家下的所有学员
    users_res = db.table("users").select("id, name_en, email, country, wh_id, wh_name_en, company") \
        .eq("country", training_country) \
        .eq("user_status", "registered") \
        .eq("is_resign", False) \
        .execute()
    users = users_res.data or []
    user_ids = [u['id'] for u in users]
    
    if not user_ids:
        return jsonify({"data": []})
    
    # 获取该培训的分配记录（使用 admin 客户端）
    assign_res = admin_db.table("training_assignments").select("user_id").eq("training_id", training_id).execute()
    assigned_user_ids = {a['user_id'] for a in (assign_res.data or [])}
    
    # 获取签到记录
    att_res = db.table("training_attendances").select("user_id, sign_time, signed_name, signature_url") \
        .eq("training_id", training_id) \
        .in_("user_id", user_ids) \
        .execute()
    
    attendance_map = {}
    for att in (att_res.data or []):
        attendance_map[att['user_id']] = att
    
    # 构建返回数据
    result = []
    for user in users:
        user_id = user['id']
        attendance = attendance_map.get(user_id, {})
        sign_time = attendance.get('sign_time')
        signature_url = attendance.get('signature_url', '')
        has_attendance = user_id in attendance_map
        
        # ✅ 修复：优先检查是否有签到记录（全国推送场景）
        if has_attendance:
            # 有签到记录
            if not signature_url:
                sign_status = 'resign'  # 已签到但无签名（需重新签名）
            else:
                sign_status = 'signed'  # 已签到且有签名
        elif user_id in assigned_user_ids:
            # 有分配但无签到
            sign_status = 'pending'  # 待签到
        else:
            # 既无分配也无签到
            sign_status = 'not_assigned'
        
        result.append({
            "training_id": training_id,
            "training_name": training_res.data.get('name', ''),
            "training_country": training_country,
            "user_id": user_id,
            "user_country": user.get('country', ''),
            "name_en": user.get('name_en', ''),
            "email": user.get('email', ''),
            "wh_id": user.get('wh_id', ''),
            "wh_name_en": user.get('wh_name_en', ''),
            "company": user.get('company', ''),
            "sign_status": sign_status,
            "sign_time": sign_time,
            "signed_name": attendance.get('signed_name', ''),
            "signature_url": signature_url
        })
    
    return jsonify({"data": result})
