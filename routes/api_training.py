# routes/api_training.py
import logging
import json
import pdfkit
from datetime import datetime, timezone, timedelta
from flask import request, jsonify, render_template, session, flash
from . import training_bp
from services.db import get_supabase, get_supabase_admin
from routes.helpers import login_required, upload_signature
from utils.permissions import get_admin_allowed_countries, is_developer
from utils.manage_messages import log_exam_auto_extend, log_exam_assign_from_signin

# 导入新的工具函数
from utils.training_helpers import (
    parse_training_countries,
    get_training_primary_country,
    training_has_country,
    training_matches_any_country,
    filter_trainings_by_country,
    get_training_countries_display,
    normalize_training_countries,
    parse_country_list  # 向后兼容
)
logger = logging.getLogger(__name__)

logger.info("✅ api_training.py 蓝图 training_bp 已加载")
logger.info(f"   resign 路由: /api/training/resign")

# ============================================================
# 考试分配辅助函数
# ============================================================

def ensure_exam_assignment(exam_id, user_id, created_by):
    """
    确保考试分配记录存在（使用 upsert）
    这是唯一创建 exam_assignments 的入口
    """
    admin_db = get_supabase_admin()
    
    # 使用 upsert 避免并发竞态
    try:
        admin_db.table("exam_assignments").upsert({
            "exam_id": exam_id,
            "user_id": user_id,
            "created_by": created_by,
            "assigned_at": datetime.now(timezone.utc).isoformat(),
            "deleted_at": None,
            "deleted_by": None
        }, on_conflict="exam_id, user_id").execute()
        logger.info(f"✅ 考试分配 upsert 成功: user={user_id}, exam={exam_id}")
        return True
    except Exception as e:
        # 如果 upsert 不支持，降级到传统方式
        logger.warning(f"⚠️ upsert 失败，降级到传统方式: {e}")
        return _ensure_exam_assignment_fallback(exam_id, user_id, created_by)


