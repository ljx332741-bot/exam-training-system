# routes/admin_training_photos.py
"""
培训照片管理 API
- 管理员端：上传、删除、重命名、设为封面、批量删除
- 学员端：查看、上传（仅自己国家）
"""

import json
import logging
import uuid
import io
from datetime import datetime, timezone
from io import BytesIO

from flask import (
    render_template, session, Blueprint, request, jsonify, current_app, send_file
)
from PIL import Image, ImageDraw, ImageFont
from services.cloudflare_r2 import upload_to_r2, delete_from_r2, delete_multiple_from_r2
from services.db import get_supabase, get_supabase_admin
from routes.helpers import login_required, admin_required
from utils.permissions import (
    get_admin_allowed_countries, is_developer, filter_users_by_permission
)

logger = logging.getLogger(__name__)

# 创建蓝图
admin_training_photos_bp = Blueprint('admin_training_photos', __name__)


# ============================================================
# 辅助函数
# ============================================================

def add_watermark_to_image(image_data, training_name, include_training_name=True):
    """
    为图片添加水印（左下角）
    
    Args:
        image_data: 图片二进制数据
        training_name: 培训名称
        include_training_name: 是否包含培训名称
    
    Returns:
        添加水印后的图片二进制数据
    """
    try:
        # 打开图片
        img = Image.open(io.BytesIO(image_data))
        
        # 转换为 RGB（支持 PNG 透明背景）
        if img.mode in ('RGBA', 'LA', 'P'):
            background = Image.new('RGB', img.size, (255, 255, 255))
            if img.mode == 'P':
                img = img.convert('RGBA')
            background.paste(img, mask=img.split()[-1] if img.mode == 'RGBA' else None)
            img = background
        elif img.mode != 'RGB':
            img = img.convert('RGB')
        
        draw = ImageDraw.Draw(img)
        
        # 获取当前时间
        now = datetime.now()
        date_str = now.strftime('%Y-%m-%d %H:%M')
        
        # 构建水印文本
        watermark_text = date_str
        if include_training_name and training_name:
            watermark_text = f"{training_name} | {date_str}"
        
        # 根据图片大小动态调整字体大小
        base_size = min(img.width, img.height)
        font_size = max(int(base_size * 0.025), 12)
        
        # 尝试加载中文字体
        try:
            font = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', font_size)
        except:
            try:
                font = ImageFont.truetype('/System/Library/Fonts/PingFang.ttc', font_size)
            except:
                font = ImageFont.load_default()
        
        # 计算文本位置（左下角，留边距）
        bbox = draw.textbbox((0, 0), watermark_text, font=font)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]
        padding = int(base_size * 0.02) + 5
        x = padding
        y = img.height - text_height - padding
        
        # 绘制半透明背景框（提高可读性）
        bg_padding = 6
        draw.rectangle(
            [x - bg_padding, y - bg_padding, x + text_width + bg_padding, y + text_height + bg_padding],
            fill=(0, 0, 0, 128)
        )
        
        # 绘制水印文字（白色，带阴影）
        draw.text(
            (x + 1, y + 1),
            watermark_text,
            font=font,
            fill=(0, 0, 0, 180)  # 黑色阴影
        )
        draw.text(
            (x, y),
            watermark_text,
            font=font,
            fill=(255, 255, 255, 230)  # 白色文字
        )
        
        # 保存为 JPEG
        output = io.BytesIO()
        img.save(output, format='JPEG', quality=92)
        return output.getvalue()
        
    except Exception as e:
        logger.error(f"添加水印失败: {e}")
        # 失败时返回原始图片
        return image_data


def validate_photo_permission(photo, current_user_id, current_role):
    """
    验证用户是否有权限操作该照片
    
    Returns:
        (can_edit, can_delete, can_view)
    """
    is_dev = is_developer()
    uploaded_by = photo.get('uploaded_by')
    
    # 开发者：全部权限
    if is_dev:
        return True, True, True
    
    # 超管：全部权限
    if current_role == 'super_admin':
        return True, True, True
    
    # 管理员：可以编辑/删除所有照片（但受国家权限限制）
    if current_role == 'admin':
        allowed_countries = get_admin_allowed_countries()
        training_country = photo.get('training_country')
        if allowed_countries is not None and training_country not in allowed_countries:
            return False, False, True  # 可查看但不可操作
        return True, True, True
    
    # 普通用户：只能操作自己上传的
    if current_role == 'user':
        can_edit = uploaded_by == current_user_id
        can_delete = uploaded_by == current_user_id
        return can_edit, can_delete, True
    
    return False, False, True


def get_user_country(user_id):
    """获取用户国家"""
    db = get_supabase()
    res = db.table("users").select("country, role").eq("id", user_id).maybe_single().execute()
    if res.data:
        return res.data.get('country'), res.data.get('role')
    return None, None


# ============================================================
# 1. 管理员端 API
# ============================================================

