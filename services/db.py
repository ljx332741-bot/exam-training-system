# services/db.py
import httpx
import os
from supabase import create_client, Client
from config import Config

supabase = None

def get_supabase():
    global supabase
    if supabase is None:
        Config.check()
        supabase = create_client(Config.SUPABASE_URL, Config.SUPABASE_KEY)
        # 设置超时时间为 30 秒（可根据需要调整）
        supabase.postgrest.session.timeout = 30.0
        # 如果需要存储超时，可同时设置：
        supabase.storage.session.timeout = 30.0
    return supabase

def get_supabase_admin():
    """获取管理员客户端（使用 service_role key，绕过 RLS）"""
    supabase_url = Config.SUPABASE_URL
    supabase_service_key = os.environ.get('SUPABASE_SERVICE_KEY', Config.SUPABASE_KEY)
    return create_client(supabase_url, supabase_service_key)