def _ensure_exam_assignment_fallback(exam_id, user_id, created_by):
    """
    降级方案：先查后插（带并发保护）
    """
    admin_db = get_supabase_admin()
    
    try:
        # 检查是否已存在（包括软删除）
        existing = admin_db.table("exam_assignments").select("id,deleted_at")\
            .eq("exam_id", exam_id)\
            .eq("user_id", user_id)\
            .execute()
        
        # 情况1：已有有效分配
        if existing.data and existing.data[0].get('deleted_at') is None:
            logger.info(f"用户 {user_id} 已分配考试 {exam_id}，跳过")
            return True
        
        # 情况2：有软删除记录，恢复
        if existing.data and existing.data[0].get('deleted_at') is not None:
            admin_db.table("exam_assignments").update({
                "deleted_at": None,
                "deleted_by": None,
                "assigned_at": datetime.now(timezone.utc).isoformat()
            }).eq("id", existing.data[0]['id']).execute()
            logger.info(f"✅ 恢复已删除的考试分配: user={user_id}, exam={exam_id}")
            return True
        
        # 情况3：没有记录，创建新分配
        admin_db.table("exam_assignments").insert({
            "exam_id": exam_id,
            "user_id": user_id,
            "created_by": created_by,
            "assigned_at": datetime.now(timezone.utc).isoformat()
        }).execute()
        logger.info(f"✅ 创建考试分配: user={user_id}, exam={exam_id}")
        return True
        
    except Exception as e:
        # 唯一约束冲突（并发情况）
        if '23505' in str(e) or 'duplicate' in str(e).lower():
            logger.info(f"用户 {user_id} 已分配考试 {exam_id}（并发），视为成功")
            return True
        # 其他错误
        logger.error(f"❌ 考试分配失败: {e}")
        return False

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
    att_res = admin_db.table("training_attendances") \
        .select("id,training_id,sign_time,signed_name,signature_url") \
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
    logger.info(f"✅ resign_training_ids: {list(resign_training_ids)}")

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
        
        # 使用新的工具函数解析国家列表
        country_list = parse_training_countries(t)
        
        if not country_list:
            continue
        
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
    
    # ========== 6. 🔥 新增：批量获取培训-考试绑定关系 ==========
    training_ids = [t['id'] for t in filtered_trainings]
    
    binding_map = {}
    if training_ids:
        bind_res = admin_db.table("training_exam_bindings") \
            .select("training_id, exam_id") \
            .in_("training_id", training_ids) \
            .is_("deleted_at", "null") \
            .execute()
        
        for b in (bind_res.data or []):
            tid = b['training_id']
            if tid not in binding_map:
                binding_map[tid] = []
            binding_map[tid].append(b['exam_id'])
    
    # 查询用户已完成的考试
    completed_exam_ids_set = set()
    if binding_map:
        all_exam_ids = []
        for exam_ids in binding_map.values():
            all_exam_ids.extend(exam_ids)
        
        if all_exam_ids:
            exam_res = db.table("exam_results") \
                .select("exam_id") \
                .eq("user_id", user_id) \
                .in_("exam_id", all_exam_ids) \
                .is_("deleted_at", "null") \
                .execute()
            completed_exam_ids_set = set([r['exam_id'] for r in (exam_res.data or [])])
    
    # ========== 7. 构建返回数据 ==========
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
        
        # 🔥 获取绑定信息
        binding_exam_ids = binding_map.get(training_id, [])
        has_binding = len(binding_exam_ids) > 0
        
        # 检查用户是否完成了该培训的所有绑定考试
        is_exam_completed = False
        if has_binding:
            # 检查是否完成了所有绑定的考试（全部完成才可签到）
            is_exam_completed = all(eid in completed_exam_ids_set for eid in binding_exam_ids)
        
        logger.info(f"培训 {training_id}: has_binding={has_binding}, is_exam_completed={is_exam_completed}")
        
        # ========== 8. 状态判断逻辑 ==========
        
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
                "has_binding": has_binding,
                "is_exam_completed": is_exam_completed,
                "binding_exam_ids": binding_exam_ids,
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
                    "signed": False,
                    "sign_time": signed_info.get('sign_time') if signed_info else None,
                    "signed_name": signed_info.get('signed_name') if signed_info else None,
                    "needs_resign": True,
                    "status": "需重新签名",
                    "is_future": False,
                    "is_active": False,
                    "is_expired": True,
                    "can_sign": True,
                    "has_binding": has_binding,
                    "is_exam_completed": is_exam_completed,
                    "binding_exam_ids": binding_exam_ids,
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
                    "has_binding": has_binding,
                    "is_exam_completed": is_exam_completed,
                    "binding_exam_ids": binding_exam_ids,
                    "button_html": f'<button class="btn btn-warning resign-btn" data-id="{training_id}"><i class="bi bi-exclamation-triangle"></i> <span data-i18n="re-sign">补签</span></button>'
                })
                continue
            
            # 2.3 已签到有签名 → 不显示（在"我的签到记录"中查看）
            # 2.4 未签到且无绑定考试 → 不显示
            continue
        
        # 情况3：进行中 (is_active = True)
        if is_active:
            # 🔥 判断是否可以签到（有绑定且未完成考试 → 不可签到）
            can_sign = not (has_binding and not is_exam_completed)
            
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
                    "has_binding": has_binding,
                    "is_exam_completed": is_exam_completed,
                    "binding_exam_ids": binding_exam_ids,
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
                    "has_binding": has_binding,
                    "is_exam_completed": is_exam_completed,
                    "binding_exam_ids": binding_exam_ids,
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
                    "can_sign": can_sign,
                    "has_binding": has_binding,
                    "is_exam_completed": is_exam_completed,
                    "binding_exam_ids": binding_exam_ids,
                    "button_html": _generate_button_html(training_id, can_sign, is_active, signed, needs_resign, has_binding, is_exam_completed)
                })
            continue
        
        # 其他情况（理论上不会到这里）
        continue
    
    logger.info(f"最终返回培训数量: {len(result)}")
    return jsonify(result)

