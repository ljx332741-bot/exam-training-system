# routes/api_training.py
import logging
import json
import pdfkit
from datetime import datetime, timezone, timedelta
from flask import request, jsonify, render_template, session, flash
from . import training_bp
from services.db import get_supabase, get_supabase_admin
from routes.helpers import login_required, upload_signature

logger = logging.getLogger(__name__)

@training_bp.route('/api/trainings/available')
@login_required
def api_available_trainings():
    """获取当前用户可签到的培训列表（包括已完成考试需要补签的已关闭培训）"""
    db = get_supabase()
    admin_db = get_supabase_admin()
    user_id = session['user_id']
    now = datetime.now(timezone.utc).isoformat()
    
    # 获取当前学员的国家
    user_res = db.table("users").select("country").eq("id", user_id).maybe_single().execute()
    if not user_res.data:
        return jsonify([])
    user_country = user_res.data.get('country')
    if not user_country:
        return jsonify([])

    logger.info(f"========== 学员请求培训列表 ==========")
    logger.info(f"用户ID: {user_id}, 国家: {user_country}")
    
    # ========== 1. 获取学员的所有签到记录 ==========
    att_res = db.table("training_attendances") \
        .select("id, training_id, sign_time, signed_name, signature_url") \
        .eq("user_id", user_id) \
        .execute()
    signed_dict = {a['training_id']: a for a in (att_res.data or [])}
    signed_training_ids = set(signed_dict.keys())
    
    # ========== 2. 找出需要补签的培训ID ==========
    pending_sign_training_ids = set()
    
    # 2.1 已有签到记录但无签名 → 待重新签名
    resign_training_ids = set()
    for att in (att_res.data or []):
        signature_url = att.get('signature_url')
        is_empty = not signature_url or signature_url == '' or signature_url == 'null'
        if is_empty:
            resign_training_ids.add(att['training_id'])
            logger.info(f"待重新签名培训: {att['training_id']}")
    
    # 2.2 已完成绑定考试但未签到 → 待补签
    completed_exams_res = db.table("exam_results").select("exam_id").eq("user_id", user_id).execute()
    completed_exam_ids = [r['exam_id'] for r in (completed_exams_res.data or [])]
    
    if completed_exam_ids:
        # 查找这些考试绑定的培训
        bindings_res = admin_db.table("training_exam_bindings").select("training_id").in_("exam_id", completed_exam_ids).execute()
        for b in (bindings_res.data or []):
            training_id = b['training_id']
            # 检查用户是否已经签到（有签到记录）
            if training_id not in signed_training_ids:
                pending_sign_training_ids.add(training_id)
                logger.info(f"待补签培训（已完成绑定考试）: {training_id}")
    
    # 合并：待重新签名 + 待补签
    all_pending_training_ids = resign_training_ids | pending_sign_training_ids
    logger.info(f"所有待处理培训ID: {list(all_pending_training_ids)}")
    
    # ========== 3. 查询用户被定点分配的培训ID ==========
    assigned_res = admin_db.table("training_assignments").select("training_id").eq("user_id", user_id).execute()
    assigned_training_ids = [a['training_id'] for a in (assigned_res.data or [])]
    logger.info(f"用户被分配的培训ID: {assigned_training_ids}")
    
    # ========== 4. 查询所有培训 ==========
    trainings_res = db.table("trainings").select("*").execute()
    all_trainings = trainings_res.data or []
    logger.info(f"所有培训数量: {len(all_trainings)}")
    
    # ========== 5. 筛选需要显示的培训 ==========
    filtered_trainings = []
    for t in all_trainings:
        training_id = t['id']
        training_country = t.get('country')
        
        if not training_country:
            continue
        
        # 解析国家列表
        country_list = []
        if isinstance(training_country, str):
            try:
                parsed = json.loads(training_country)
                if isinstance(parsed, list):
                    country_list = parsed
                else:
                    country_list = [training_country]
            except json.JSONDecodeError:
                country_list = [training_country]
        elif isinstance(training_country, list):
            country_list = training_country
        else:
            country_list = [str(training_country)]
        
        # 检查该培训是否有任何分配记录
        assign_check = admin_db.table("training_assignments").select("id").eq("training_id", training_id).execute()
        has_targeted_assignments = len(assign_check.data or []) > 0
        
        show_training = False
        
        # 情况1：用户被定点分配了该培训
        if training_id in assigned_training_ids:
            show_training = True
            logger.info(f"培训 {training_id} 用户在分配列表中")
        
        # 情况2：没有分配记录时，按国家过滤（全国推送）
        elif not has_targeted_assignments:
            if user_country in country_list:
                show_training = True
                logger.info(f"培训 {training_id} 国家匹配（全国推送）")
        
        # 情况3：需要补签的培训，强制显示
        if training_id in all_pending_training_ids:
            show_training = True
            logger.info(f"培训 {training_id} 需要补签，强制显示")
        
        if show_training:
            filtered_trainings.append(t)
    
    logger.info(f"过滤后培训数量: {len(filtered_trainings)}")
    
    # ========== 6. 构建返回数据 ==========
    result = []
    for t in filtered_trainings:
        training_id = t['id']
        start = t.get('start_time')
        end = t.get('end_time')
        
        # 跳过没有有效期的培训
        if not start or not end:
            continue
        
        signed_info = signed_dict.get(training_id)
        signed = signed_info is not None
        needs_resign = False
        if signed:
            # 有签到记录但无签名 → 需要重新签名
            if not signed_info.get('signature_url') or signed_info.get('signature_url') == '':
                needs_resign = True
        
        # 判断培训状态
        is_future = now < start
        is_active = start <= now <= end
        is_expired = now > end
        
        # ========== 7. 状态判断逻辑 ==========
        
        # 情况1：未开始 → 显示"未开始"
        if is_future:
            result.append({
                "id": training_id,
                "name": t['name'],
                "start_time": start,
                "end_time": end,
                "signed": False,
                "sign_time": None,
                "signed_name": None,
                "needs_resign": False,
                "status": "未开始",
                "is_future": True,
                "is_active": False,
                "is_expired": False,
                "can_sign": False,
                "button_html": f'<button class="btn btn-secondary" disabled><span data-i18n="not_started">未开始</span></button>'
            })
            continue
        
        # 情况2：已关闭
        if is_expired:
            # 2.1 需要重新签名（有签到记录但无签名）→ 显示"重新签名"
            if needs_resign:
                result.append({
                    "id": training_id,
                    "name": t['name'],
                    "start_time": start,
                    "end_time": end,
                    "signed": True,
                    "sign_time": signed_info.get('sign_time') if signed_info else None,
                    "signed_name": signed_info.get('signed_name') if signed_info else None,
                    "needs_resign": True,
                    "status": "需重新签名",
                    "is_future": False,
                    "is_active": False,
                    "is_expired": True,
                    "can_sign": True,
                    "button_html": f'<button class="btn btn-warning resign-btn" data-id="{training_id}"><i class="bi bi-exclamation-triangle"></i> <span data-i18n="re-sign">重新签名</span></button>'
                })
                continue
            
            # 2.2 待补签（已完成绑定考试但未签到）→ 显示"补签"
            if training_id in pending_sign_training_ids and not signed:
                result.append({
                    "id": training_id,
                    "name": t['name'],
                    "start_time": start,
                    "end_time": end,
                    "signed": False,
                    "sign_time": None,
                    "signed_name": None,
                    "needs_resign": True,  # 复用此字段表示需要补签
                    "status": "需补签",
                    "is_future": False,
                    "is_active": False,
                    "is_expired": True,
                    "can_sign": True,
                    "button_html": f'<button class="btn btn-warning resign-btn" data-id="{training_id}"><i class="bi bi-exclamation-triangle"></i> <span data-i18n="re-sign">补签</span></button>'
                })
                continue
            
            # 2.3 已签到有签名 → 不显示（在"我的签到记录"中查看）
            # 2.4 未签到且无绑定考试 → 不显示
            continue
        
        # 情况3：进行中 (is_active = True)
        if is_active:
            if signed and not needs_resign:
                # 已签到有签名
                result.append({
                    "id": training_id,
                    "name": t['name'],
                    "start_time": start,
                    "end_time": end,
                    "signed": True,
                    "sign_time": signed_info.get('sign_time') if signed_info else None,
                    "signed_name": signed_info.get('signed_name') if signed_info else None,
                    "needs_resign": False,
                    "status": "已签到",
                    "is_future": False,
                    "is_active": True,
                    "is_expired": False,
                    "can_sign": False,
                    "button_html": f'<button class="btn btn-success" disabled><span data-i18n="signed">已签到</span></button>'
                })
            elif needs_resign:
                # 待重新签名（进行中）
                result.append({
                    "id": training_id,
                    "name": t['name'],
                    "start_time": start,
                    "end_time": end,
                    "signed": True,
                    "sign_time": signed_info.get('sign_time') if signed_info else None,
                    "signed_name": signed_info.get('signed_name') if signed_info else None,
                    "needs_resign": True,
                    "status": "需重新签名",
                    "is_future": False,
                    "is_active": True,
                    "is_expired": False,
                    "can_sign": True,
                    "button_html": f'<button class="btn btn-warning resign-btn" data-id="{training_id}"><i class="bi bi-exclamation-triangle"></i> <span data-i18n="re-sign">重新签名</span></button>'
                })
            else:
                # 未签到（进行中）
                result.append({
                    "id": training_id,
                    "name": t['name'],
                    "start_time": start,
                    "end_time": end,
                    "signed": False,
                    "sign_time": None,
                    "signed_name": None,
                    "needs_resign": False,
                    "status": "待签到",
                    "is_future": False,
                    "is_active": True,
                    "is_expired": False,
                    "can_sign": True,
                    "button_html": f'<button class="btn btn-primary sign-btn" data-id="{training_id}"><span data-i18n="sign_now">立即签到</span></button>'
                })
            continue
        
        # 其他情况（理论上不会到这里）
        continue
    
    logger.info(f"最终返回培训数量: {len(result)}")
    return jsonify(result)