@admin_training_photos_bp.route('/api/admin/training/photos', methods=['GET'])
@login_required
@admin_required
def api_admin_get_training_photos():
    """
    获取培训照片列表（管理员端）
    
    参数:
        training_id: 培训ID（可选）
        exam_id: 考试ID（可选）
        country: 国家（可选）
        page: 页码
        per_page: 每页数量
        sort: 排序字段
        order: 排序方向
    """
    db = get_supabase_admin()
    allowed_countries = get_admin_allowed_countries()
    is_dev = is_developer()
    current_user_id = session.get('user_id')
    current_role = session.get('role')
    
    # 获取参数
    training_id = request.args.get('training_id')
    exam_id = request.args.get('exam_id')
    country = request.args.get('country')
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 20, type=int)
    sort = request.args.get('sort', 'uploaded_at')
    order = request.args.get('order', 'desc')
    
    # 基础查询
    query = db.table("training_photos").select("*", count="exact").eq("is_deleted", False)
    
    # 筛选条件
    if training_id:
        query = query.eq("training_id", training_id)
    if exam_id:
        query = query.eq("exam_id", exam_id)
    if country:
        query = query.eq("training_country", country)
    
    # 权限过滤
    if not is_dev:
        if allowed_countries is not None and allowed_countries:
            query = query.in_("training_country", allowed_countries)
        elif current_role == 'admin':
            # 管理员只能看自己国家的照片
            user_country = session.get('user_country')
            if user_country:
                query = query.eq("training_country", user_country)
    
    # 排序
    order_direction = "desc" if order == "desc" else "asc"
    query = query.order(sort, desc=(order_direction == "desc"))
    
    # 分页
    start = (page - 1) * per_page
    end = start + per_page - 1
    query = query.range(start, end)
    
    try:
        result = query.execute()
        photos = result.data or []
        total = result.count or 0
        
        # 获取上传人姓名
        uploader_ids = [p.get('uploaded_by') for p in photos if p.get('uploaded_by')]
        if uploader_ids:
            user_res = db.table("users").select("id, name_en").in_("id", uploader_ids).execute()
            user_map = {u['id']: u.get('name_en', '') for u in (user_res.data or [])}
            for p in photos:
                p['uploaded_by_name'] = user_map.get(p.get('uploaded_by'), '')
        
        return jsonify({
            "success": True,
            "data": photos,
            "total": total,
            "page": page,
            "per_page": per_page
        })
    except Exception as e:
        logger.error(f"获取照片列表失败: {e}")
        return jsonify({"success": False, "message": str(e)}), 500


@admin_training_photos_bp.route('/api/admin/training/photos', methods=['POST'])
@login_required
@admin_required
def api_admin_upload_training_photos():
    """
    上传培训照片（管理员端）
    
    表单参数:
        training_id: 培训ID（必填）
        training_name: 培训名称（必填）
        training_country: 培训国家（必填）
        exam_id: 考试ID（可选）
        exam_name: 考试名称（可选）
        photos: 照片文件（多张）
        descriptions: 描述（JSON数组）
    """
    db = get_supabase_admin()
    current_user_id = session.get('user_id')
    is_dev = is_developer()
    allowed_countries = get_admin_allowed_countries()
    
    # 获取表单参数
    training_id = request.form.get('training_id')
    training_name = request.form.get('training_name')
    training_country = request.form.get('training_country')
    exam_id = request.form.get('exam_id')
    exam_name = request.form.get('exam_name')
    descriptions_json = request.form.get('descriptions', '[]')
    
    # 参数验证
    if not training_id:
        return jsonify({"success": False, "message": "培训ID不能为空"}), 400
    if not training_name:
        return jsonify({"success": False, "message": "培训名称不能为空"}), 400
    if not training_country:
        return jsonify({"success": False, "message": "培训国家不能为空"}), 400
    
    # 权限检查：培训国家是否在权限范围内
    if not is_dev and allowed_countries is not None:
        if training_country not in allowed_countries:
            return jsonify({
                "success": False,
                "message": f"无权操作国家 {training_country} 的培训照片"
            }), 403
    
    # 解析描述
    try:
        descriptions = json.loads(descriptions_json)
    except:
        descriptions = []
    
    # 检查文件
    files = request.files.getlist('photos')
    if not files or len(files) == 0:
        return jsonify({"success": False, "message": "请选择要上传的照片"}), 400
    
    if len(files) > 20:
        return jsonify({"success": False, "message": "单次最多上传20张照片"}), 400
    
    # 验证培训是否存在
    training_res = db.table("trainings").select("id, name, country").eq("id", int(training_id)).maybe_single().execute()
    if not training_res.data:
        return jsonify({"success": False, "message": "培训不存在"}), 404
    
    # 上传照片
    uploaded_photos = []
    errors = []
    now = datetime.now(timezone.utc).isoformat()
    
    for idx, file in enumerate(files):
        if file.filename == '':
            continue
        
        # 检查文件大小（最大 10MB）
        file.seek(0, 2)
        file_size = file.tell()
        file.seek(0)
        if file_size > 10 * 1024 * 1024:
            errors.append(f"{file.filename}: 文件大小超过 10MB")
            continue
        
        # 检查文件类型
        allowed_types = ['image/jpeg', 'image/png', 'image/gif', 'image/webp', 'image/heic']
        if file.content_type not in allowed_types:
            errors.append(f"{file.filename}: 不支持的图片格式")
            continue
        
        try:
            # 生成唯一文件名
            ext = file.filename.rsplit('.', 1)[-1] if '.' in file.filename else 'jpg'
            unique_id = uuid.uuid4().hex[:12]
            file_key = f"training_{training_id}/{unique_id}.{ext}"
            
            # 获取描述
            description = descriptions[idx] if idx < len(descriptions) else ''
            
            # 上传到 R2
            public_url, photo_path = upload_to_r2(
                file_obj=file,
                training_id=training_id,
                filename=file.filename,
                content_type=file.content_type
            )
            
            # 插入数据库
            insert_data = {
                "training_id": int(training_id),
                "training_name": training_name,
                "training_country": training_country,
                "exam_id": int(exam_id) if exam_id else None,
                "exam_name": exam_name,
                "photo_url": public_url,
                "photo_path": photo_path,
                "file_name": file.filename,
                "file_size": file_size,
                "file_type": file.content_type,
                "photo_description": description,
                "is_cover": False,  # 默认不是封面
                "uploaded_at": now,
                "uploaded_by": current_user_id,
                "metadata": {
                    "upload_source": "admin",
                    "original_filename": file.filename
                }
            }
            
            result = db.table("training_photos").insert(insert_data).execute()
            if result.data:
                photo_data = result.data[0]
                photo_data['uploaded_by_name'] = session.get('name_en', '')
                uploaded_photos.append(photo_data)
            else:
                errors.append(f"{file.filename}: 保存记录失败")
                
        except Exception as e:
            logger.error(f"上传照片失败 {file.filename}: {e}")
            errors.append(f"{file.filename}: {str(e)}")
    
    return jsonify({
        "success": True,
        "uploaded_count": len(uploaded_photos),
        "error_count": len(errors),
        "photos": uploaded_photos,
        "errors": errors
    })