def _generate_button_html(training_id, can_sign, is_active, signed, needs_resign, has_binding, is_exam_completed):
    """根据状态生成按钮HTML（包含国际化属性）"""
    if has_binding and not is_exam_completed:
        # 有绑定考试且未完成 → 显示"请先完成考试"
        return f'''
            <button class="btn btn-secondary" disabled data-i18n-title="exam_required">
                <span data-i18n="complete_exam_first">请先完成考试</span>
            </button>
        '''
    elif needs_resign:
        return f'''
            <button class="btn btn-warning resign-btn" data-id="{training_id}">
                <i class="bi bi-exclamation-triangle"></i> 
                <span data-i18n="re-sign">重新签名</span>
            </button>
        '''
    elif signed:
        return f'''
            <button class="btn btn-success" disabled>
                <span data-i18n="signed">已签到</span>
            </button>
        '''
    elif is_active:
        return f'''
            <button class="btn btn-primary sign-btn" data-id="{training_id}">
                <span data-i18n="sign_now">立即签到</span>
            </button>
        '''
    else:
        return f'''
            <button class="btn btn-secondary" disabled>
                <span data-i18n="not_started">未开始</span>
            </button>
        '''

@training_bp.route('/api/trainings/for_photos')
@login_required
def api_trainings_for_photos():
    """
    获取照片上传可选择的培训列表
    普通学员：与照片墙保持一致
    """
    db = get_supabase()
    admin_db = get_supabase_admin()
    user_id = session['user_id']
    current_role = session.get('role')
    
    # 获取用户信息
    user_res = db.table("users").select("country,role").eq("id", user_id).maybe_single().execute()
    if not user_res.data:
        return jsonify([])
    
    user_country = user_res.data.get('country')
    user_role = user_res.data.get('role')
    
    # 判断是否开发者
    is_dev = is_developer()
    
    # ============================================================
    # 1. 获取所有培训
    # ============================================================
    trainings_res = db.table("trainings").select("*").execute()
    all_trainings = trainings_res.data or []
    
    # ============================================================
    # 2. 获取管理员权限范围
    # ============================================================
    allowed_countries = get_admin_allowed_countries()
    
    # ============================================================
    # 3. 根据角色过滤
    # ============================================================
    filtered_trainings = []
    
    # 情况1：开发者 - 看到所有培训
    if is_dev:
        filtered_trainings = all_trainings
        result = _build_photo_training_response(db, filtered_trainings)
        result.sort(key=lambda x: x.get('created_at', ''), reverse=True)
        return jsonify(result)
    
    # 情况2：管理员或超管
    if current_role in ['admin', 'super_admin']:
        for t in all_trainings:
            training_country = t.get('country')
            if not training_country:
                continue
            
            country_list = _parse_country_list(training_country)
            
            if allowed_countries is None:
                filtered_trainings.append(t)
                continue
            
            if allowed_countries:
                matched = any(c in allowed_countries for c in country_list)
                if matched:
                    filtered_trainings.append(t)
        
        result = _build_photo_training_response(db, filtered_trainings)
        result.sort(key=lambda x: x.get('created_at', ''), reverse=True)
        return jsonify(result)
    
    # ============================================================
    # 情况3：普通学员 - 与照片墙逻辑保持一致
    # ============================================================
    if not user_country:
        return jsonify([])
    
    # 1. 获取用户相关的培训ID（与照片墙完全一致）
    assigned_res = admin_db.table("training_assignments").select("training_id").eq("user_id", user_id).execute()
    assigned_training_ids = {a['training_id'] for a in (assigned_res.data or [])}
    
    att_res = admin_db.table("training_attendances") \
        .select("training_id") \
        .eq("user_id", user_id) \
        .execute()
    signed_training_ids = {a['training_id'] for a in (att_res.data or [])}
    
    completed_exams_res = db.table("exam_results").select("exam_id").eq("user_id", user_id).execute()
    completed_exam_ids = [r['exam_id'] for r in (completed_exams_res.data or [])]
    
    pending_sign_training_ids = set()
    if completed_exam_ids:
        bindings_res = admin_db.table("training_exam_bindings").select("training_id").in_("exam_id", completed_exam_ids).execute()
        for b in (bindings_res.data or []):
            training_id_tmp = b['training_id']
            if training_id_tmp not in signed_training_ids:
                pending_sign_training_ids.add(training_id_tmp)
    
    # 2. 合并所有可访问的培训ID（与照片墙完全一致）
    accessible_training_ids = assigned_training_ids | signed_training_ids | pending_sign_training_ids
    
    # 3. 如果没有可访问的培训，返回空
    if not accessible_training_ids:
        return jsonify([])
    
    # 4. 筛选培训
    for t in all_trainings:
        training_id = t.get('id')
        training_country = t.get('country')
        
        if not training_country:
            continue
        
        country_list = _parse_country_list(training_country)
        
        # 必须匹配用户国家
        if user_country not in country_list:
            continue
        
        # 只返回可访问的培训（与照片墙完全一致）
        if training_id in accessible_training_ids:
            filtered_trainings.append(t)
    
    result = _build_photo_training_response(db, filtered_trainings)
    result.sort(key=lambda x: x.get('created_at', ''), reverse=True)
    return jsonify(result)

