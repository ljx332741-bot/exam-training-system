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
    
    def __init__(self, name='default'):
        self._cache = {}
        self._default_ttl = 300  # 默认5分钟
        self._name = name  # 缓存实例名称，用于日志
    
    def _generate_key(self, prefix='', include_user=True):
        """生成缓存键"""
        user_id = session.get('user_id', '') if include_user else ''
        path = request.path
        params = request.args.to_dict() if request.method == 'GET' else {}
        param_str = json.dumps(params, sort_keys=True)
        
        key = f"{prefix}_{user_id}_{path}_{param_str}"
        
        if len(key) > 200:
            key = hashlib.md5(key.encode()).hexdigest()
        
        return key
    
    def get(self, key):
        """获取缓存"""
        if key in self._cache:
            data, timestamp = self._cache[key]
            if datetime.now() - timestamp < timedelta(seconds=self._default_ttl):
                logger.debug(f"[{self._name}] 缓存命中: {key[:30]}...")
                return data
            del self._cache[key]
        return None
    
    def set(self, key, data):
        """设置缓存"""
        self._cache[key] = (data, datetime.now())
        logger.debug(f"[{self._name}] 缓存写入: {key[:30]}...")
    
    def clear(self, prefix=''):
        """清除缓存"""
        if prefix:
            search_prefix = f"{prefix}_"
            keys_to_remove = [k for k in self._cache.keys() if k.startswith(search_prefix)]
            for k in keys_to_remove:
                del self._cache[k]
            logger.info(f"[{self._name}] 清除缓存: {len(keys_to_remove)} 条 (前缀: {prefix})")
            return len(keys_to_remove)
        else:
            count = len(self._cache)
            self._cache.clear()
            logger.info(f"[{self._name}] 清除所有缓存: {count} 条")
            return count
    
    def clear_by_pattern(self, patterns):
        """按模式清除缓存"""
        if isinstance(patterns, str):
            patterns = [patterns]
        
        keys_to_remove = []
        for key in self._cache.keys():
            for pattern in patterns:
                if pattern in key:
                    keys_to_remove.append(key)
                    break
        
        for key in keys_to_remove:
            del self._cache[key]
        
        logger.info(f"[{self._name}] 按模式清除缓存: {len(keys_to_remove)} 条")
        return len(keys_to_remove)
    
    def clear_by_prefixes(self, prefixes):
        """按前缀清除缓存"""
        if isinstance(prefixes, str):
            prefixes = [prefixes]
        
        keys_to_remove = []
        for key in self._cache.keys():
            for prefix in prefixes:
                if key.startswith(prefix):
                    keys_to_remove.append(key)
                    break
        
        for key in keys_to_remove:
            del self._cache[key]
        
        logger.info(f"[{self._name}] 按前缀清除缓存: {len(keys_to_remove)} 条")
        return len(keys_to_remove)
    
    def clear_by_contains(self, patterns):
        """按包含模式清除缓存"""
        if isinstance(patterns, str):
            patterns = [patterns]
        
        keys_to_remove = []
        for key in self._cache.keys():
            for pattern in patterns:
                if pattern in key:
                    keys_to_remove.append(key)
                    break
        
        for key in keys_to_remove:
            del self._cache[key]
        
        logger.info(f"[{self._name}] 按包含模式清除缓存: {len(keys_to_remove)} 条")
        return len(keys_to_remove)
    
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
            "name": self._name,
            "total": total,
            "active": active,
            "expired": expired,
            "ttl_seconds": self._default_ttl
        }

    def get_keys_by_pattern(self, pattern):
        """获取匹配模式的缓存键列表"""
        return [k for k in self._cache.keys() if pattern in k]
    
    def get_key_count(self):
        """获取缓存总数"""
        return len(self._cache)


# 创建全局实例
training_cache = CacheManager(name='training')
exam_cache = CacheManager(name='exam')
user_cache = CacheManager(name='user')
report_cache = CacheManager(name='report')


# ==================== 统一缓存清除函数 ====================

def clear_all_assignment_caches(training_id=None, exam_id=None, user_id=None):
    """
    清除所有分配相关的缓存（统一入口）
    当分配/取消分配操作发生时调用此函数
    """
    all_instances = [training_cache, exam_cache, user_cache, report_cache]
    cleared_count = 0
    
    patterns = []
    
    if training_id:
        patterns.extend([
            f'training_{training_id}',
            f'training_list_{training_id}',
            f'training_detail_{training_id}',
            f'training_bindings_{training_id}',
        ])
        patterns.append('training_list_')
        patterns.append('training_bindings_')
    
    if exam_id:
        patterns.extend([
            f'exam_{exam_id}',
            f'exam_list_{exam_id}',
            f'exam_detail_{exam_id}',
        ])
        patterns.append('exam_')
    
    if user_id:
        patterns.append(f'user_{user_id}')
    
    patterns.extend([
        'completion_report',
        'report_',
        'dashboard_',
        '_assign_',
        '_assignment_',
        '_alloc_',
    ])
    
    patterns = list(set(patterns))
    
    for instance in all_instances:
        cleared_count += instance.clear_by_contains(patterns)
        # 特殊处理前缀
        cleared_count += instance.clear_by_prefixes('training_list_')
        cleared_count += instance.clear_by_prefixes('training_bindings_')
    
    logger.info(f"🧹 统一缓存清除完成: 清除 {cleared_count} 条缓存")
    print(f"🧹 统一缓存清除完成: 清除 {cleared_count} 条缓存", flush=True)
    
    return cleared_count