@admin_training_photos_bp.route('/api/admin/training/photos/<int:photo_id>', methods=['DELETE'])
@login_required
@admin_required
def api_admin_delete_training_photo(photo_id):
    """
    删除照片（软删除 + 从 R2 删除）
    """
    db = get_supabase_admin()
    current_user_id = session.get('user_id')
    
    # 获取照片信息
    photo_res = db.table("training_photos").select("*").eq("id", photo_id).eq("is_deleted", False).maybe_single().execute()
    if not photo_res.data:
        return jsonify({"success": False, "message": "照片不存在或已删除"}), 404
    
    photo = photo_res.data
    training_country = photo.get('training_country')
    
    # 权限检查
    if not _can_manage_photo(photo):
        return jsonify({"success": False, "message": "无权删除此照片"}), 403
    
    try:
        # 1. 从 R2 删除文件
        photo_path = photo.get('photo_path')
        if photo_path:
            delete_from_r2(photo_path)
        
        # 2. 软删除数据库记录
        now = datetime.now(timezone.utc).isoformat()
        db.table("training_photos").update({
            "is_deleted": True,
            "deleted_at": now,
            "deleted_by": current_user_id
        }).eq("id", photo_id).execute()
        
        return jsonify({"success": True, "message": "照片已删除"})
    except Exception as e:
        logger.error(f"删除照片失败: {e}")
        return jsonify({"success": False, "message": str(e)}), 500


@admin_training_photos_bp.route('/api/admin/training/photos/<int:photo_id>', methods=['PUT'])
@login_required
@admin_required
def api_admin_update_training_photo(photo_id):
    """
    更新照片信息（重命名、更新描述）
    """
    db = get_supabase_admin()
    current_user_id = session.get('user_id')
    data = request.json
    
    # 获取照片信息
    photo_res = db.table("training_photos").select("*").eq("id", photo_id).eq("is_deleted", False).maybe_single().execute()
    if not photo_res.data:
        return jsonify({"success": False, "message": "照片不存在"}), 404
    
    photo = photo_res.data
    
    # 权限检查
    if not _can_manage_photo(photo):
        return jsonify({"success": False, "message": "无权修改此照片"}), 403
    
    # 构建更新数据
    update_data = {}
    if 'file_name' in data:
        update_data['file_name'] = data['file_name']
    if 'photo_description' in data:
        update_data['photo_description'] = data['photo_description']
    
    if not update_data:
        return jsonify({"success": False, "message": "没有要更新的字段"}), 400
    
    update_data['updated_at'] = datetime.now(timezone.utc).isoformat()
    update_data['updated_by'] = current_user_id
    
    try:
        result = db.table("training_photos").update(update_data).eq("id", photo_id).execute()
        return jsonify({"success": True, "data": result.data[0] if result.data else None})
    except Exception as e:
        logger.error(f"更新照片失败: {e}")
        return jsonify({"success": False, "message": str(e)}), 500


@admin_training_photos_bp.route('/api/admin/training/photos/<int:photo_id>/cover', methods=['PUT'])
@login_required
@admin_required
def api_admin_set_training_cover(photo_id):
    """
    设置照片为培训封面
    """
    db = get_supabase_admin()
    current_user_id = session.get('user_id')
    
    # 获取照片信息
    photo_res = db.table("training_photos").select("*").eq("id", photo_id).eq("is_deleted", False).maybe_single().execute()
    if not photo_res.data:
        return jsonify({"success": False, "message": "照片不存在"}), 404
    
    photo = photo_res.data
    training_id = photo.get('training_id')
    
    # 权限检查
    if not _can_manage_photo(photo):
        return jsonify({"success": False, "message": "无权操作此照片"}), 403
    
    try:
        # 1. 清除该培训的现有封面
        db.table("training_photos").update({"is_cover": False}).eq("training_id", training_id).eq("is_deleted", False).execute()
        
        # 2. 设置新封面
        now = datetime.now(timezone.utc).isoformat()
        db.table("training_photos").update({
            "is_cover": True,
            "updated_at": now,
            "updated_by": current_user_id
        }).eq("id", photo_id).execute()
        
        return jsonify({"success": True, "message": "已设为封面"})
    except Exception as e:
        logger.error(f"设置封面失败: {e}")
        return jsonify({"success": False, "message": str(e)}), 500


