# services/db.py
import httpx
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