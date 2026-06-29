# services/db.py
import os
import time
import logging
import traceback
import httpx
from functools import wraps
from supabase import create_client, Client
from config import Config
from datetime import datetime, timezone


logger = logging.getLogger(__name__)

_supabase_client = None
_supabase_admin_client = None
supabase = None

# ============================================================
# 1. 超时配置
# ============================================================
timeout_config = httpx.Timeout(
    connect=30.0,   # 连接超时 30秒
    read=60.0,      # 读取超时 60秒（增加，应对大数据量查询）
    write=30.0,     # 写入超时 30秒
    pool=10.0       # 连接池超时
)

# 创建自定义 HTTP 客户端
http_client = httpx.Client(timeout=timeout_config)

# 备用客户端（更长超时，用于大数据量查询）
long_timeout_config = httpx.Timeout(
    connect=60.0,
    read=120.0,
    write=60.0,
    pool=20.0
)
http_client_long = httpx.Client(timeout=long_timeout_config)


# ============================================================
# 2. 重试装饰器
# ============================================================
def retry_on_timeout(max_retries=3, delay=2, backoff=True):
    """
    超时重试装饰器
    
    Args:
        max_retries: 最大重试次数
        delay: 初始延迟（秒）
        backoff: 是否使用指数退避
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.TimeoutException) as e:
                    last_exception = e
                    if attempt == max_retries - 1:
                        logger.error(f"重试 {max_retries} 次后仍失败: {e}")
                        raise
                    
                    wait_time = delay * (2 ** attempt) if backoff else delay
                    logger.warning(f"超时重试 {attempt + 1}/{max_retries}，等待 {wait_time:.1f}秒: {e}")
                    time.sleep(wait_time)
                except Exception as e:
                    # 非超时异常直接抛出
                    raise
            return None
        return wrapper
    return decorator


# ============================================================
# 3. Supabase 客户端（带超时配置）
# ============================================================
def get_supabase():
    """获取 Supabase 客户端（带超时配置）"""
    global supabase, http_client
    if supabase is None:
        Config.check()
        supabase = create_client(Config.SUPABASE_URL, Config.SUPABASE_KEY)
        # 设置超时（兼容不同版本）
        try:
            if hasattr(supabase, 'postgrest') and hasattr(supabase.postgrest, 'session'):
                supabase.postgrest.session.timeout = 30.0
        except Exception as e:
            logger.debug(f"设置超时失败（可忽略）: {e}")
    return supabase


def get_supabase_admin():
    """获取管理员客户端（使用 service_role key，绕过 RLS）"""
    supabase_url = Config.SUPABASE_URL
    supabase_service_key = os.environ.get('SUPABASE_SERVICE_KEY', Config.SUPABASE_KEY)
    client = create_client(supabase_url, supabase_service_key)
    # 设置超时
    try:
        if hasattr(client, 'postgrest') and hasattr(client.postgrest, 'session'):
            client.postgrest.session.timeout = 30.0
    except Exception as e:
        logger.debug(f"设置超时失败（可忽略）: {e}")
    return client

# ============================================================
# 4. 安全查询包装器
# ============================================================
class SafeQuery:
    """安全查询包装器，自动处理超时和重试"""
    
    def __init__(self, table_name, use_admin=False, timeout=None):
        self.table_name = table_name
        self.use_admin = use_admin
        self.timeout = timeout
        self._query = None  # 存储查询构建器
        self._client = None
        self._executed = False
    
    def _get_client(self):
        if self.use_admin:
            return get_supabase_admin()
        return get_supabase()
    
    def _get_table(self):
        client = self._get_client()
        if self.timeout:
            client.postgrest.session.timeout = self.timeout
        self._client = client
        return client.table(self.table_name)
    
    # ========== 链式查询方法 ==========
    def select(self, *args, **kwargs):
        """SELECT 查询"""
        self._query = self._get_table().select(*args, **kwargs)
        return self
    
    def insert(self, data, **kwargs):
        """INSERT 查询"""
        self._query = self._get_table().insert(data, **kwargs)
        return self
    
    def update(self, data, **kwargs):
        """UPDATE 查询"""
        self._query = self._get_table().update(data, **kwargs)
        return self
    
    def delete(self, **kwargs):
        """DELETE 查询"""
        self._query = self._get_table().delete(**kwargs)
        return self
    
    # ========== 过滤方法 ==========
    def eq(self, column, value):
        self._query = self._query.eq(column, value)
        return self
    
    def neq(self, column, value):
        self._query = self._query.neq(column, value)
        return self
    
    def gt(self, column, value):
        self._query = self._query.gt(column, value)
        return self
    
    def gte(self, column, value):
        self._query = self._query.gte(column, value)
        return self
    
    def lt(self, column, value):
        self._query = self._query.lt(column, value)
        return self
    
    def lte(self, column, value):
        self._query = self._query.lte(column, value)
        return self
    
    def like(self, column, value):
        self._query = self._query.like(column, value)
        return self
    
    def ilike(self, column, value):
        self._query = self._query.ilike(column, value)
        return self
    
    def is_(self, column, value):
        self._query = self._query.is_(column, value)
        return self
    
    def not_(self, column, value):
        self._query = self._query.not_(column, value)
        return self
    
    def in_(self, column, values):
        self._query = self._query.in_(column, values)
        return self
    
    def order(self, column, desc=True):
        self._query = self._query.order(column, desc=desc)
        return self
    
    def limit(self, value):
        self._query = self._query.limit(value)
        return self
    
    def range(self, start, end):
        self._query = self._query.range(start, end)
        return self
    
    def maybe_single(self):
        self._query = self._query.maybe_single()
        return self
    
    def single(self):
        self._query = self._query.single()
        return self
    
    # ========== not_.is_ 特殊处理 ==========
    class Not:
        def __init__(self, parent):
            self._parent = parent
        
        def is_(self, column, value):
            self._parent._query = self._parent._query.not_.is_(column, value)
            return self._parent
    
    @property
    def not_(self):
        return self.Not(self)
    
    # ========== 执行方法（带重试）==========
    @retry_on_timeout(max_retries=3, delay=2)
    def execute(self):
        """执行查询（带重试）"""
        # 只检查 _query 是否存在
        if self._query is None:
            logger.error(f"SafeQuery.execute() 被调用但 _query 为 None")
            logger.error(f"调用栈: {traceback.format_stack()}")
            raise ValueError("请先调用 select/insert/update/delete 方法")
        
        try:
            result = self._query.execute()
            if result is None:
                return type('EmptyResult', (), {'data': [], 'count': 0})()
            return result
        except Exception as e:
            logger.error(f"查询执行失败: {e}")
            raise
        finally:
            self._query = None

def safe_table(table_name, use_admin=False, timeout=None):
    """
    获取安全查询包装器
    
    Args:
        table_name: 表名
        use_admin: 是否使用管理员客户端
        timeout: 自定义超时时间
    
    Returns:
        SafeQuery: 安全查询包装器
    
    Example:
        # 普通查询
        result = safe_table('users').select('*').eq('id', user_id)
        
        # 带超时
        result = safe_table('users', timeout=120).select('*')
        
        # 管理员查询
        result = safe_table('trainings', use_admin=True).select('*')
    """
    # 为调度器增加默认超时
    if timeout is None:
        timeout = 120  # 默认 120 秒
    return SafeQuery(table_name, use_admin, timeout)


# ============================================================
# 5. 批量查询优化工具
# ============================================================
def batch_query(table_name, ids, select_fields='*', id_field='id', use_admin=False, timeout=None):
    """
    批量查询（自动分批，避免 IN 查询过长）
    
    Args:
        table_name: 表名
        ids: ID 列表
        select_fields: 查询字段
        id_field: ID 字段名
        use_admin: 是否使用管理员客户端
        timeout: 自定义超时时间
    
    Returns:
        list: 查询结果列表
    """
    if not ids:
        return []
    
    # 分批大小（Supabase 对 IN 查询有限制）
    BATCH_SIZE = 50
    results = []
    
    client = get_supabase_admin() if use_admin else get_supabase()
    if timeout:
        client.postgrest.session.timeout = timeout
    
    for i in range(0, len(ids), BATCH_SIZE):
        batch_ids = ids[i:i + BATCH_SIZE]
        try:
            query = client.table(table_name).select(select_fields).in_(id_field, batch_ids)
            response = query.execute()
            if response.data:
                results.extend(response.data)
        except (httpx.ConnectTimeout, httpx.ReadTimeout) as e:
            logger.warning(f"批量查询超时 (批次 {i//BATCH_SIZE + 1}): {e}")
            # 重试一次
            try:
                time.sleep(1)
                query = client.table(table_name).select(select_fields).in_(id_field, batch_ids)
                response = query.execute()
                if response.data:
                    results.extend(response.data)
            except Exception as retry_e:
                logger.error(f"批量查询重试失败: {retry_e}")
                continue
    
    return results


# ============================================================
# 6. 工具函数
# ============================================================
def get_current_utc():
    """获取当前 UTC 时间"""
    return datetime.now(timezone.utc).isoformat()


def create_exam(data):
    """创建考试（示例）"""
    db = get_supabase()
    return db.table("exams").insert({
        "start_time": data.get('start_time'),
        "end_time": data.get('end_time'),
        "created_at": get_current_utc()
    }).execute()


# ============================================================
# 7. 兼容原有接口
# ============================================================
# 为了保持与现有代码的兼容性，保留原有函数名
# 但实际使用时建议使用 safe_table() 或 batch_query()

__all__ = [
    'get_supabase',
    'get_supabase_admin',
    'get_supabase_with_timeout',
    'get_current_utc',
    'create_exam',
    'safe_table',
    'batch_query',
    'retry_on_timeout',
    'SafeQuery',
]