@admin_training_photos_bp.route('/api/admin/training/photos/batch_delete', methods=['POST'])
@login_required
@admin_required
def api_admin_batch_delete_training_photos():
    """
    批量删除照片
    """
    db = get_supabase_admin()
    current_user_id = session.get('user_id')
    data = request.json
    photo_ids = data.get('ids', [])
    
    if not photo_ids:
        return jsonify({"success": False, "message": "请选择要删除的照片"}), 400
    
    success_count = 0
    fail_count = 0
    errors = []
    
    for photo_id in photo_ids:
        try:
            # 获取照片信息
            photo_res = db.table("training_photos").select("*").eq("id", photo_id).eq("is_deleted", False).maybe_single().execute()
            if not photo_res.data:
                fail_count += 1
                errors.append(f"照片 {photo_id} 不存在")
                continue
            
            photo = photo_res.data
            
            # 权限检查
            if not _can_manage_photo(photo):
                fail_count += 1
                errors.append(f"照片 {photo_id}: 无权限")
                continue
            
            # 从 R2 删除
            photo_path = photo.get('photo_path')
            if photo_path:
                delete_from_r2(photo_path)
            
            # 软删除数据库记录
            now = datetime.now(timezone.utc).isoformat()
            db.table("training_photos").update({
                "is_deleted": True,
                "deleted_at": now,
                "deleted_by": current_user_id
            }).eq("id", photo_id).execute()
            
            success_count += 1
        except Exception as e:
            fail_count += 1
            errors.append(f"照片 {photo_id}: {str(e)}")
    
    return jsonify({
        "success": True,
        "success_count": success_count,
        "fail_count": fail_count,
        "errors": errors[:10]
    })


# ============================================================
# 2. 学员端 API
# ============================================================
@admin_training_photos_bp.route('/api/training/photos', methods=['GET'])
@login_required
def api_training_get_photos():
    """
    获取学员可见的培训照片
    - 普通用户：仅自己国家的培训照片
    - 管理员：权限范围内的所有照片
    """
    print("=" * 60)
    print("📷 开始处理照片列表请求")
    print(f"当前用户ID: {session.get('user_id')}")
    print(f"当前用户角色: {session.get('role')}")
    
    # ✅ 关键修复：使用管理员客户端绕过 RLS
    db = get_supabase_admin()  # 改为 admin 客户端
    current_user_id = session.get('user_id')
    current_role = session.get('role')
    
    # 获取用户信息
    user_res = db.table("users").select("country, role").eq("id", current_user_id).maybe_single().execute()
    if not user_res.data:
        return jsonify({"success": False, "message": "用户不存在"}), 404
    
    user_country = user_res.data.get('country')
    user_role = user_res.data.get('role')
    
    # 获取参数
    training_id = request.args.get('training_id')
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 20, type=int)
    only_mine = request.args.get('only_mine', 'false').lower() == 'true'
    
    # 判断是否管理员及以上
    is_admin = user_role in ['admin', 'super_admin', 'developer']
    all_photos_res = db.table("training_photos").select("*").eq("is_deleted", False).execute()
    all_photos = all_photos_res.data or []
    
    for p in all_photos[:5]:
        print(f"  - id={p.get('id')}, training_id={p.get('training_id')}, "
              f"training_country={p.get('training_country')}, file={p.get('file_name')}")
    
    user_photos_res = db.table("training_photos").select("*").eq("is_deleted", False).eq("uploaded_by", current_user_id).execute()
    user_photos = user_photos_res.data or []
    
    for p in user_photos[:5]:
        print(f"  - id={p.get('id')}, training_id={p.get('training_id')}, "
              f"training_country={p.get('training_country')}, file={p.get('file_name')}")
    
    # 基础查询
    query = db.table("training_photos").select("*", count="exact").eq("is_deleted", False)
    
    if is_admin:
        allowed_countries = get_admin_allowed_countries()
        print(f"管理员权限范围: {allowed_countries}")
        
        if allowed_countries is not None:
            if allowed_countries:
                query = query.in_("training_country", allowed_countries)
            else:
                return jsonify({"success": True, "data": [], "total": 0, "page": page, "per_page": per_page})
    else:
        if not user_country:
            return jsonify({"success": True, "data": [], "total": 0, "page": page, "per_page": per_page})
        
        query = query.eq("training_country", user_country)
    
    if only_mine:
        query = query.eq("uploaded_by", current_user_id)
    
    if training_id:
        query = query.eq("training_id", training_id)
    
    query = query.order("uploaded_at", desc=True)
    start = (page - 1) * per_page
    end = start + per_page - 1
    query = query.range(start, end)
    
    try:
        result = query.execute()
        photos = result.data or []
        total = result.count or 0
        
        for i, p in enumerate(photos[:5]):
            print(f"  结果 {i+1}: id={p.get('id')}, training_id={p.get('training_id')}, "
                  f"training_country={p.get('training_country')}, file={p.get('file_name')}")
        
        # 获取上传人信息
        uploader_ids = [p.get('uploaded_by') for p in photos if p.get('uploaded_by')]
        if uploader_ids:
            user_res = db.table("users").select("id, name_en, name_cn").in_("id", uploader_ids).execute()
            user_map = {u['id']: u.get('name_en') or u.get('name_cn', '') for u in (user_res.data or [])}
            for p in photos:
                p['uploaded_by_name'] = user_map.get(p.get('uploaded_by'), '')
                can_edit, can_delete, _ = validate_photo_permission(
                    p, current_user_id, current_role
                )
                p['can_edit'] = can_edit
                p['can_delete'] = can_delete
        
        print("=" * 60)
        return jsonify({
            "success": True,
            "data": photos,
            "total": total,
            "page": page,
            "per_page": per_page,
            "user_country": user_country,
            "user_role": user_role,
            "is_admin": is_admin
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "message": str(e)}), 500

