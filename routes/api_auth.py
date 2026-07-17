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
from utils.manage_messages import log_user_login, log_user_logout
from services.privacy import PrivacyService

logger = logging.getLogger(__name__)

@auth_bp.route('/health')
def health_check():
    return "OK", 200

@auth_bp.route('/register')
def register():
    return render_template('auth/register_standalone.html')

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
    
    # 判断是否为 AJAX 请求
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
    
    if request.method == 'POST':
        action = request.form.get('action')
        
        if action == 'update_info':
            # 更新基本信息
            birthday = request.form.get('birthday', '')
            update_data = {
                'name_en': request.form.get('name_en', ''),
                'company': request.form.get('company', ''),
                'department': request.form.get('department', ''),
                'employee_id': request.form.get('employee_id', ''),
                'phone': request.form.get('phone', ''),
                'birthday': birthday if birthday else None
            }
            
            try:
                db.table('users').update(update_data).eq('id', user_id).execute()
                
                if is_ajax:
                    return jsonify({'success': True, 'message': '个人信息已更新'})
                flash({'msg': 'profile_updated', 'params': []}, 'success')
                return redirect(url_for('auth.profile'))
            except Exception as e:
                if is_ajax:
                    return jsonify({'success': False, 'message': str(e)}), 500
                flash({'msg': 'update_failed', 'params': []}, 'danger')
                return redirect(url_for('auth.profile'))

        elif action == 'change_password':
            old_pwd = request.form.get('old_password')
            new_pwd = request.form.get('new_password')
            confirm_pwd = request.form.get('confirm_password')
            
            # 验证
            if new_pwd != confirm_pwd:
                if is_ajax:
                    return jsonify({'success': False, 'message': '两次输入的新密码不一致'}), 400
                flash({'msg': 'password_mismatch', 'params': []}, 'danger')
                return redirect(url_for('auth.profile'))
            
            if len(new_pwd) < 6:
                if is_ajax:
                    return jsonify({'success': False, 'message': '密码长度至少6位'}), 400
                flash({'msg': 'password_too_short', 'params': []}, 'danger')
                return redirect(url_for('auth.profile'))
            
            # 验证原密码
            user_res = db.table('users').select('password_hash').eq('id', user_id).execute()
            if not user_res.data or not auth.check_password(old_pwd, user_res.data[0]['password_hash']):
                if is_ajax:
                    return jsonify({'success': False, 'message': '原密码错误'}), 400
                flash({'msg': 'wrong_password', 'params': []}, 'danger')
                return redirect(url_for('auth.profile'))
            
            # 更新密码
            new_hash = auth.hash_password(new_pwd)
            db.table('users').update({'password_hash': new_hash}).eq('id', user_id).execute()
            
            if is_ajax:
                return jsonify({'success': True, 'message': '密码修改成功，请重新登录'})
            flash({'msg': 'password_changed', 'params': []}, 'success')
            session.clear()
            return redirect(url_for('auth.login'))
        
        return redirect(url_for('auth.profile'))

    # GET 请求 - 渲染页面
    user_res = db.table('users').select('*').eq('id', user_id).single().execute()
    if not user_res.data:
        flash("用户不存在", "danger")
        return redirect(url_for('dashboard'))
    
    user = user_res.data
    
    # ========== 处理国家显示（中英文） ==========
    if user.get('country'):
        try:
            c_res = db.table("countries").select("name_zh, name_en").eq("code", user['country']).maybe_single().execute()
            if c_res and c_res.data:
                user['country_display_zh'] = c_res.data.get('name_zh')
                user['country_display_en'] = c_res.data.get('name_en')
                user['country_display'] = user['country_display_zh']
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
    
    # ========== 处理权限范围显示（中英文） ==========
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
    """发送邮箱验证码（支持注册和重置密码）"""
    email = request.json.get('email')
    purpose = request.json.get('purpose', 'register')  # 'register' 或 'reset'
    
    if not email:
        return jsonify({"success": False, "message": "缺少邮箱"}), 400
    
    if not auth._is_valid_email(email):
        return jsonify({"success": False, "message": "邮箱格式无效"}), 400
    
    # 如果是重置密码场景，验证邮箱是否已注册
    if purpose == 'reset':
        db = get_supabase()
        user_res = db.table("users").select("id").eq("email", email).is_("deleted_at", "null").execute()
        if not user_res.data:
            return jsonify({
                "success": False, 
                "message": "email_not_registered",  # 返回翻译键
                "params": []
            }), 400
    
    # 如果是注册场景，验证邮箱是否已被注册（防止重复注册）
    if purpose == 'register':
        db = get_supabase()
        user_res = db.table("users").select("id").eq("email", email).is_("deleted_at", "null").execute()
        if user_res.data:
            return jsonify({
                "success": False, 
                "message": "email_already_registered",
                "params": []
            }), 400
    
    try:
        auth.send_otp(email)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

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
        .is_("deleted_at", "null")

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
        "admin_countries": target.get('admin_countries', '')
    })
    return jsonify({
        "success": True, 
        "redirect": url_for('auth.index'),
        "message": "register_success"  # 添加成功消息键
    })


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
                "user_id": user['id'], 
                "user_email": email, 
                "role": user.get('role', 'user'),
                "admin_countries": admin_countries, 
                "is_protected": user.get('is_protected', False),
                "user_country": user.get('country')
            })
            
            # 记录登录消息
            try:
                log_user_login(
                    user_id=user['id'],
                    user_name=user.get('name_en') or user.get('name_cn', ''),
                    email=user.get('email', ''),
                    ip=request.remote_addr,
                    user_agent=request.headers.get('User-Agent')
                )
            except Exception as e:
                logger.warning(f"记录登录消息失败: {e}")

            privacy_status = PrivacyService.check_user_needs_acknowledgment(user['id'])
            
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return jsonify({
                    "success": True, 
                    "redirect": url_for('auth.index'),
                    "needs_privacy": privacy_status["needs_acknowledgment"]
                })

            flash({'msg': 'login_success', 'params': []}, 'success')
            return redirect(url_for('auth.index'))
        else:
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return jsonify({"success": False, "message": "invalid_email_or_password"}), 401
            
            flash({'msg': 'invalid_email_or_password', 'params': []}, 'danger')
            return render_template('auth/login_standalone.html')

    # GET 请求也使用独立模板
    return render_template('auth/login_standalone.html')

@auth_bp.route('/logout')
def logout():
    user_id = session.get('user_id')
    db = get_supabase()
    if user_id:
        try:
            user_res = db.table("users").select("name_en, email").eq("id", user_id).maybe_single().execute()
            if user_res.data:
                log_user_logout(
                    user_id=user_id,
                    user_name=user_res.data.get('name_en', ''),
                    email=user_res.data.get('email', '')
                )
        except Exception as e:
            logger.warning(f"记录登出消息失败: {e}")
    session.clear()
    flash({'msg': 'logout_success', 'params': []}, 'info')
    return redirect(url_for('auth.login'))

@auth_bp.route('/')
def index():
    if 'user_id' in session: return render_template('index.html')
    return redirect(url_for('auth.login'))