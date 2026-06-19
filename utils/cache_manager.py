# utils/cache_manager.py
import hashlib
import json
import logging
from datetime import datetime, timedelta
from functools import wraps
from flask import request, session
logger = logging.getLogger(__name__)


class CacheManager:
    """
    通用缓存管理器
    支持：
    - 自动缓存 GET 请求
    - 基于用户ID + URL + 参数的缓存键
    - 自动过期
    - 手动清除
    """
    
    def __init__(self):
        self._cache = {}
        self._default_ttl = 300  # 默认5分钟
    
    def _generate_key(self, prefix='', include_user=True):
        """生成缓存键"""
        # 用户ID
        user_id = session.get('user_id', '') if include_user else ''
        
        # 请求路径
        path = request.path
        
        # 请求参数（只取 GET 参数）
        params = request.args.to_dict() if request.method == 'GET' else {}
        param_str = json.dumps(params, sort_keys=True)
        
        # 组合键
        key = f"{prefix}_{user_id}_{path}_{param_str}"
        
        # 如果太长，使用 MD5
        if len(key) > 200:
            key = hashlib.md5(key.encode()).hexdigest()
        
        return key
    
    def get(self, key):
        """获取缓存"""
        if key in self._cache:
            data, timestamp = self._cache[key]
            if datetime.now() - timestamp < timedelta(seconds=self._default_ttl):
                # ✅ 使用明显标识
                print(f"✅✅✅ 缓存命中: {key[:50]}...", flush=True)
                logger.debug(f"缓存命中: {key[:30]}...")
                return data
            # 过期则删除
            del self._cache[key]
        print(f"❌❌❌ 缓存未命中: {key[:50]}...", flush=True)
        return None
    
    def set(self, key, data):
        """设置缓存"""
        self._cache[key] = (data, datetime.now())
        print(f"💾💾💾 缓存写入: {key[:50]}...", flush=True)
        logger.debug(f"缓存写入: {key[:30]}...")
    
    def clear(self, prefix=''):
        import traceback
        print("⚠️⚠️⚠️ 缓存被清除:", flush=True)
        traceback.print_stack()
        """清除缓存"""
        if prefix:
            # 清除指定前缀的缓存
            keys_to_remove = [k for k in self._cache.keys() if k.startswith(prefix)]
            for k in keys_to_remove:
                del self._cache[k]
            logger.info(f"清除缓存: {len(keys_to_remove)} 条 (前缀: {prefix})")
        else:
            # 清除所有缓存
            count = len(self._cache)
            self._cache.clear()
            logger.info(f"清除缓存: {count} 条")
    
    def get_stats(self):
        """获取缓存统计信息"""
        total = len(self._cache)
        active = 0
        expired = 0
        now = datetime.now()
        
        for key, (data, timestamp) in self._cache.items():
            if now - timestamp < timedelta(seconds=self._default_ttl):
                active += 1
            else:
                expired += 1
        
        return {
            "total": total,
            "active": active,
            "expired": expired,
            "ttl_seconds": self._default_ttl
        }


# 创建全局实例
training_cache = CacheManager()
exam_cache = CacheManager()
user_cache = CacheManager()


# ==================== 装饰器 ====================

def cache_get(ttl=300, prefix='', include_user=True):
    """
    缓存 GET 请求的装饰器
    
    Args:
        ttl: 缓存时间（秒），默认300秒（5分钟）
        prefix: 缓存前缀，用于分类管理
        include_user: 是否包含用户ID到缓存键
    
    Example:
        @cache_get(ttl=300, prefix='training_list')
        def get_trainings():
            return [...]
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            print(f"📌 请求方法: {request.method}", flush=True)  # 应该是 GET
            # 只缓存 GET 请求
            if request.method != 'GET':
                return func(*args, **kwargs)
            
            # 生成缓存键
            cache_instance = training_cache  # 默认使用 training_cache
            key = cache_instance._generate_key(prefix=prefix, include_user=include_user)
            print(f"🔑 缓存键: {key}", flush=True)
            
            # 检查缓存
            cached = cache_instance.get(key)
            if cached is not None:
                return cached
            
            # 执行函数
            result = func(*args, **kwargs)
            
            # 存入缓存
            cache_instance.set(key, result)
            
            return result
        
        # 添加清除缓存的方法
        wrapper.clear_cache = lambda: training_cache.clear(prefix)
        wrapper.cache_key = lambda: training_cache._generate_key(prefix=prefix, include_user=include_user)
        
        return wrapper
    return decorator


def invalidate_cache_on_change(prefix=''):
    """
    在数据变更时清除缓存的装饰器（用于 POST, PUT, DELETE）
    
    Example:
        @invalidate_cache_on_change(prefix='training_list')
        def create_training():
            return {...}
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            # 执行函数
            result = func(*args, **kwargs)
            
            # 如果成功，清除缓存
            if result and isinstance(result, tuple):
                # 处理 (data, status_code) 格式
                data = result[0] if len(result) > 0 else {}
                if isinstance(data, dict) and data.get('success', True):
                    training_cache.clear(prefix)
            elif result and isinstance(result, dict):
                # 处理 dict 格式
                if result.get('success', True):
                    training_cache.clear(prefix)
            else:
                # 默认清除
                training_cache.clear(prefix)
            
            return result
        return wrapper
    return decorator