@training_bp.route('/api/training/sign', methods=['POST'])
@login_required
def api_training_sign():
    data = request.get_json()
    training_id = data.get('training_id')
    sig = data.get('signature')
    name = data.get('name', '').strip()
    if not training_id or not sig: return jsonify({"success": False, "message": "缺少必要参数"}), 400
    db = get_supabase()
    admin_db = get_supabase_admin()
    user_id = session['user_id']

    # 1. 验证用户状态
    user_res = db.table("users").select("is_active, user_status").eq("id", user_id).maybe_single().execute()
    if not user_res.data or not user_res.data.get('is_active') or user_res.data.get('user_status') != 'registered':
        return jsonify({"success": False, "message": "用户未完成注册"}), 400

    # 2. 检查是否重复签到
    try:
        exist = db.table("training_attendances").select("id").eq("training_id", training_id).eq("user_id", user_id).execute()
        if exist and exist.data: return jsonify({"success": False, "message": "您已签到过本培训"}), 400
    except: pass

    # 3. 检查培训有效期
    now = datetime.now(timezone.utc).isoformat()
    tr = db.table("trainings").select("start_time, end_time").eq("id", training_id).maybe_single().execute()
    if not tr or not tr.data: return jsonify({"success": False, "message": "培训不存在"}), 404
    if now < tr.data['start_time']: return jsonify({"success": False, "message": "签到尚未开始"}), 400
    if now > tr.data['end_time']: return jsonify({"success": False, "message": "签到已结束"}), 400

    # 4. 保存签名
    try: url = upload_signature(sig, training_id, user_id)
    except: return jsonify({"success": False, "message": "签名保存失败"}), 500

    # 5. 保存签到记录
    try:
        db.table("training_attendances").insert({"training_id": training_id, "user_id": user_id, "signature_url": url, "signed_name": name, "sign_time": now}).execute()
    except Exception as e: return jsonify({"success": False, "message": "数据保存失败"}), 500

    # 签到成功后，检查是否有分配记录，如果没有则创建（全国推送场景）
    try:
        assign_check = admin_db.table("training_assignments").select("id").eq("training_id", training_id).eq("user_id", user_id).execute()
        if not assign_check.data:
            admin_db.table("training_assignments").insert({
                "training_id": training_id,
                "user_id": user_id,
                "created_by": user_id  # 由学员自己触发
            }).execute()
            logger.info(f"全国推送场景：为学员 {user_id} 自动创建分配记录")
    except Exception as e:
        logger.warning(f"创建分配记录失败: {e}")
    # ========== 6. 签到成功后自动分配绑定的考试 ==========

    # 签到成功后
    logger.info(f"========== 用户签到成功 ==========")
    logger.info(f"用户: {user_id}, 培训: {training_id}")
    try:
        # 查询该培训绑定的考试
        bindings_res = admin_db.table("training_exam_bindings").select("exam_id, pass_score")\
            .eq("training_id", training_id)\
            .eq("is_auto_assign", True)\
            .is_("deleted_at", "null")\
            .execute()
        
        for binding in (bindings_res.data or []):
            exam_id = binding['exam_id']
            
            # 检查是否已分配
            existing = admin_db.table("exam_assignments").select("id")\
                .eq("exam_id", exam_id)\
                .eq("user_id", user_id)\
                .execute()
            
            if not existing.data:
                # 分配考试
                admin_db.table("exam_assignments").insert({
                    "exam_id": exam_id,
                    "user_id": user_id,
                    "created_by": user_id
                }).execute()
                logger.info(f"培训签到后自动分配考试: user={user_id}, exam={exam_id}")
                
                # ✅ 激活绑定模式的考试（如果尚未激活）
                exam_res = admin_db.table("exams").select("is_active, is_binding_exam").eq("id", exam_id).maybe_single().execute()
                if exam_res.data and exam_res.data.get('is_binding_exam') and not exam_res.data.get('is_active'):
                    # 设置默认有效期
                    now = datetime.now(timezone.utc).isoformat()
                    end_time = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
                    admin_db.table("exams").update({
                        "is_active": True,
                        "status": "active",
                        "start_time": now,
                        "end_time": end_time
                    }).eq("id", exam_id).execute()
                    logger.info(f"绑定考试 {exam_id} 已激活")
        
    except Exception as e:
        logger.error(f"自动分配考试失败: {e}")
    
    return jsonify({"success": True, "sign_time": now})

@training_bp.route('/api/training/resign', methods=['POST'])
@login_required
def api_resign_training():
    data = request.get_json()
    training_id = data.get('training_id')
    sig = data.get('signature')
    name = data.get('name', '').strip()
    if not training_id or not sig: return jsonify({"success": False, "message": "缺少必要参数"}), 400
    db = get_supabase()
    user_id = session['user_id']
    exist = db.table("training_attendances").select("id, signature_url").eq("training_id", training_id).eq("user_id", user_id).maybe_single().execute()
    if not exist.data: return jsonify({"success": False, "message": "签到记录不存在"}), 404
    if exist.data.get('signature_url'): return jsonify({"success": False, "message": "签名已存在，无需重新签字"}), 400
    try: url = upload_signature(sig, training_id, user_id)
    except: return jsonify({"success": False, "message": "签名保存失败"}), 500
    db.table("training_attendances").update({"signature_url": url, "signed_name": name}).eq("id", exist.data['id']).execute()
    return jsonify({"success": True})