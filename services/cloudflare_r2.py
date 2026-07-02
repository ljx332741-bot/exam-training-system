# services/cloudflare_r2.py
"""
Cloudflare R2 存储服务（兼容 AWS S3 API）
"""

import boto3
import uuid
from datetime import datetime
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError
from flask import current_app
import logging

from config import Config

logger = logging.getLogger(__name__)


def get_r2_client():
    """获取 R2 客户端"""
    endpoint = Config.CLOUDFLARE_R2_ENDPOINT or current_app.config.get('CLOUDFLARE_R2_ENDPOINT')
    access_key = Config.CLOUDFLARE_R2_ACCESS_KEY_ID or current_app.config.get('CLOUDFLARE_R2_ACCESS_KEY_ID')
    secret_key = Config.CLOUDFLARE_R2_SECRET_ACCESS_KEY or current_app.config.get('CLOUDFLARE_R2_SECRET_ACCESS_KEY')
    region = Config.CLOUDFLARE_R2_REGION or current_app.config.get('CLOUDFLARE_R2_REGION', 'auto')
    
    return boto3.client(
        's3',
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name=region,
        config=BotoConfig(signature_version='s3v4')
    )


def get_r2_bucket():
    """获取 R2 存储桶名称"""
    bucket = Config.get_r2_bucket()
    if not bucket:
        bucket = current_app.config.get('CLOUDFLARE_R2_BUCKET')
    return bucket


def get_r2_public_url():
    """获取 R2 公共 URL"""
    public_url = Config.get_r2_public_url()
    if not public_url:
        public_url = current_app.config.get('CLOUDFLARE_R2_PUBLIC_URL')
    return public_url


def upload_to_r2(file_obj, training_id, filename=None, content_type=None):
    """
    上传文件到 Cloudflare R2
    
    Args:
        file_obj: 文件对象（支持 .read()）
        training_id: 培训 ID
        filename: 原始文件名（可选）
        content_type: MIME 类型（可选）
    
    Returns:
        tuple: (public_url, file_key)
    """
    try:
        client = get_r2_client()
        bucket = get_r2_bucket()
        public_url_base = get_r2_public_url()
        
        if not bucket:
            raise ValueError("CLOUDFLARE_R2_BUCKET 未配置")
        if not public_url_base:
            raise ValueError("CLOUDFLARE_R2_PUBLIC_URL 未配置")
        
        # 生成唯一文件名
        if filename:
            ext = filename.rsplit('.', 1)[-1] if '.' in filename else 'jpg'
        else:
            ext = 'jpg'
        
        date_str = datetime.now().strftime('%Y%m%d')
        random_id = uuid.uuid4().hex[:8]
        file_key = f"training_{training_id}/{date_str}_{random_id}.{ext}"
        
        # 确定 Content-Type
        if not content_type:
            content_type = 'image/jpeg'
            if ext.lower() in ['png']:
                content_type = 'image/png'
            elif ext.lower() in ['gif']:
                content_type = 'image/gif'
            elif ext.lower() in ['webp']:
                content_type = 'image/webp'
        
        # 上传文件
        client.upload_fileobj(
            file_obj,
            bucket,
            file_key,
            ExtraArgs={
                'ContentType': content_type,
                'CacheControl': 'public, max-age=31536000, immutable'
            }
        )
        
        public_url = f"{public_url_base}/{file_key}"
        logger.info(f"✅ 文件上传成功: {public_url}")
        return public_url, file_key
        
    except ClientError as e:
        logger.error(f"❌ R2 上传失败: {e}")
        raise
    except Exception as e:
        logger.error(f"❌ 上传异常: {e}")
        raise


def delete_from_r2(file_key):
    """
    从 Cloudflare R2 删除文件
    
    Args:
        file_key: 文件路径
    """
    try:
        client = get_r2_client()
        bucket = get_r2_bucket()
        
        if not bucket:
            raise ValueError("CLOUDFLARE_R2_BUCKET 未配置")
        
        client.delete_object(Bucket=bucket, Key=file_key)
        logger.info(f"✅ 文件删除成功: {file_key}")
        return True
        
    except ClientError as e:
        logger.error(f"❌ R2 删除失败: {e}")
        raise
    except Exception as e:
        logger.error(f"❌ 删除异常: {e}")
        raise


def delete_multiple_from_r2(file_keys):
    """
    批量从 Cloudflare R2 删除文件
    
    Args:
        file_keys: 文件路径列表
    """
    if not file_keys:
        return True
    
    try:
        client = get_r2_client()
        bucket = get_r2_bucket()
        
        if not bucket:
            raise ValueError("CLOUDFLARE_R2_BUCKET 未配置")
        
        objects = [{'Key': key} for key in file_keys]
        client.delete_objects(
            Bucket=bucket,
            Delete={'Objects': objects, 'Quiet': True}
        )
        
        logger.info(f"✅ 批量删除成功: {len(file_keys)} 个文件")
        return True
        
    except ClientError as e:
        logger.error(f"❌ R2 批量删除失败: {e}")
        raise
    except Exception as e:
        logger.error(f"❌ 批量删除异常: {e}")
        raise


def get_file_info(file_key):
    """获取文件信息"""
    try:
        client = get_r2_client()
        bucket = get_r2_bucket()
        
        if not bucket:
            return None
        
        response = client.head_object(Bucket=bucket, Key=file_key)
        return {
            'size': response.get('ContentLength'),
            'last_modified': response.get('LastModified'),
            'etag': response.get('ETag'),
            'content_type': response.get('ContentType')
        }
    except ClientError:
        return None