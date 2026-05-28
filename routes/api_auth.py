# routes/api_auth.py
import os
import json
import logging
from datetime import datetime, timezone
from flask import request, jsonify, redirect, url_for, render_template, session, flash
from . import auth_bp
from services.db import get_supabase
from services import auth
from services.auth import hash_password
from routes.helpers import login_required

logger = logging.getLogger(__name__)

@auth_bp.route('/health')
def health_check():
    return "OK", 200

@auth_bp.route('/register')
def register():
    return render_template('auth/register.html')

@auth_bp.route('/api/check-name')
def check_name():
    q = request.args.get('q', '').strip()
    if len(q) < 2: return jsonify([])
    db = get_supabase()
    res = db.table("users").select("name_en").eq("user_status", "imported").ilike("name_en", f"%{q}%").limit(10).execute()
    names = list(dict.fromkeys(r['name_en'] for r in (res.data or []) if r.get('name_en')))
    return jsonify(names)

@auth_bp.route('/api/countries')
def api_countries():
    db = get_supabase()
    res = db.table("countries").select("code, name_zh, name_en").execute()
    return jsonify(res.data)

@auth_bp.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    db = get_supabase()
    user_id = session['user_id']
    
    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'update_info':
            # 更新基本信息
            update_data = {
                'birthday': request.form.get('birthday', ''),
                'name_en': request.form.get('name_en', ''),
                'company': request.form.get('company', ''),
                'department': request.form.get('department', ''),
                'employee_id': request.form.get('employee_id', ''),
                'country': request.form.get('country', ''),
                'phone': request.form.get('phone', ''),
                'birthday': request.form.get('birthday', '')  or None
            }
            db.table('users').update(update_data).eq('id', user_id).execute()
            #flash('个人信息已更新', 'success')
            flash({'msg': 'profile_updated', 'params': []}, 'success')

        elif action == 'change_password':
            old_pwd = request.form.get('old_password')
            new_pwd = request.form.get('new_password')
            confirm_pwd = request.form.get('confirm_password')
            
            if new_pwd != confirm_pwd:
                #flash('两次输入的新密码不一致', 'danger')
                flash({'msg': 'password_mismatch', 'params': []}, 'danger')
                return redirect(url_for('profile'))
            if len(new_pwd) < 6:
                #flash('密码长度至少6位', 'danger')
                flash({'msg': 'password_too_short', 'params': []}, 'danger')
                return redirect(url_for('profile'))
            
            # 验证原密码
            user_res = db.table('users').select('password_hash').eq('id', user_id).execute()
            if not user_res.data or not auth.check_password(old_pwd, user_res.data[0]['password_hash']):
                #flash('原密码错误', 'danger')
                flash({'msg': 'wrong_password', 'params': []}, 'danger')
                return redirect(url_for('profile'))
            
            # 更新密码
            new_hash = auth.hash_password(new_pwd)
            db.table('users').update({'password_hash': new_hash}).eq('id', user_id).execute()
            #flash('密码修改成功，请重新登录', 'success')
            flash({'msg': 'password_changed', 'params': []}, 'success')
            session.clear()
            return redirect(url_for('login'))
        
        return redirect(url_for('profile'))

    if request.method == 'GET':
        user_res = db.table('users').select('*').eq('id', user_id).single().execute()
        user = user_res.data
        
        if user:
            # ========== 处理国家显示（中英文） ==========
            if user.get('country'):
                try:
                    c_res = db.table("countries").select("name_zh, name_en").eq("code", user['country']).maybe_single().execute()
                    if c_res and c_res.data:
                        user['country_display_zh'] = c_res.data.get('name_zh')
                        user['country_display_en'] = c_res.data.get('name_en')
                        user['country_display'] = user['country_display_zh']  # 默认中文
                    else:
                        user['country_display_zh'] = user['country']
                        user['country_display_en'] = user['country']
                        user['country_display'] = user['country']
                except Exception as e:
                    logger.warning(f"获取国家名称失败: {e}")
                    user['country_display_zh'] = user['country']
                    user['country_display_en'] = user['country']
                    user['country_display'] = user['country']
            else:
                user['country_display_zh'] = '未设置'
                user['country_display_en'] = 'Not Set'
                user['country_display'] = '未设置'
        
        # 处理权限范围显示（中英文）
        admin_countries = user.get('admin_countries')
        if admin_countries:
            try:
                if isinstance(admin_countries, str):
                    country_codes = json.loads(admin_countries)
                else:
                    country_codes = admin_countries
                
                if country_codes and len(country_codes) > 0:
                    # 获取国家名称映射
                    countries_res = db.table("countries").select("code, name_zh, name_en").execute()
                    country_map = {c['code']: c for c in (countries_res.data or [])}
                    
                    names_zh = []
                    names_en = []
                    for code in country_codes:
                        if code in country_map:
                            names_zh.append(country_map[code].get('name_zh', code))
                            names_en.append(country_map[code].get('name_en', code))
                        else:
                            names_zh.append(code)
                            names_en.append(code)
                    
                    user['admin_countries_display_zh'] = ', '.join(names_zh) if names_zh else '无限制'
                    user['admin_countries_display_en'] = ', '.join(names_en) if names_en else 'Unrestricted'
                    user['admin_countries_display'] = user['admin_countries_display_zh']
                else:
                    user['admin_countries_display_zh'] = '无限制'
                    user['admin_countries_display_en'] = 'Unrestricted'
                    user['admin_countries_display'] = '无限制'
            except:
                user['admin_countries_display_zh'] = '无限制'
                user['admin_countries_display_en'] = 'Unrestricted'
                user['admin_countries_display'] = '无限制'
        else:
            user['admin_countries_display_zh'] = '无限制'
            user['admin_countries_display_en'] = 'Unrestricted'
            user['admin_countries_display'] = '无限制'

    return render_template('auth/profile.html', user=user)