@admin_training_photos_bp.route('/api/training/photos', methods=['POST'])
@login_required
def api_training_upload_photos():
    """
    学员端上传培训照片
    支持：图库选择 + 摄像头拍摄
    自动添加水印（默认开启）
    """
    db = get_supabase_admin()  # 使用管理员客户端绕过 RLS
    current_user_id = session.get('user_id')
    current_role = session.get('role')

    print("=" * 60)
    print("📸 开始处理照片上传请求")
    print(f"用户ID: {current_user_id}, 角色: {current_role}")
    
    # 获取用户信息
    user_res = db.table("users").select("country, name_en, name_cn").eq("id", current_user_id).maybe_single().execute()
    if not user_res.data:
        logger.error(f"用户不存在: {current_user_id}")
        return jsonify({"success": False, "message": "用户不存在"}), 404
    
    user_country = user_res.data.get('country')
    user_name = user_res.data.get('name_en') or user_res.data.get('name_cn', '')
    print(f"用户国家: {user_country}, 用户名: {user_name}")
    
    # 获取表单参数
    training_id = request.form.get('training_id')
    training_name = request.form.get('training_name')
    add_watermark = request.form.get('add_watermark', 'true').lower() == 'true'
    include_training_name = request.form.get('include_training_name', 'true').lower() == 'true'

    print(f"表单参数: training_id={training_id}, training_name={training_name}")
    print(f"水印: add_watermark={add_watermark}, include_training_name={include_training_name}")
    
    # 参数验证
    if not training_id:
        logger.warning("培训ID为空")
        return jsonify({"success": False, "message": "请选择培训"}), 400
    if not training_name:
        logger.warning("培训名称为空")
        return jsonify({"success": False, "message": "培训名称不能为空"}), 400
    
    # 验证培训是否存在且用户有权限
    training_res = db.table("trainings").select("id, name, country").eq("id", int(training_id)).maybe_single().execute()
    if not training_res.data:
        logger.error(f"培训不存在: training_id={training_id}")
        return jsonify({"success": False, "message": "培训不存在"}), 404
    
    training_country = training_res.data.get('country')
    print(f"培训国家: {training_country}")
    
    # 权限检查：普通用户只能上传自己国家的培训
    is_admin = current_role in ['admin', 'super_admin', 'developer']
    if not is_admin:
        if training_country != user_country:
            logger.warning(f"国家不匹配: training_country={training_country}, user_country={user_country}")
            return jsonify({
                "success": False,
                "message": "您只能上传自己国家培训的照片"
            }), 403
    
    # 检查该培训的照片数量
    count_res = db.table("training_photos").select("id", count="exact").eq("training_id", int(training_id)).eq("is_deleted", False).execute()
    current_count = count_res.count or 0
    MAX_PHOTOS_PER_TRAINING = 50
    print(f"培训当前照片数: {current_count}, 上限: {MAX_PHOTOS_PER_TRAINING}")
    if current_count >= MAX_PHOTOS_PER_TRAINING:
        logger.warning(f"培训照片已满: {current_count}")
        return jsonify({
            "success": False,
            "message": f"该培训已上传 {current_count} 张照片，达到上限 {MAX_PHOTOS_PER_TRAINING} 张"
        }), 400
    
    # 检查文件
    files = request.files.getlist('photos')
    print(f"接收到 {len(files)} 个文件")
    if not files or len(files) == 0:
        logger.warning("没有选择照片")
        return jsonify({"success": False, "message": "请选择要上传的照片"}), 400
    
    if len(files) > 10:
        logger.warning(f"照片数量超限: {len(files)}")
        return jsonify({"success": False, "message": "单次最多上传10张照片"}), 400
    
    # 计算剩余可上传数量
    remaining = MAX_PHOTOS_PER_TRAINING - current_count
    if len(files) > remaining:
        logger.warning(f"剩余容量不足: remaining={remaining}, files={len(files)}")
        return jsonify({
            "success": False,
            "message": f"该培训最多 {MAX_PHOTOS_PER_TRAINING} 张，剩余 {remaining} 张"
        }), 400
    
    # 上传照片
    uploaded_photos = []
    errors = []
    now = datetime.now(timezone.utc).isoformat()

    # ✅ 确定最终使用的国家（用于数据库存储）
    final_country = training_country or user_country
    print(f"最终存储的国家: {final_country}")
    
    for idx, file in enumerate(files):
        if file.filename == '':
            continue
        print(f"处理文件 {idx+1}/{len(files)}: {file.filename}")
        
        
        # 检查文件大小（最大 10MB）
        file.seek(0, 2)
        file_size = file.tell()
        file.seek(0)
        if file_size > 10 * 1024 * 1024:
            errors.append(f"{file.filename}: 文件大小超过 10MB")
            logger.warning(f"文件过大: {file.filename}, size={file_size}")
            continue
        
        # 检查文件类型
        allowed_types = ['image/jpeg', 'image/png', 'image/gif', 'image/webp']
        content_type = file.content_type or 'image/jpeg'
        if content_type not in allowed_types:
            # 尝试通过扩展名判断
            ext = file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else ''
            if ext not in ['jpg', 'jpeg', 'png', 'gif', 'webp']:
                errors.append(f"{file.filename}: 不支持的图片格式")
                logger.warning(f"不支持的格式: {file.filename}, ext={ext}")
                continue
            # 修正 content_type
            if ext in ['jpg', 'jpeg']:
                content_type = 'image/jpeg'
            elif ext == 'png':
                content_type = 'image/png'
            elif ext == 'gif':
                content_type = 'image/gif'
            elif ext == 'webp':
                content_type = 'image/webp'
        
        try:
            # 读取图片数据
            file.seek(0)
            image_data = file.read()
            
            # 添加水印（如果开启）
            if add_watermark:
                image_data = add_watermark_to_image(
                    image_data, 
                    training_name, 
                    include_training_name
                )
                print(f"已添加水印: {file.filename}")
            
            # 生成唯一文件名
            ext = file.filename.rsplit('.', 1)[-1] if '.' in file.filename else 'jpg'
            unique_id = uuid.uuid4().hex[:12]
            file_key = f"training_{training_id}/{unique_id}.{ext}"
            
            # 上传到 R2（使用 BytesIO）
            file_obj = BytesIO(image_data)
            file_obj.seek(0)
            
            public_url, photo_path = upload_to_r2(
                file_obj=file_obj,
                training_id=training_id,
                filename=file.filename,
                content_type=content_type
            )
            
            # 获取描述（从表单）
            description_key = f'descriptions[{idx}]'
            description = request.form.get(description_key, '')
            
            # 插入数据库
            insert_data = {
                "training_id": int(training_id),
                "training_name": training_name,
                "training_country": training_country or user_country,
                "photo_url": public_url,
                "photo_path": photo_path,
                "file_name": file.filename,
                "file_size": file_size,
                "file_type": content_type,
                "photo_description": description,
                "is_cover": False,
                "uploaded_at": now,
                "uploaded_by": current_user_id,
                "metadata": {
                    "upload_source": "user",
                    "original_filename": file.filename,
                    "has_watermark": add_watermark,
                    "watermark_text": f"{training_name} | {datetime.now().strftime('%Y-%m-%d %H:%M')}" if add_watermark and include_training_name else datetime.now().strftime('%Y-%m-%d %H:%M') if add_watermark else None,
                    "uploaded_by_name": user_name
                }
            }
            print(f"插入数据库: training_id={training_id}, training_country={final_country}, file_name={file.filename}")
            
            result = db.table("training_photos").insert(insert_data).execute()
            if result.data:
                photo_data = result.data[0]
                photo_data['uploaded_by_name'] = user_name
                photo_data['can_edit'] = True
                photo_data['can_delete'] = True
                uploaded_photos.append(photo_data)
                print(f"✅ 照片记录创建成功: id={photo_data['id']}")
            else:
                errors.append(f"{file.filename}: 保存记录失败")
                logger.error(f"数据库插入失败: {file.filename}")
                
        except Exception as e:
            logger.error(f"上传照片失败 {file.filename}: {e}")
            errors.append(f"{file.filename}: {str(e)}")

    print(f"上传完成: 成功 {len(uploaded_photos)} 张, 失败 {len(errors)} 张")
    print("=" * 60)
    
    return jsonify({
        "success": True,
        "uploaded_count": len(uploaded_photos),
        "error_count": len(errors),
        "photos": uploaded_photos,
        "errors": errors,
        "remaining_slots": remaining - len(uploaded_photos)
    })


