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
    """获取当前用户可签到的培训列表（显示有效期内的所有培训，包括未开始的）"""
    db = get_supabase()
    user_id = session['user_id']
    now = datetime.now(timezone.utc).isoformat()
    
    # 获取当前学员的国家
    user_res = db.table("users").select("country").eq("id", user_id).maybe_single().execute()
    if not user_res.data:
        return jsonify([])
    user_country = user_res.data.get('country')
    if not user_country:
        return jsonify([])

    # 详细日志
    logger.info(f"========== 学员请求培训列表 ==========")
    logger.info(f"用户ID: {user_id}")
    logger.info(f"用户国家: {user_country}")
    
    # 查询该用户被定点分配的培训ID
    assigned_res = db.table("training_assignments").select("training_id").eq("user_id", user_id).execute()
    assigned_training_ids = [a['training_id'] for a in (assigned_res.data or [])]
    logger.info(f"用户被分配的培训ID: {assigned_training_ids}")
    
    # 查询激活的培训
    trainings_res = db.table("trainings").select("*").eq("is_active", True).execute()
    trainings = trainings_res.data or []
    logger.info(f"所有激活的培训ID: {[t['id'] for t in trainings]}")
    
    # 根据学员国家过滤培训
    filtered_trainings = []
    for t in trainings:
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

        # 正确判断是否有分配记录
        assign_check = db.table("training_assignments").select("id").eq("training_id", t['id']).execute()
        has_targeted_assignments = len(assign_check.data or []) > 0
        
        show_training = False
        
        # 情况1：用户被定点分配了该培训
        if t['id'] in assigned_training_ids:
            show_training = True
        
        # 情况2：没有分配记录时，才按国家过滤（全国推送）
        elif not has_targeted_assignments:
            if user_country in country_list:
                show_training = True
        
        # 情况3：有分配记录但用户未被分配 → 不显示
        
        if show_training:
            filtered_trainings.append(t)
    
    logger.info(f"过滤后培训数量: {len(filtered_trainings)}")
    
    # 查询用户已签到记录
    att_res = db.table("training_attendances") \
        .select("id, training_id, sign_time, signed_name, signature_url") \
        .eq("user_id", user_id) \
        .execute()
    signed_dict = {a['training_id']: a for a in (att_res.data or [])}
    
    result = []
    for t in filtered_trainings:
        start = t.get('start_time')
        end = t.get('end_time')
        
        # 跳过没有有效期的培训
        if not start or not end:
            continue
        
        signed_info = signed_dict.get(t['id'])
        signed = signed_info is not None
        needs_resign = False
        if signed:
            if not signed_info.get('signature_url'):
                needs_resign = True
        
        # 判断培训状态
        is_future = now < start
        is_active = start <= now <= end
        is_expired = now > end
        
        if is_expired:
            continue
        
        if is_future:
            status_text = "未开始"
            status_badge = "bg-secondary"
            can_sign = False
            button_html = f'<button class="btn btn-secondary" disabled><span data-i18n="not_started">未开始</span></button>'
        elif is_active:
            if signed and not needs_resign:
                status_text = "已签到"
                status_badge = "bg-success"
                can_sign = False
                button_html = f'<button class="btn btn-success" disabled><span data-i18n="signed">已签到</span></button>'
            else:
                status_text = "待签到" if not signed else "需重新签名"
                status_badge = "bg-warning text-dark"
                can_sign = True
                button_class = "btn-warning resign-btn" if needs_resign else "btn-primary sign-btn"
                button_text = "重新签名" if needs_resign else "立即签到"
                button_html = f'<button class="btn {button_class}" data-id="{t["id"]}">{button_text}</button>'
        else:
            continue
        
        result.append({
            "id": t['id'],
            "name": t['name'],
            "start_time": t['start_time'],
            "end_time": t['end_time'],
            "signed": signed,
            "sign_time": signed_info['sign_time'] if signed_info else None,
            "signed_name": signed_info['signed_name'] if signed_info else None,
            "needs_resign": needs_resign,
            "status": status_text,
            "is_future": is_future,
            "is_active": is_active,
            "can_sign": can_sign,
            "button_html": button_html
        })
    
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
        exist = db.table("training_attendances").select("id").eq("training_id", training_id).eq("user_id", user_id).maybe_single().execute()
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
    
    # ========== 6. 签到成功后自动分配绑定的考试 ==========

    # 签到成功后
    logger.info(f"========== 用户签到成功 ==========")
    logger.info(f"用户: {user_id}, 培训: {training_id}")
    try:
        # 查询该培训绑定的考试
        bindings_res = db.table("training_exam_bindings").select("exam_id, pass_score")\
            .eq("training_id", training_id)\
            .eq("is_auto_assign", True)\
            .eq("deleted_at", None)\
            .execute()
        
        for binding in (bindings_res.data or []):
            exam_id = binding['exam_id']
            
            # 检查是否已分配
            existing = db.table("exam_assignments").select("id")\
                .eq("exam_id", exam_id)\
                .eq("user_id", user_id)\
                .execute()
            
            if not existing.data:
                # 分配考试
                db.table("exam_assignments").insert({
                    "exam_id": exam_id,
                    "user_id": user_id,
                    "created_by": user_id
                }).execute()
                logger.info(f"培训签到后自动分配考试: user={user_id}, exam={exam_id}")
                
                # ✅ 激活绑定模式的考试（如果尚未激活）
                exam_res = db.table("exams").select("is_active, is_binding_exam").eq("id", exam_id).maybe_single().execute()
                if exam_res.data and exam_res.data.get('is_binding_exam') and not exam_res.data.get('is_active'):
                    # 设置默认有效期
                    now = datetime.now(timezone.utc).isoformat()
                    end_time = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
                    db.table("exams").update({
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