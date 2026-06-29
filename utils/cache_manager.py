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
                # 使用明显标识
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

    def clear_by_pattern(self, patterns):
        """
        按模式清除缓存（支持多个模式）
        
        Args:
            patterns: 字符串或字符串列表，缓存键包含这些模式即被清除
        
        Example:
            cache.clear_by_pattern('training_123')
            cache.clear_by_pattern(['training_123', 'exam_456'])
        """
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
        
        logger.info(f"按模式清除缓存: {len(keys_to_remove)} 条 (模式: {patterns})")
        return len(keys_to_remove)
    
    def clear_by_prefixes(self, prefixes):
        """
        按前缀清除缓存
        
        Args:
            prefixes: 字符串或字符串列表
        
        Example:
            cache.clear_by_prefixes('training_')
            cache.clear_by_prefixes(['training_', 'exam_', 'completion_report'])
        """
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
        
        logger.info(f"按前缀清除缓存: {len(keys_to_remove)} 条 (前缀: {prefixes})")
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
training_cache = CacheManager()
exam_cache = CacheManager()
user_cache = CacheManager()
report_cache = CacheManager()


# ==================== 统一缓存清除函数 ====================

def clear_all_assignment_caches(training_id=None, exam_id=None, user_id=None):
    """
    清除所有分配相关的缓存（统一入口）
    当分配/取消分配操作发生时调用此函数
    
    Args:
        training_id: 培训ID（可选）
        exam_id: 考试ID（可选）
        user_id: 用户ID（可选）
    """
    patterns = []
    prefixes = []
    
    # 1. 清除培训相关缓存
    if training_id:
        patterns.extend([
            f'training_{training_id}',
            f'training_list_{training_id}',
            f'training_detail_{training_id}'
        ])
        prefixes.append('training_')
    
    # 2. 清除考试相关缓存
    if exam_id:
        patterns.extend([
            f'exam_{exam_id}',
            f'exam_list_{exam_id}',
            f'exam_detail_{exam_id}'
        ])
        prefixes.append('exam_')
    
    # 3. 清除用户相关缓存
    if user_id:
        patterns.append(f'user_{user_id}')
    
    # 4. 清除报表缓存（所有报表相关）
    prefixes.append('completion_report')
    prefixes.append('report_')
    
    # 5. 清除 dashboard 缓存
    prefixes.append('dashboard_')
    
    # 执行清除
    cleared_count = 0
    
    # 按模式清除
    if patterns:
        cleared_count += training_cache.clear_by_pattern(patterns)
        cleared_count += exam_cache.clear_by_pattern(patterns)
        cleared_count += user_cache.clear_by_pattern(patterns)
        cleared_count += report_cache.clear_by_pattern(patterns)
    
    # 按前缀清除
    if prefixes:
        cleared_count += training_cache.clear_by_prefixes(prefixes)
        cleared_count += exam_cache.clear_by_prefixes(prefixes)
        cleared_count += user_cache.clear_by_prefixes(prefixes)
        cleared_count += report_cache.clear_by_prefixes(prefixes)
    
    # 6. 额外清除所有缓存实例中的相关键
    all_instances = [training_cache, exam_cache, user_cache, report_cache]
    for instance in all_instances:
        # 清除缓存键中包含分配相关关键词的
        assign_patterns = ['_assign_', '_assignment_', '_alloc_']
        for pattern in assign_patterns:
            keys = instance.get_keys_by_pattern(pattern)
            for key in keys:
                instance._cache.pop(key, None)
                cleared_count += 1
    
    logger.info(f"统一缓存清除完成: 清除 {cleared_count} 条缓存")
    print(f"🧹🧹🧹 统一缓存清除完成: 清除 {cleared_count} 条缓存", flush=True)
    
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
    for instance in [training_cache, exam_cache, user_cache, report_cache]:
        cleared += instance.clear_by_prefixes(['completion_report', 'report_'])
    logger.info(f"报表缓存清除完成: 清除 {cleared} 条")
    return cleared


# ==================== 装饰器 ====================

def cache_get(ttl=300, prefix='', include_user=True, cache_instance=None):
    """
    缓存 GET 请求的装饰器
    
    Args:
        ttl: 缓存时间（秒），默认300秒（5分钟）
        prefix: 缓存前缀，用于分类管理
        include_user: 是否包含用户ID到缓存键
        cache_instance: 指定的缓存实例（默认使用 training_cache）
    
    Example:
        @cache_get(ttl=300, prefix='training_list')
        def get_trainings():
            return [...]
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            # 只缓存 GET 请求
            if request.method != 'GET':
                return func(*args, **kwargs)
            
            # 选择缓存实例
            instance = cache_instance if cache_instance else training_cache
            
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
        wrapper.clear_cache = lambda: instance.clear(prefix)
        wrapper.cache_key = lambda: instance._generate_key(prefix=prefix, include_user=include_user)
        
        return wrapper
    return decorator


def invalidate_cache_on_change(prefix='', patterns=None, clear_all_assignment=False):
    """
    在数据变更时清除缓存的装饰器（用于 POST, PUT, DELETE）
    
    Args:
        prefix: 缓存前缀
        patterns: 额外的缓存模式
        clear_all_assignment: 是否清除所有分配相关缓存
    
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
            
            # 判断是否成功
            is_success = True
            if result and isinstance(result, tuple):
                data = result[0] if len(result) > 0 else {}
                if isinstance(data, dict) and data.get('success') is False:
                    is_success = False
            elif result and isinstance(result, dict):
                if result.get('success') is False:
                    is_success = False
            
            # 如果成功，清除缓存
            if is_success:
                cleared = 0
                
                # 1. 清除指定前缀的缓存
                if prefix:
                    cleared += training_cache.clear(prefix)
                
                # 2. 清除额外模式
                if patterns:
                    if isinstance(patterns, str):
                        patterns = [patterns]
                    cleared += training_cache.clear_by_pattern(patterns)
                    cleared += exam_cache.clear_by_pattern(patterns)
                
                # 3. 清除所有分配相关缓存
                if clear_all_assignment:
                    cleared += clear_all_assignment_caches()
                
                logger.info(f"数据变更后清除缓存: {cleared} 条")
            
            return result
        return wrapper
    return decorator


# ==================== 新增：缓存预热（可选） ====================

def warmup_report_cache():
    """预热报表缓存（在数据变更后调用）"""
    # 这里可以添加预加载逻辑
    # 例如：异步加载常用报表数据到缓存
    pass


# ==================== 新增：监控接口（可选） ====================

def get_cache_status():
    """获取所有缓存实例的状态"""
    return {
        "training_cache": training_cache.get_stats(),
        "exam_cache": exam_cache.get_stats(),
        "user_cache": user_cache.get_stats(),
        "report_cache": report_cache.get_stats()
    }