@auth_bp.route('/api/send-otp', methods=['POST'])
def api_send_otp():
    email = request.json.get('email')
    if not email: return jsonify({"success": False, "message": "缺少邮箱"}), 400
    try: auth.send_otp(email); return jsonify({"success": True})
    except Exception as e: return jsonify({"success": False, "message": str(e)}), 500

@auth_bp.route('/api/register', methods=['POST'])
def api_register():
    """用户注册"""
    d = request.json
    email = d.get('email', '').strip().lower()
    name_en = d.get('name_en', '').strip()
    password = d.get('password', '')
    birthday = d.get('birthday', '')               # 格式 YYYY-MM-DD
    is_partner_val = d.get('is_partner', 'N')
    otp = d.get('otp', '')
    
    # 1. 邮箱全局唯一性检查
    db = get_supabase()
    if db.table("users").select("id").eq("email", email).execute().data:
        # 邮箱已注册
        # return jsonify({"success": False, "message": "该邮箱已注册，请直接登录或更换邮箱"}), 400
        return jsonify({"success": False, "message": "email_already_registered", "params": []}), 400
    
    country = d.get('country', '').strip().upper()
    if not country:
        return jsonify({"success": False, "message": "jsonify_country_required", "params": []}), 400

    # 2. 姓名 + 出生日期精确匹配 imported 用户（且尚未设置邮箱）
    base_query = db.table("users").select("*") \
        .eq("name_en", name_en) \
        .eq("user_status", "imported") \
        .is_("email", "null") \
        .is_("deleted_at", "null")   # ✅ 防止匹配到已删除用户

    pool = base_query.execute()
    users = pool.data or []

    # 在 Python 中过滤生日条件
    if birthday:
        # 提供生日：要求导入记录的生日为空或者与提供的生日一致
        matched_users = [u for u in users if u.get('birthday') is None or u.get('birthday') == birthday]
    else:
        # 未提供生日：要求导入记录的生日为空
        matched_users = [u for u in users if u.get('birthday') is None]
    
    count = len(matched_users)
    target = matched_users[0] if matched_users else None

    if count == 0:
        if birthday:
            # 姓名与生日不匹配
            #return jsonify({"success": False, "message": "姓名与出生日期不匹配，请核对或联系管理员"}), 403
            return jsonify({"success": False, "message": "name_birthday_mismatch", "params": []}), 403
        else:
            # 姓名未匹配
            #return jsonify({"success": False, "message": "姓名未匹配到预授权名单，请联系管理员"}), 403
            return jsonify({"success": False, "message": "name_not_matched", "params": []}), 403

    if count > 1:
        # 多条匹配
        #return jsonify({"success": False, "message": "该姓名和出生日期对应多条预授权记录，请通知管理员修正数据"}), 403
        return jsonify({"success": False, "message": "multiple_imported_records", "params": []}), 403

    # 3. 姓名匹配通过后，再验证 OTP
    if not auth.verify_otp(email, otp):
        # 验证码无效或已过期
        # return jsonify({"success": False, "message": "验证码无效或已过期"})
        return jsonify({"success": False, "message": "otp_invalid", "params": []})

    # 4. 更新记录，完成注册
    update_fields = {
        "email": email,
        "password_hash": auth.hash_password(password),
        "user_status": "registered",
        "is_active": True,
        "is_partner": True if is_partner_val.upper() == 'Y' else False,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "country": country
    }
    # 如果前端传了出生日期，可更新（确保一致），但预授权已有生日则不必改
    if birthday:
        update_fields["birthday"] = birthday

    db.table("users").update(update_fields).eq("id", target['id']).execute()

    # 5. 自动登录
    session.update({
        "user_id": target['id'],
        "user_email": email,
        "role": target.get('role', 'user'),
        "admin_countries": target.get('admin_countries', '')  # ✅ 新增
    })
    return jsonify({"success": True, "redirect": url_for('index')})