@admin_training_photos_bp.route('/api/training/photos/<int:photo_id>', methods=['DELETE'])
@login_required
def api_training_delete_photo(photo_id):
    """
    删除照片（学员端）
    - 普通用户：仅能删除自己上传的
    - 管理员：可删除所有
    """
    db = get_supabase_admin()
    current_user_id = session.get('user_id')
    current_role = session.get('role')

    print("=" * 60)
    print(f"🗑️ 删除照片请求: photo_id={photo_id}")
    print(f"用户ID: {current_user_id}, 角色: {current_role}")
    
    # 获取照片信息
    photo_res = db.table("training_photos").select("*").eq("id", photo_id).eq("is_deleted", False).maybe_single().execute()
    if not photo_res.data:
        logger.warning(f"照片不存在或已删除: photo_id={photo_id}")
        return jsonify({"success": False, "message": "照片不存在或已删除"}), 404
    
    photo = photo_res.data
    print(f"照片信息: training_id={photo.get('training_id')}, training_country={photo.get('training_country')}, "
                f"uploaded_by={photo.get('uploaded_by')}, file={photo.get('file_name')}")
    
    # 权限检查
    can_edit, can_delete, _ = validate_photo_permission(photo, current_user_id, current_role)
    print(f"权限检查: can_edit={can_edit}, can_delete={can_delete}")
    
    if not can_delete:
        logger.warning(f"无权限删除: user_id={current_user_id}, uploaded_by={photo.get('uploaded_by')}")
        return jsonify({"success": False, "message": "无权删除此照片"}), 403
    
    try:
        # 1. 从 R2 删除文件
        photo_path = photo.get('photo_path')
        if photo_path:
            print(f"从 R2 删除: {photo_path}")
            delete_from_r2(photo_path)
        
        # 2. 软删除数据库记录
        now = datetime.now(timezone.utc).isoformat()
        db.table("training_photos").update({
            "is_deleted": True,
            "deleted_at": now,
            "deleted_by": current_user_id
        }).eq("id", photo_id).execute()

        print(f"✅ 照片删除成功: photo_id={photo_id}")
        print("=" * 60)
        
        return jsonify({"success": True, "message": "照片已删除"})
    except Exception as e:
        logger.error(f"删除照片失败: {e}")
        return jsonify({"success": False, "message": str(e)}), 500