def clear_training_related_cache(training_id):
    """清除培训相关缓存（便捷函数）"""
    return clear_all_assignment_caches(training_id=training_id)


def clear_exam_related_cache(exam_id):
    """清除考试相关缓存（便捷函数）"""
    return clear_all_assignment_caches(exam_id=exam_id)


def clear_report_cache():
    """清除报表缓存（便捷函数）"""
    cleared = 0
    patterns = ['completion_report', 'report_']
    for instance in [training_cache, exam_cache, user_cache, report_cache]:
        cleared += instance.clear_by_contains(patterns)
    logger.info(f"报表缓存清除完成: 清除 {cleared} 条")
    return cleared


def force_clear_all_training_cache():
    """强制清除所有培训相关缓存"""
    cleared = 0
    all_instances = [training_cache, exam_cache, user_cache, report_cache]
    patterns = ['training_', 'exam_', 'completion_report', 'report_', 'dashboard_', '_assign_', '_assignment_']
    
    for instance in all_instances:
        for pattern in patterns:
            cleared += instance.clear_by_contains(pattern)
    
    logger.info(f"强制清除所有培训缓存: {cleared} 条")
    return cleared


# ==================== 缓存装饰器 ====================

def cache_get(ttl=300, prefix='', include_user=True, cache_instance=None):
    """
    缓存 GET 请求的装饰器
    
    Args:
        ttl: 缓存时间（秒），默认300秒（5分钟）
        prefix: 缓存前缀，用于分类管理
        include_user: 是否包含用户ID到缓存键
        cache_instance: 指定的缓存实例（默认使用 training_cache）
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            # 只缓存 GET 请求
            if request.method != 'GET':
                return func(*args, **kwargs)
            
            # 选择缓存实例 - 确保在函数内部定义
            instance = cache_instance if cache_instance is not None else training_cache
            
            # 生成缓存键
            key = instance._generate_key(prefix=prefix, include_user=include_user)
            
            # 检查缓存
            cached = instance.get(key)
            if cached is not None:
                return cached
            
            # 执行函数
            result = func(*args, **kwargs)
            
            # 存入缓存
            instance.set(key, result)
            
            return result
        
        # 添加清除缓存的方法
        def clear_cache():
            instance = cache_instance if cache_instance is not None else training_cache
            return instance.clear(prefix)
        
        wrapper.clear_cache = clear_cache
        
        def cache_key():
            instance = cache_instance if cache_instance is not None else training_cache
            return instance._generate_key(prefix=prefix, include_user=include_user)
        
        wrapper.cache_key = cache_key
        wrapper.cache_instance = cache_instance if cache_instance is not None else training_cache
        
        return wrapper
    return decorator


def invalidate_cache_on_change(prefix='', patterns=None, clear_all_assignment=False):
    """
    在数据变更时清除缓存的装饰器（用于 POST, PUT, DELETE）
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            result = func(*args, **kwargs)
            
            # 判断是否成功
            is_success = True
            if result and isinstance(result, tuple):
                data = result[0] if len(result) > 0 else {}
                if isinstance(data, dict) and data.get('success') is False:
                    is_success = False
            elif result and isinstance(result, dict):
                if result.get('success') is False:
                    is_success = False
            
            if is_success:
                cleared = 0
                all_instances = [training_cache, exam_cache, user_cache, report_cache]
                
                if prefix:
                    for instance in all_instances:
                        cleared += instance.clear(prefix)
                
                if patterns:
                    if isinstance(patterns, str):
                        patterns = [patterns]
                    for instance in all_instances:
                        cleared += instance.clear_by_contains(patterns)
                
                if clear_all_assignment:
                    cleared += clear_all_assignment_caches()
                
                logger.info(f"数据变更后清除缓存: {cleared} 条")
            
            return result
        return wrapper
    return decorator


# ==================== 监控接口 ====================

def get_cache_status():
    """获取所有缓存实例的状态"""
    return {
        "training_cache": training_cache.get_stats(),
        "exam_cache": exam_cache.get_stats(),
        "user_cache": user_cache.get_stats(),
        "report_cache": report_cache.get_stats()
    }


def clear_all_caches():
    """清除所有缓存"""
    cleared = 0
    for instance in [training_cache, exam_cache, user_cache, report_cache]:
        cleared += instance.clear()
    logger.warning(f"⚠️ 清除所有缓存: {cleared} 条")
    return cleared