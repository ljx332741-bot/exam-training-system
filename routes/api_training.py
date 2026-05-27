# routes/api_training.py
import logging
import json
import pdfkit
from datetime import datetime, timezone
from flask import request, jsonify, render_template, session, flash
from . import training_bp
from services.db import get_supabase
from routes.helpers import login_required, upload_signature

logger = logging.getLogger(__name__)

@training_bp.route('/api/trainings/available')
@login_required
def api_available_trainings():
    """获取当前用户可签到的培训列表（显示有效期内的所有培训，包括未开始的）"""
    db = get_supabase()
    user_id = session['user_id']
    now = datetime.now(timezone.utc).isoformat()
    
    # ✅ 1. 获取当前学员的国家
    user_res = db.table("users").select("country").eq("id", user_id).maybe_single().execute()
    if not user_res.data:
        return jsonify([])
    
    user_country = user_res.data.get('country')
    if not user_country:
        # 学员没有设置国家，返回空列表
        return jsonify([])
    
    # ✅ 2. 查询激活的培训
    trainings_res = db.table("trainings") \
        .select("*") \
        .eq("is_active", True) \
        .execute()

    trainings = trainings_res.data or []

    # ✅ 3. 根据学员国家过滤培训
    filtered_trainings = []
    for t in trainings:
        training_country = t.get('country')
        
        # 跳过没有国家配置的培训
        if not training_country:
            continue
        
        # 解析国家（支持单个字符串或 JSON 数组）
        country_list = []
        if isinstance(training_country, str):
            try:
                # 尝试解析为 JSON 数组
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
        
        # 检查学员国家是否在培训的目标国家中
        if user_country in country_list:
            filtered_trainings.append(t)
    
    # 如果没有符合条件的培训，直接返回空列表
    if not filtered_trainings:
        return jsonify([])
    
    # 查询用户已签到记录
    att_res = db.table("training_attendances") \
        .select("id, training_id, sign_time, signed_name, signature_url") \
        .eq("user_id", user_id) \
        .execute()
    signed_dict = {a['training_id']: a for a in (att_res.data or [])}
    
    result = []
    # ✅ 关键修复：使用 filtered_trainings，而不是 trainings
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
            # 如果已签到但签名URL为空，则需要重新签字
            if not signed_info.get('signature_url'):
                needs_resign = True
        
        # 判断培训状态
        is_future = now < start      # 未开始（未来）
        is_active = start <= now <= end  # 进行中
        is_expired = now > end       # 已过期
        
        # 显示条件：进行中 或 未开始（未来）都显示，已过期的不显示
        if is_expired:
            continue
        
        # 确定状态文本和按钮状态
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
    user_id = session['user_id']
    user_res = db.table("users").select("is_active, user_status").eq("id", user_id).maybe_single().execute()
    if not user_res.data or not user_res.data.get('is_active') or user_res.data.get('user_status') != 'registered':
        return jsonify({"success": False, "message": "用户未完成注册"}), 400
    try:
        exist = db.table("training_attendances").select("id").eq("training_id", training_id).eq("user_id", user_id).maybe_single().execute()
        if exist and exist.data: return jsonify({"success": False, "message": "您已签到过本培训"}), 400
    except: pass
    now = datetime.now(timezone.utc).isoformat()
    tr = db.table("trainings").select("start_time, end_time").eq("id", training_id).maybe_single().execute()
    if not tr or not tr.data: return jsonify({"success": False, "message": "培训不存在"}), 404
    if now < tr.data['start_time']: return jsonify({"success": False, "message": "签到尚未开始"}), 400
    if now > tr.data['end_time']: return jsonify({"success": False, "message": "签到已结束"}), 400
    try: url = upload_signature(sig, training_id, user_id)
    except: return jsonify({"success": False, "message": "签名保存失败"}), 500
    try:
        db.table("training_attendances").insert({"training_id": training_id, "user_id": user_id, "signature_url": url, "signed_name": name, "sign_time": now}).execute()
    except Exception as e: return jsonify({"success": False, "message": "数据保存失败"}), 500
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