# ============================================================
# 辅助函数
# ============================================================

def _can_manage_photo(photo):
    """
    检查当前用户是否有权限管理该照片
    """
    if is_developer():
        return True
    
    allowed_countries = get_admin_allowed_countries()
    training_country = photo.get('training_country')
    current_role = session.get('role')
    
    # 超管
    if current_role == 'super_admin':
        if allowed_countries is None:
            return True
        return training_country in allowed_countries
    
    # 管理员
    if current_role == 'admin':
        if allowed_countries is not None:
            return training_country in allowed_countries
        user_country = session.get('user_country')
        return training_country == user_country
    
    return False


# ============================================================
# 3. 管理员端 API（扩展）
# ============================================================

@admin_training_photos_bp.route('/api/admin/training/photos', methods=['GET'])
@login_required
@admin_required
def api_admin_get_photos():
    """
    获取照片列表（管理员端）
    支持更多筛选条件
    """
    db = get_supabase_admin()
    allowed_countries = get_admin_allowed_countries()
    is_dev = is_developer()
    current_user_id = session.get('user_id')
    current_role = session.get('role')
    
    # 获取参数
    training_id = request.args.get('training_id')
    training_name = request.args.get('training_name')
    exam_id = request.args.get('exam_id')
    country = request.args.get('country')
    uploaded_by = request.args.get('uploaded_by')
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 24, type=int)
    sort = request.args.get('sort', 'uploaded_at')
    order = request.args.get('order', 'desc')
    
    # 基础查询
    query = db.table("training_photos").select("*", count="exact").eq("is_deleted", False)
    
    # 筛选条件
    if training_id:
        query = query.eq("training_id", training_id)
    if training_name:
        query = query.ilike("training_name", f"%{training_name}%")
    if exam_id:
        query = query.eq("exam_id", exam_id)
    if country:
        query = query.eq("training_country", country)
    if uploaded_by:
        query = query.eq("uploaded_by", uploaded_by)
    
    # 权限过滤
    if not is_dev:
        if allowed_countries is not None and allowed_countries:
            query = query.in_("training_country", allowed_countries)
        elif current_role == 'admin':
            user_country = session.get('user_country')
            if user_country:
                query = query.eq("training_country", user_country)
    
    # 排序
    order_direction = "desc" if order == "desc" else "asc"
    query = query.order(sort, desc=(order_direction == "desc"))
    
    # 分页
    start = (page - 1) * per_page
    end = start + per_page - 1
    query = query.range(start, end)
    
    try:
        result = query.execute()
        photos = result.data or []
        total = result.count or 0
        
        # 获取上传人信息
        uploader_ids = [p.get('uploaded_by') for p in photos if p.get('uploaded_by')]
        if uploader_ids:
            user_res = db.table("users").select("id, name_en, name_cn").in_("id", uploader_ids).execute()
            user_map = {u['id']: u.get('name_en') or u.get('name_cn', '') for u in (user_res.data or [])}
            for p in photos:
                p['uploaded_by_name'] = user_map.get(p.get('uploaded_by'), '')
        
        return jsonify({
            "success": True,
            "data": photos,
            "total": total,
            "page": page,
            "per_page": per_page
        })
    except Exception as e:
        logger.error(f"获取照片列表失败: {e}")
        return jsonify({"success": False, "message": str(e)}), 500


@admin_training_photos_bp.route('/api/admin/training/photos/<int:photo_id>', methods=['DELETE'])
@login_required
@admin_required
def api_admin_delete_photo(photo_id):
    """管理员删除照片"""
    db = get_supabase_admin()
    current_user_id = session.get('user_id')
    
    photo_res = db.table("training_photos").select("*").eq("id", photo_id).eq("is_deleted", False).maybe_single().execute()
    if not photo_res.data:
        return jsonify({"success": False, "message": "照片不存在"}), 404
    
    photo = photo_res.data
    
    # 权限检查
    allowed_countries = get_admin_allowed_countries()
    training_country = photo.get('training_country')
    if allowed_countries is not None and training_country not in allowed_countries:
        return jsonify({"success": False, "message": "无权删除此照片"}), 403
    
    try:
        # 从 R2 删除
        photo_path = photo.get('photo_path')
        if photo_path:
            delete_from_r2(photo_path)
        
        # 软删除
        now = datetime.now(timezone.utc).isoformat()
        db.table("training_photos").update({
            "is_deleted": True,
            "deleted_at": now,
            "deleted_by": current_user_id
        }).eq("id", photo_id).execute()
        
        return jsonify({"success": True, "message": "照片已删除"})
    except Exception as e:
        logger.error(f"删除照片失败: {e}")
        return jsonify({"success": False, "message": str(e)}), 500