@training_bp.route('/api/training/sign', methods=['POST'])
@login_required
def api_training_sign():
    data = request.get_json()
    training_id = data.get('training_id')
    sig = data.get('signature')
    name = data.get('name', '').strip()
    
    if not training_id or not sig:
        return jsonify({"success": False, "message": "缺少必要参数"}), 400
    
    db = get_supabase()
    admin_db = get_supabase_admin()
    user_id = session['user_id']

    # 1. 验证用户状态
    user_res = db.table("users").select("is_active,user_status,country,role").eq("id", user_id).maybe_single().execute()
    if not user_res.data or not user_res.data.get('is_active') or user_res.data.get('user_status') != 'registered':
        return jsonify({"success": False, "message": "用户未完成注册"}), 400
    
    user_country = user_res.data.get('country')
    if not user_country:
        user_country = session.get('user_country')
    
    user_role = user_res.data.get('role', 'user')
    is_developer = user_role == 'developer'

    # 2. 检查培训有效期
    now = datetime.now(timezone.utc).isoformat()
    tr = db.table("trainings").select("start_time,end_time,countries,country").eq("id", training_id).maybe_single().execute()
    if not tr or not tr.data:
        return jsonify({"success": False, "message": "培训不存在"}), 404
    
    start_time = tr.data.get('start_time')
    end_time = tr.data.get('end_time')
    
    if not start_time or not end_time:
        return jsonify({"success": False, "message": "培训未设置有效期"}), 400
    
    if now < start_time:
        return jsonify({"success": False, "message": "签到尚未开始"}), 400
    if now > end_time:
        return jsonify({"success": False, "message": "签到已结束"}), 400

    # 3. 国家匹配检查
    from utils.training_helpers import parse_training_countries
    training_countries = parse_training_countries(tr.data)
    
    if training_countries:
        if is_developer and not user_country:
            logger.info(f"开发者 {user_id} 无国家信息，允许签到")
        elif user_country:
            if user_country not in training_countries:
                logger.warning(f"用户 {user_id} 国家 {user_country} 不在培训 {training_id} 国家列表 {training_countries} 中")
                return jsonify({
                    "success": False, 
                    "message": "您的国家不在该培训的签到范围内"
                }), 403
        else:
            return jsonify({
                "success": False, 
                "message": "用户未设置国家，无法签到多国家培训"
            }), 403

    # ✅ 4. 检查是否重复签到（使用 admin_db）
    try:
        exist = admin_db.table("training_attendances") \
            .select("id,signature_url") \
            .eq("training_id", training_id) \
            .eq("user_id", user_id) \
            .maybe_single() \
            .execute()
        
        if exist and exist.data:
            # 已有签名 → 已签到
            if exist.data.get('signature_url'):
                return jsonify({"success": False, "message": "您已签到过本培训"}), 400
            # 无签名 → 允许重新签名（但这里走的是 sign 接口，不是 resign）
            # 实际上如果有记录但无签名，应该走 resign 接口
            logger.info(f"用户 {user_id} 已有签到记录但无签名，请使用重新签名功能")
            return jsonify({"success": False, "message": "请使用重新签名功能"}), 400
    except Exception as e:
        logger.warning(f"检查重复签到失败: {e}")

    # 5. 保存签名
    try:
        url = upload_signature(sig, training_id, user_id)
    except Exception as e:
        logger.error(f"签名上传失败: {e}")
        return jsonify({"success": False, "message": "签名保存失败"}), 500

    # 6. 保存签到记录
    try:
        admin_db.table("training_attendances").insert({
            "training_id": training_id, 
            "user_id": user_id, 
            "signature_url": url, 
            "signed_name": name, 
            "sign_time": now
        }).execute()
        logger.info(f"✅ 签到记录保存成功: user={user_id}, training={training_id}")
    except Exception as e:
        logger.error(f"签到记录保存失败: {e}")
        return jsonify({"success": False, "message": "数据保存失败"}), 500

    # 7. 创建培训分配记录
    try:
        assign_check = admin_db.table("training_assignments").select("id").eq("training_id", training_id).eq("user_id", user_id).execute()
        if not assign_check.data:
            admin_db.table("training_assignments").insert({
                "training_id": training_id,
                "user_id": user_id,
                "created_by": user_id
            }).execute()
            logger.info(f"为学员 {user_id} 自动创建培训分配记录")
    except Exception as e:
        logger.warning(f"创建培训分配记录失败: {e}")

    # 8. 自动分配绑定的考试
    try:
        bindings_res = admin_db.table("training_exam_bindings").select("exam_id,pass_score")\
            .eq("training_id", training_id)\
            .eq("is_auto_assign", True)\
            .is_("deleted_at", "null")\
            .execute()
        
        for binding in (bindings_res.data or []):
            exam_id = binding['exam_id']
            
            # 检查考试是否过期，如果过期则自动延长
            exam_res = admin_db.table("exams").select("title,start_time,end_time,status,is_active,is_binding_exam").eq("id", exam_id).maybe_single().execute()
            if exam_res.data:
                exam_title = exam_res.data.get('title', f'考试#{exam_id}')
                now_utc = datetime.now(timezone.utc)
                end_time_exam = exam_res.data.get('end_time')
                
                if end_time_exam:
                    try:
                        end_dt = datetime.fromisoformat(end_time_exam.replace('Z', '+00:00'))
                        if end_dt < now_utc:
                            new_end_time = (now_utc + timedelta(days=2)).isoformat()
                            new_start_time = now_utc.isoformat()
                            admin_db.table("exams").update({
                                "start_time": new_start_time,
                                "end_time": new_end_time,
                                "status": "active",
                                "is_active": True
                            }).eq("id", exam_id).execute()
                            logger.info(f"考试 {exam_id} 已自动延长有效期")
                    except Exception as e:
                        logger.warning(f"检查考试有效期失败: {e}")
            
            # 分配考试
            assignment_created = ensure_exam_assignment(exam_id, user_id, user_id)
            
            if assignment_created:
                try:
                    log_exam_assign_from_signin(
                        db=admin_db,
                        exam_id=exam_id,
                        exam_title=exam_title or f'考试#{exam_id}',
                        user_id=user_id,
                        user_name=name or user_id
                    )
                except Exception as msg_error:
                    logger.warning(f"记录消息失败: {msg_error}")

            # 激活绑定模式的考试
            if exam_res and exam_res.data:
                if exam_res.data.get('is_binding_exam') and not exam_res.data.get('is_active'):
                    now_utc = datetime.now(timezone.utc).isoformat()
                    end_time_new = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
                    admin_db.table("exams").update({
                        "is_active": True,
                        "status": "active",
                        "start_time": now_utc,
                        "end_time": end_time_new
                    }).eq("id", exam_id).execute()
                    logger.info(f"绑定考试 {exam_id} 已激活")
        
    except Exception as e:
        logger.error(f"自动分配考试失败: {e}")
    
    return jsonify({"success": True, "sign_time": now})