@auth_bp.route('/api/reset-password', methods=['POST'])
def api_reset_password():
    """密码重置，用户自身重置"""
    d = request.json
    if not auth.verify_otp(d.get('email'), d.get('otp')):
        return jsonify({"success": False, "message": "otp_invalid", "params": []})
    db = get_supabase()
    db.table("users").update({
        "password_hash": auth.hash_password(d['password'])
    }).eq("email", d['email']).execute()
    return jsonify({"success": True})

@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email, pwd = request.form['email'], request.form['password']
        db = get_supabase()
        user = None
        try:
            res = db.table("users").select("*").eq("email", email).is_("deleted_at", "null").maybe_single().execute()
            if res and hasattr(res, 'data'): user = res.data
            elif isinstance(res, dict): user = res
        except: pass
        if user and auth.check_password(pwd, user.get('password_hash', '')):
            admin_countries = user.get('admin_countries', '')
            try:
                if isinstance(admin_countries, str): json.loads(admin_countries)
            except: admin_countries = json.dumps([])
            session.update({
                "user_id": user['id'], "user_email": email, "role": user.get('role', 'user'),
                "admin_countries": admin_countries, "is_protected": user.get('is_protected', False),
                "user_country": user.get('country')
            })
            flash({'msg': 'login_success', 'params': []}, 'success')
            return redirect(url_for('exam.dashboard'))
        else:
            flash({'msg': 'invalid_email_or_password', 'params': []}, 'danger')
    return render_template('auth/login.html')

@auth_bp.route('/logout')
def logout():
    session.clear()
    flash({'msg': 'logout_success', 'params': []}, 'info')
    return redirect(url_for('auth.login'))

@auth_bp.route('/')
def index():
    if 'user_id' in session: return render_template('index.html')
    return redirect(url_for('auth.login'))