@admin_training_photos_bp.route('/api/admin/training/photos/<int:photo_id>', methods=['PUT'])
@login_required
@admin_required
def api_admin_update_photo(photo_id):
    """更新照片信息（重命名、描述）"""
    db = get_supabase_admin()
    current_user_id = session.get('user_id')
    data = request.json
    
    photo_res = db.table("training_photos").select("*").eq("id", photo_id).eq("is_deleted", False).maybe_single().execute()
    if not photo_res.data:
        return jsonify({"success": False, "message": "照片不存在"}), 404
    
    photo = photo_res.data
    
    # 权限检查
    allowed_countries = get_admin_allowed_countries()
    training_country = photo.get('training_country')
    if allowed_countries is not None and training_country not in allowed_countries:
        return jsonify({"success": False, "message": "无权修改此照片"}), 403
    
    update_data = {}
    if 'file_name' in data:
        update_data['file_name'] = data['file_name']
    if 'photo_description' in data:
        update_data['photo_description'] = data['photo_description']
    
    if not update_data:
        return jsonify({"success": False, "message": "没有要更新的字段"}), 400
    
    update_data['updated_at'] = datetime.now(timezone.utc).isoformat()
    update_data['updated_by'] = current_user_id
    
    try:
        result = db.table("training_photos").update(update_data).eq("id", photo_id).execute()
        return jsonify({"success": True, "data": result.data[0] if result.data else None})
    except Exception as e:
        logger.error(f"更新照片失败: {e}")
        return jsonify({"success": False, "message": str(e)}), 500


@admin_training_photos_bp.route('/api/admin/training/photos/<int:photo_id>/cover', methods=['PUT'])
@login_required
@admin_required
def api_admin_set_cover(photo_id):
    """设置培训封面"""
    db = get_supabase_admin()
    current_user_id = session.get('user_id')
    
    photo_res = db.table("training_photos").select("*").eq("id", photo_id).eq("is_deleted", False).maybe_single().execute()
    if not photo_res.data:
        return jsonify({"success": False, "message": "照片不存在"}), 404
    
    photo = photo_res.data
    training_id = photo.get('training_id')
    
    # 权限检查
    allowed_countries = get_admin_allowed_countries()
    training_country = photo.get('training_country')
    if allowed_countries is not None and training_country not in allowed_countries:
        return jsonify({"success": False, "message": "无权操作"}), 403
    
    try:
        # 清除该培训的现有封面
        db.table("training_photos").update({"is_cover": False}).eq("training_id", training_id).eq("is_deleted", False).execute()
        
        # 设置新封面
        now = datetime.now(timezone.utc).isoformat()
        db.table("training_photos").update({
            "is_cover": True,
            "updated_at": now,
            "updated_by": current_user_id
        }).eq("id", photo_id).execute()
        
        return jsonify({"success": True, "message": "已设为封面"})
    except Exception as e:
        logger.error(f"设置封面失败: {e}")
        return jsonify({"success": False, "message": str(e)}), 500


@admin_training_photos_bp.route('/api/admin/training/photos/batch_delete', methods=['POST'])
@login_required
@admin_required
def api_admin_batch_delete_photos():
    """批量删除照片"""
    db = get_supabase_admin()
    current_user_id = session.get('user_id')
    data = request.json
    photo_ids = data.get('ids', [])
    
    if not photo_ids:
        return jsonify({"success": False, "message": "请选择要删除的照片"}), 400
    
    success_count = 0
    fail_count = 0
    errors = []
    
    for photo_id in photo_ids:
        try:
            photo_res = db.table("training_photos").select("*").eq("id", photo_id).eq("is_deleted", False).maybe_single().execute()
            if not photo_res.data:
                fail_count += 1
                errors.append(f"照片 {photo_id} 不存在")
                continue
            
            photo = photo_res.data
            
            # 权限检查
            allowed_countries = get_admin_allowed_countries()
            training_country = photo.get('training_country')
            if allowed_countries is not None and training_country not in allowed_countries:
                fail_count += 1
                errors.append(f"照片 {photo_id}: 无权限")
                continue
            
            # 从 R2 删除
            photo_path = photo.get('photo_path')
            if photo_path:
                delete_from_r2(photo_path)
            
            # 软删除
            now = datetime.now(timezone.utc).isoformat()
            db.table("training_photos").update({
                "is_deleted": True,
                "deleted_at": now,
                "deleted_by": current_user_id
            }).eq("id", photo_id).execute()
            
            success_count += 1
        except Exception as e:
            fail_count += 1
            errors.append(f"照片 {photo_id}: {str(e)}")
    
    return jsonify({
        "success": True,
        "success_count": success_count,
        "fail_count": fail_count,
        "errors": errors[:10]
    })


# ============================================================
# 4. 页面路由
# ============================================================

@admin_training_photos_bp.route('/admin/training/photos')
@login_required
@admin_required
def admin_training_photos_page():
    """管理员照片管理页面"""
    return render_template('admin/admin_training_photos.html')


@admin_training_photos_bp.route('/training/photos')
@login_required
def training_photos_page():
    """学员端照片上传/查看页面"""
    return render_template('exam/training_photos.html')


# ============================================================
# 5. 用户角色 API（供前端权限判断）
# ============================================================

@admin_training_photos_bp.route('/api/user/role')
@login_required
def api_user_role():
    """获取当前用户角色和权限信息"""
    current_user_id = session.get('user_id')
    db = get_supabase()
    
    res = db.table("users").select("role, country, admin_countries").eq("id", current_user_id).maybe_single().execute()
    if not res.data:
        return jsonify({"success": False, "message": "用户不存在"}), 404
    
    user = res.data
    role = user.get('role', 'user')
    country = user.get('country')
    admin_countries = user.get('admin_countries')
    
    # 解析权限范围
    if admin_countries:
        try:
            admin_countries = json.loads(admin_countries) if isinstance(admin_countries, str) else admin_countries
        except:
            admin_countries = []
    else:
        admin_countries = []
    
    is_admin = role in ['admin', 'super_admin', 'developer']
    
    return jsonify({
        "success": True,
        "role": role,
        "country": country,
        "admin_countries": admin_countries,
        "is_admin": is_admin,
        "can_manage_photos": is_admin
    })