@training_bp.route('/api/training/resign', methods=['POST'])
@login_required
def api_resign_training():
    """重新签名接口（支持补签场景）"""
    logger.info("=" * 60)
    logger.info("📝 api_resign_training 被调用")
    logger.info("=" * 60)
    
    data = request.get_json()
    logger.info(f"请求数据: {data}")
    
    training_id = data.get('training_id') if data else None
    sig = data.get('signature') if data else None
    name = data.get('name', '').strip() if data else ''
    
    if not training_id:
        return jsonify({"success": False, "message": "缺少培训ID"}), 400
    
    if not sig:
        return jsonify({"success": False, "message": "缺少签名"}), 400

    db = get_supabase()
    admin_db = get_supabase_admin()
    user_id = session['user_id']
    now = datetime.now(timezone.utc).isoformat()
    logger.info(f"用户ID: {user_id}, 培训ID: {training_id}")

    # ============================================================
    # 核心逻辑：查询签到记录，如果没有则自动创建
    # ============================================================
    try:
        exist = db.table("training_attendances") \
            .select("*") \
            .eq("training_id", training_id) \
            .eq("user_id", user_id) \
            .maybe_single() \
            .execute()
        
        logger.info(f"查询结果: {exist}")
        
        # ========== 情况1：没有签到记录 → 自动创建（补签场景） ==========
        if not exist or not exist.data:
            logger.info(f"用户 {user_id} 在培训 {training_id} 下没有签到记录，自动创建（补签场景）")
            
            # 获取用户信息用于补签记录
            user_res = db.table("users").select("name_en").eq("id", user_id).maybe_single().execute()
            user_name = user_res.data.get('name_en', name or '系统补签') if user_res else (name or '系统补签')
            
            # 创建签到记录（先插入无签名记录）
            insert_result = admin_db.table("training_attendances").insert({
                "training_id": training_id,
                "user_id": user_id,
                "sign_time": now,
                "signed_name": user_name,
                "signature_url": None  # 先无签名，后续更新
            }).execute()
            
            if not insert_result.data:
                logger.error(f"创建签到记录失败")
                return jsonify({"success": False, "message": "创建签到记录失败"}), 500
            
            # 重新查询获取刚创建的记录ID
            exist = db.table("training_attendances") \
                .select("*") \
                .eq("training_id", training_id) \
                .eq("user_id", user_id) \
                .maybe_single() \
                .execute()
            
            logger.info(f"✅ 自动创建签到记录成功: id={exist.data['id'] if exist and exist.data else 'N/A'}")
            
            # 同时创建培训分配记录（确保用户被分配到该培训）
            try:
                assign_check = admin_db.table("training_assignments").select("id").eq("training_id", training_id).eq("user_id", user_id).execute()
                if not assign_check.data:
                    admin_db.table("training_assignments").insert({
                        "training_id": training_id,
                        "user_id": user_id,
                        "created_by": user_id
                    }).execute()
                    logger.info(f"✅ 自动创建培训分配记录: user={user_id}, training={training_id}")
            except Exception as e:
                logger.warning(f"创建培训分配记录失败: {e}")
        
        # ========== 情况2：已有签到记录但已有签名 → 不允许重复签名 ==========
        elif exist.data.get('signature_url'):
            logger.warning(f"用户 {user_id} 在培训 {training_id} 下已有签名")
            return jsonify({"success": False, "message": "签名已存在，无需重新签字"}), 400
        
        # ========== 情况3：已有签到记录但无签名 → 正常重新签名 ==========
        else:
            logger.info(f"用户 {user_id} 在培训 {training_id} 下已有签到记录，更新签名")
            
    except Exception as e:
        logger.error(f"查询/创建签到记录失败: {e}", exc_info=True)
        return jsonify({"success": False, "message": f"操作失败: {str(e)}"}), 500

    # ============================================================
    # 保存签名
    # ============================================================
    try:
        url = upload_signature(sig, training_id, user_id)
        logger.info(f"签名上传成功: {url[:50] if url else 'N/A'}...")
    except Exception as e:
        logger.error(f"签名上传失败: {e}", exc_info=True)
        return jsonify({"success": False, "message": f"签名保存失败: {str(e)}"}), 500

    # ============================================================
    # 更新签名
    # ============================================================
    try:
        admin_db.table("training_attendances") \
            .update({
                "signature_url": url, 
                "signed_name": name,
                "sign_time": now
            }) \
            .eq("id", exist.data['id']) \
            .execute()
        logger.info(f"✅ 签名更新成功: user={user_id}, training={training_id}")
    except Exception as e:
        logger.error(f"更新签名失败: {e}", exc_info=True)
        return jsonify({"success": False, "message": f"更新签名失败: {str(e)}"}), 500

    return jsonify({"success": True, "message": "补签成功"})

