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
'''一行布局
def add_watermark_to_image(
    image_data, 
    training_name, 
    include_training_name=True,
    font_scale=0.03,      # 字体大小比例（默认 6%）
    min_font_size=24,     # 最小字体（px）
    bg_padding_scale=0.15 # 背景框 padding 比例（默认 15%）
    ):
    """
    为图片添加水印（左下角）
    Args:
        image_data: 图片二进制数据
        training_name: 培训名称
        include_training_name: 是否包含培训名称
        font_scale: 字体大小相对于图片尺寸的比例
        min_font_size: 最小字体大小（px）
        bg_padding_scale: 背景框 padding 相对于字体大小的比例
    """
    try:
        # 打开图片
        img = Image.open(io.BytesIO(image_data))
        
        # 转换为 RGB
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
        font_size = max(int(base_size * font_scale), min_font_size)

        # 尝试加载字体
        import os
        font = None
        font_paths = [
            # Linux 中文字体
            '/usr/share/fonts/truetype/wqy/wqy-microhei.ttc',
            '/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc',
            '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
            '/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc',
            '/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc',
            '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
            '/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf',
            # macOS 中文字体
            '/System/Library/Fonts/PingFang.ttc',
            '/System/Library/Fonts/STHeiti Light.ttc',
            '/System/Library/Fonts/Hiragino Sans GB.ttc',
            '/System/Library/Fonts/AppleSDGothicNeo.ttc',
            # Windows 中文字体
            'C:/Windows/Fonts/msyh.ttc',      # 微软雅黑
            'C:/Windows/Fonts/msyhbd.ttc',    # 微软雅黑粗体
            'C:/Windows/Fonts/simsun.ttc',    # 宋体
            'C:/Windows/Fonts/simhei.ttf',    # 黑体
            'C:/Windows/Fonts/STKAITI.TTF',   # 楷体
            'C:/Windows/Fonts/arial.ttf',     # Arial（备选）
        ]
        for path in font_paths:
            if os.path.exists(path):
                try:
                    font = ImageFont.truetype(path, font_size)
                    break
                except Exception as e:
                    print(f"⚠️ 加载字体失败 {path}: {e}")
                    continue

        # 如果所有字体都加载失败，尝试使用 ImageFont.load_default() 并记录警告
        if font is None:
            print("⚠️ 未找到任何字体，使用默认字体（中文可能显示为方块）")
            font = ImageFont.load_default()
        
        # 计算文本尺寸
        bbox = draw.textbbox((0, 0), watermark_text, font=font)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]
        # 计算位置
        padding = max(int(font_size * 0.4), 12)
        x = padding
        y = img.height - text_height - padding
        
        # 绘制背景框（使用较小的 padding）
        bg_padding = int(font_size * bg_padding_scale) + 4
        draw.rectangle(
            [x - bg_padding, y - bg_padding, x + text_width + bg_padding, y + text_height + bg_padding],
            fill=(0, 0, 0, 160)
        )
        
        # 绘制水印文字
        draw.text(
            (x + 1, y + 1),
            watermark_text,
            font=font,
            fill=(0, 0, 0, 200)
        )
        draw.text(
            (x, y),
            watermark_text,
            font=font,
            fill=(255, 255, 255, 255)
        )
        
        # 保存为 JPEG
        output = io.BytesIO()
        img.save(output, format='JPEG', quality=92)
        return output.getvalue()
        
    except Exception as e:
        logger.error(f"添加水印失败: {e}", exc_info=True)
        return image_data
'''
def add_watermark_to_image(
    image_data, 
    training_name, 
    include_training_name=True,
    font_scale=0.03,
    min_font_size=24,
    bg_padding_scale=0.12
):
    """
    为图片添加水印（左下角）- 双行布局
    第一行：培训名称
    第二行：日期时间
    """
    try:
        logger.info(f"🖼️ 开始添加水印: training_name={training_name}")
        
        # 打开图片
        img = Image.open(io.BytesIO(image_data))
        
        # 转换为 RGB
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
        
        # 双行布局：构建两行文本
        if include_training_name and training_name:
            line1 = training_name
            line2 = date_str
            watermark_texts = [line1, line2]
        else:
            # 如果不显示培训名称，只显示日期
            watermark_texts = [date_str]
        
        # 根据图片大小动态调整字体大小
        base_size = min(img.width, img.height)
        font_size = max(int(base_size * font_scale), min_font_size)
        
        # 字体大小可以稍小一点（双行时更协调）
        font_size = max(int(font_size * 0.85), 18)
        
        # 尝试加载中文字体
        import os
        font = None
        font_paths = [
            # Linux 中文字体
            '/usr/share/fonts/truetype/wqy/wqy-microhei.ttc',
            '/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc',
            '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
            '/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc',
            '/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc',
            '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
            # macOS 中文字体
            '/System/Library/Fonts/PingFang.ttc',
            '/System/Library/Fonts/STHeiti Light.ttc',
            '/System/Library/Fonts/Hiragino Sans GB.ttc',
            # Windows 中文字体
            'C:/Windows/Fonts/msyh.ttc',
            'C:/Windows/Fonts/msyhbd.ttc',
            'C:/Windows/Fonts/simsun.ttc',
            'C:/Windows/Fonts/simhei.ttf',
        ]
        for path in font_paths:
            if os.path.exists(path):
                try:
                    font = ImageFont.truetype(path, font_size)
                    logger.info(f"✅ 使用字体: {path}")
                    break
                except:
                    continue
        if font is None:
            logger.warning("⚠️ 使用默认字体")
            font = ImageFont.load_default()
        
        # 计算所有行的尺寸（取最大宽度）
        text_widths = []
        text_heights = []
        for text in watermark_texts:
            bbox = draw.textbbox((0, 0), text, font=font)
            text_widths.append(bbox[2] - bbox[0])
            text_heights.append(bbox[3] - bbox[1])
        
        max_width = max(text_widths) if text_widths else 0
        line_height = max(text_heights) if text_heights else 0
        total_height = len(watermark_texts) * line_height + (len(watermark_texts) - 1) * 6  # 行间距 6px
        
        # 计算位置（左下角）
        padding = max(int(font_size * 0.4), 12)
        x = padding
        # 上移图片高度的 2%（大图移动更多，小图移动较少）
        offset_up = int(img.height * 0.02)  # 2% 偏移量
        y = img.height - total_height - padding - offset_up
        
        # 绘制背景框
        bg_padding = int(font_size * bg_padding_scale) + 4
        draw.rectangle(
            [x - bg_padding, y - bg_padding, 
             x + max_width + bg_padding, y + total_height + bg_padding],
            fill=(0, 0, 0, 160)
        )
        
        # 逐行绘制水印文字
        current_y = y
        for idx, text in enumerate(watermark_texts):
            # 计算该行文本宽度（用于居中或左对齐）
            bbox = draw.textbbox((0, 0), text, font=font)
            text_width = bbox[2] - bbox[0]
            
            # 左对齐绘制
            draw.text(
                (x + 1, current_y + 1),
                text,
                font=font,
                fill=(0, 0, 0, 200)
            )
            draw.text(
                (x, current_y),
                text,
                font=font,
                fill=(255, 255, 255, 255)
            )
            
            # 移动到下一行
            current_y += line_height + 6  # 行间距 6px
        
        logger.info(f"✅ 水印添加成功: 字体={font_size}px, {len(watermark_texts)}行")
        
        # 保存为 JPEG
        output = io.BytesIO()
        img.save(output, format='JPEG', quality=92)
        return output.getvalue()
        
    except Exception as e:
        logger.error(f"添加水印失败: {e}", exc_info=True)
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
        add_watermark: 是否添加水印（true/false）
        include_training_name: 是否包含培训名称（true/false）
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

    # 获取水印参数（默认开启）
    add_watermark = request.form.get('add_watermark', 'true').lower() == 'true'
    include_training_name = request.form.get('include_training_name', 'true').lower() == 'true'
    
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
        content_type = file.content_type or 'image/jpeg'
        if content_type not in allowed_types:
            # 尝试通过扩展名判断
            ext = file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else ''
            if ext not in ['jpg', 'jpeg', 'png', 'gif', 'webp', 'heic']:
                errors.append(f"{file.filename}: 不支持的图片格式")
                continue
            if ext in ['jpg', 'jpeg']:
                content_type = 'image/jpeg'
            elif ext == 'png':
                content_type = 'image/png'
            elif ext == 'gif':
                content_type = 'image/gif'
            elif ext == 'webp':
                content_type = 'image/webp'
            elif ext == 'heic':
                content_type = 'image/heic'
                
        try:
            # 读取图片数据
            file.seek(0)
            image_data = file.read()

            # ✅ 添加水印（如果开启）- 与学员端完全一致
            if add_watermark:
                image_data = add_watermark_to_image(
                    image_data, 
                    training_name, 
                    include_training_name
                )
            
            # ✅ 确定扩展名和内容类型 - 与学员端完全一致
            original_ext = file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else 'jpg'
            
            # 如果添加了水印，图片已转换为 JPEG
            if add_watermark:
                ext = 'jpg'
                content_type = 'image/jpeg'
            else:
                # 未添加水印，使用原始格式
                ext = original_ext
                # 支持的类型映射
                type_map = {
                    'jpg': 'image/jpeg',
                    'jpeg': 'image/jpeg',
                    'jfif': 'image/jpeg',
                    'jpe': 'image/jpeg',
                    'png': 'image/png',
                    'gif': 'image/gif',
                    'webp': 'image/webp',
                    'heic': 'image/heic',
                }
                
                if ext not in type_map:
                    ext = 'jpg'
                    content_type = 'image/jpeg'
                else:
                    content_type = type_map[ext]
                    if ext in ['jfif', 'jpe']:
                        ext = 'jpg'
            
            # 生成唯一文件名
            unique_id = uuid.uuid4().hex[:12]
            file_key = f"training_{training_id}/{unique_id}.{ext}"

            # ✅ 关键修复：使用处理后的 image_data，而不是原始 file
            file_obj = BytesIO(image_data)
            file_obj.seek(0)
            
            # ✅ 上传到 R2
            public_url, photo_path = upload_to_r2(
                file_obj=file_obj,                # ✅ 使用 BytesIO 对象
                training_id=training_id,
                filename=f"{unique_id}.{ext}",    # ✅ 使用新文件名
                content_type=content_type         # ✅ 使用正确的 content_type
            )
            
            # 获取描述
            description = descriptions[idx] if idx < len(descriptions) else ''
            
            # 插入数据库
            insert_data = {
                "training_id": int(training_id),
                "training_name": training_name,
                "training_country": training_country,
                "exam_id": int(exam_id) if exam_id else None,
                "exam_name": exam_name,
                "photo_url": public_url,
                "photo_path": photo_path,
                "file_name": f"{unique_id}.{ext}",  # ✅ 使用新文件名
                "file_size": file_size,
                "file_type": content_type,          # ✅ 使用正确的 content_type
                "photo_description": description,
                "is_cover": False,
                "uploaded_at": now,
                "uploaded_by": current_user_id,
                "metadata": {
                    "upload_source": "admin",
                    "original_filename": file.filename,
                    "has_watermark": add_watermark,
                    "watermark_text": f"{training_name} | {datetime.now().strftime('%Y-%m-%d %H:%M')}" if add_watermark and include_training_name else datetime.now().strftime('%Y-%m-%d %H:%M') if add_watermark else None,
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

    # 在返回之前，查询该培训的最新照片数量
    count_res = db.table("training_photos").select("id", count="exact") \
        .eq("training_id", int(training_id)) \
        .eq("is_deleted", False) \
        .execute()
    new_photo_count = count_res.count or 0

    return jsonify({
        "success": True,
        "uploaded_count": len(uploaded_photos),
        "error_count": len(errors),
        "photos": uploaded_photos,
        "errors": errors,
        "photo_count": new_photo_count
    })

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



# ============================================================
# 2. 学员端 API
# ============================================================
@admin_training_photos_bp.route('/api/training/photos', methods=['GET'])
@login_required
def api_training_get_photos():
    """
    获取学员可见的培训照片
    - 普通用户：仅自己国家 + 被分配的培训
    - 管理员：权限范围内的所有照片
    """
    db = get_supabase_admin()  # 使用管理员客户端绕过 RLS
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
    allowed_countries = get_admin_allowed_countries()
    
    # ============================================================
    # 管理员：按权限范围过滤
    # ============================================================
    if is_admin:
        query = db.table("training_photos").select("*", count="exact").eq("is_deleted", False)
        
        if allowed_countries is not None:
            if allowed_countries:
                query = query.in_("training_country", allowed_countries)
            else:
                return jsonify({"success": True, "data": [], "total": 0, "page": page, "per_page": per_page})
        
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
                "per_page": per_page,
                "user_country": user_country,
                "user_role": user_role,
                "is_admin": is_admin
            })
        except Exception as e:
            import traceback
            traceback.print_exc()
            return jsonify({"success": False, "message": str(e)}), 500
    
    # ============================================================
    # 普通学员：只能看到自己被分配的培训的照片
    # ============================================================
    
    # 1. 获取用户被分配的培训ID
    assigned_res = db.table("training_assignments").select("training_id").eq("user_id", current_user_id).execute()
    assigned_training_ids = [a['training_id'] for a in (assigned_res.data or [])]
    
    # 2. 获取用户已签到的培训ID（全国推送场景）
    signed_res = db.table("training_attendances").select("training_id").eq("user_id", current_user_id).execute()
    signed_training_ids = [a['training_id'] for a in (signed_res.data or [])]
    
    # 3. 获取已完成考试需要补签的培训ID
    completed_exams_res = db.table("exam_results").select("exam_id").eq("user_id", current_user_id).execute()
    completed_exam_ids = [r['exam_id'] for r in (completed_exams_res.data or [])]
    pending_sign_training_ids = set()
    if completed_exam_ids:
        bindings_res = db.table("training_exam_bindings").select("training_id").in_("exam_id", completed_exam_ids).execute()
        for b in (bindings_res.data or []):
            training_id_tmp = b['training_id']
            if training_id_tmp not in signed_training_ids:
                pending_sign_training_ids.add(training_id_tmp)
    
    # 4. 合并所有可访问的培训ID
    accessible_training_ids = set(assigned_training_ids) | set(signed_training_ids) | pending_sign_training_ids
    
    # 5. 如果没有可访问的培训，返回空
    if not accessible_training_ids:
        return jsonify({
            "success": True,
            "data": [],
            "total": 0,
            "page": page,
            "per_page": per_page,
            "user_country": user_country,
            "user_role": user_role,
            "is_admin": is_admin
        })
    
    # 6. 获取这些培训的照片
    query = db.table("training_photos").select("*", count="exact").eq("is_deleted", False)
    
    if training_id:
        # 如果指定了 training_id，检查是否在可访问列表中
        if int(training_id) not in accessible_training_ids:
            return jsonify({"success": True, "data": [], "total": 0, "page": page, "per_page": per_page})
        query = query.eq("training_id", training_id)
    else:
        query = query.in_("training_id", list(accessible_training_ids))
    
    if only_mine:
        query = query.eq("uploaded_by", current_user_id)
    
    # 按国家过滤（安全保护）
    if user_country:
        query = query.eq("training_country", user_country)
    else:
        return jsonify({"success": True, "data": [], "total": 0, "page": page, "per_page": per_page})
    
    query = query.order("uploaded_at", desc=True)
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
                # 普通学员只能删除自己上传的
                p['can_edit'] = p.get('uploaded_by') == current_user_id
                p['can_delete'] = p.get('uploaded_by') == current_user_id
        
        return jsonify({
            "success": True,
            "data": photos,
            "total": total,
            "page": page,
            "per_page": per_page,
            "user_country": user_country,
            "user_role": user_role,
            "is_admin": is_admin,
            "accessible_training_count": len(accessible_training_ids)
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

    # 获取用户信息
    user_res = db.table("users").select("country, name_en, name_cn").eq("id", current_user_id).maybe_single().execute()
    if not user_res.data:
        logger.error(f"用户不存在: {current_user_id}")
        return jsonify({"success": False, "message": "用户不存在"}), 404
    
    user_country = user_res.data.get('country')
    user_name = user_res.data.get('name_en') or user_res.data.get('name_cn', '')
    
    # 获取表单参数
    training_id = request.form.get('training_id')
    training_name = request.form.get('training_name')
    add_watermark = request.form.get('add_watermark', 'true').lower() == 'true'
    include_training_name = request.form.get('include_training_name', 'true').lower() == 'true'

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
    if current_count >= MAX_PHOTOS_PER_TRAINING:
        logger.warning(f"培训照片已满: {current_count}")
        return jsonify({
            "success": False,
            "message": f"该培训已上传 {current_count} 张照片，达到上限 {MAX_PHOTOS_PER_TRAINING} 张"
        }), 400
    
    # 检查文件
    files = request.files.getlist('photos')
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

    # 确定最终使用的国家（用于数据库存储）
    final_country = training_country or user_country
    
    for idx, file in enumerate(files):
        if file.filename == '':
            continue
        
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
            
            result = db.table("training_photos").insert(insert_data).execute()
            if result.data:
                photo_data = result.data[0]
                photo_data['uploaded_by_name'] = user_name
                photo_data['can_edit'] = True
                photo_data['can_delete'] = True
                uploaded_photos.append(photo_data)
            else:
                errors.append(f"{file.filename}: 保存记录失败")
                logger.error(f"数据库插入失败: {file.filename}")
                
        except Exception as e:
            logger.error(f"上传照片失败 {file.filename}: {e}")
            errors.append(f"{file.filename}: {str(e)}")

    # 在返回之前，查询该培训的最新照片数量
    count_res = db.table("training_photos").select("id", count="exact") \
        .eq("training_id", int(training_id)) \
        .eq("is_deleted", False) \
        .execute()
    new_photo_count = count_res.count or 0

    return jsonify({
        "success": True,
        "uploaded_count": len(uploaded_photos),
        "error_count": len(errors),
        "photos": uploaded_photos,
        "errors": errors,
        "remaining_slots": remaining - len(uploaded_photos),
        "photo_count": new_photo_count
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

    # 获取照片信息
    photo_res = db.table("training_photos").select("*").eq("id", photo_id).eq("is_deleted", False).maybe_single().execute()
    if not photo_res.data:
        logger.warning(f"照片不存在或已删除: photo_id={photo_id}")
        return jsonify({"success": False, "message": "照片不存在或已删除"}), 404
    
    photo = photo_res.data
    training_id = photo.get('training_id')
    # 权限检查
    can_edit, can_delete, _ = validate_photo_permission(photo, current_user_id, current_role)
    
    if not can_delete:
        logger.warning(f"无权限删除: user_id={current_user_id}, uploaded_by={photo.get('uploaded_by')}")
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

        # 查询该培训剩余照片数量
        count_res = db.table("training_photos").select("id", count="exact") \
            .eq("training_id", training_id) \
            .eq("is_deleted", False) \
            .execute()
        new_photo_count = count_res.count or 0
        
        return jsonify({
            "success": True, 
            "message": "照片已删除",
            "training_id": training_id,
            "photo_count": new_photo_count
        })
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
    training_id = photo.get('training_id')
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

        # 查询该培训剩余照片数量
        count_res = db.table("training_photos").select("id", count="exact") \
            .eq("training_id", training_id) \
            .eq("is_deleted", False) \
            .execute()
        new_photo_count = count_res.count or 0
        
        return jsonify({
            "success": True, 
            "message": "照片已删除",
            "training_id": training_id,
            "photo_count": new_photo_count
        })
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
            training_id = photo.get('training_id')
            
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

    # 查询该培训剩余照片数量
    new_photo_count = 0
    if training_id:
        count_res = db.table("training_photos").select("id", count="exact") \
            .eq("training_id", training_id) \
            .eq("is_deleted", False) \
            .execute()
        new_photo_count = count_res.count or 0
    
    return jsonify({
        "success": True,
        "success_count": success_count,
        "fail_count": fail_count,
        "errors": errors[:10],
        "training_id": training_id,
        "photo_count": new_photo_count
    })


# ============================================================
# 4. 页面路由
# ============================================================

@admin_training_photos_bp.route('/admin/training/photos')
@login_required
@admin_required
def admin_training_photos_page():
    """管理员照片管理页面"""
    training_id = request.args.get('training_id')
    training_name = request.args.get('training_name')
    return render_template('admin/admin_training_photos.html', 
                          training_id=training_id,
                          training_name=training_name)


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