# ============================================================
# 辅助函数（放在文件顶部或末尾）
# ============================================================

def _parse_country_list(training_country):
    """解析培训国家列表（复用原逻辑）"""
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
    return country_list

def _build_photo_training_response(db, trainings):
    """构建照片上传培训响应数据（包含动态状态）"""
    from datetime import datetime, timezone
    from services.db import get_supabase_admin
    
    if not trainings:
        return []
    
    result = []
    now = datetime.now(timezone.utc)
    
    # 批量获取照片数量（一次性查询所有培训的照片数量）
    training_ids = [t['id'] for t in trainings]
    photo_counts = {}
    
    try:
        admin_db = get_supabase_admin()
        photo_res = admin_db.table("training_photos") \
            .select("training_id") \
            .in_("training_id", training_ids) \
            .eq("is_deleted", False) \
            .execute()
        
        for p in (photo_res.data or []):
            tid = p['training_id']
            photo_counts[tid] = photo_counts.get(tid, 0) + 1
    except Exception as e:
        logger.warning(f"批量获取照片数量失败: {e}")
    
    for t in trainings:
        training_id = t['id']
        
        # 计算动态状态
        start_time = t.get('start_time')
        end_time = t.get('end_time')
        
        if not start_time or not end_time:
            dynamic_status = 'draft'
        else:
            try:
                if isinstance(start_time, str):
                    start_dt = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
                else:
                    start_dt = start_time
                if isinstance(end_time, str):
                    end_dt = datetime.fromisoformat(end_time.replace('Z', '+00:00'))
                else:
                    end_dt = end_time
                
                if now < start_dt:
                    dynamic_status = 'pending'
                elif now > end_dt:
                    dynamic_status = 'closed'
                else:
                    dynamic_status = 'active'
            except Exception as e:
                logger.warning(f"解析培训 {training_id} 时间失败: {e}")
                dynamic_status = 'draft'
        
        result.append({
            "id": training_id,
            "name": t.get('name', ''),
            "country": t.get('country'),
            "start_time": t.get('start_time'),
            "end_time": t.get('end_time'),
            "created_at": t.get('created_at'),
            "is_active": t.get('is_active', False),
            "photo_count": photo_counts.get(training_id, 0),
            "dynamic_status": dynamic_status,
            "status_text": _get_status_text(dynamic_status),
        })
    
    return result


def _get_status_text(status):
    """获取状态显示文本"""
    status_map = {
        'draft': '草稿',
        'pending': '未开始',
        'active': '进行中',
        'closed': '已关闭'
    }
    return status_map.get(status, status)
