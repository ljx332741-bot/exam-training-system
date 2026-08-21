# services/privacy.py
"""
隐私声明服务层
"""
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List

from services.db import get_supabase, get_supabase_admin

logger = logging.getLogger(__name__)


class PrivacyService:
    """隐私声明服务"""
    
    @staticmethod
    def get_active_agreement() -> Optional[Dict[str, Any]]:
        """获取当前有效的隐私声明"""
        db = get_supabase_admin()
        try:
            res = db.table("privacy_agreements") \
                .select("*") \
                .eq("is_active", True) \
                .order("created_at", desc=True) \
                .limit(1) \
                .execute()
            
            if res and hasattr(res, 'data') and res.data and len(res.data) > 0:
                return res.data[0]
            return None
        except Exception as e:
            logger.error(f"获取活跃声明失败: {e}")
            return None
    
    @staticmethod
    def get_agreement_by_id(agreement_id: int) -> Optional[Dict[str, Any]]:
        """根据ID获取声明"""
        db = get_supabase_admin()
        try:
            res = db.table("privacy_agreements") \
                .select("*") \
                .eq("id", agreement_id) \
                .execute()
            
            if res and hasattr(res, 'data') and res.data and len(res.data) > 0:
                return res.data[0]
            return None
        except Exception as e:
            logger.error(f"根据ID获取声明失败: {e}")
            return None
    
    @staticmethod
    def get_agreement_by_version(version: str) -> Optional[Dict[str, Any]]:
        """根据版本号获取声明"""
        db = get_supabase_admin()
        try:
            res = db.table("privacy_agreements") \
                .select("*") \
                .eq("version", version) \
                .execute()
            
            if res and hasattr(res, 'data') and res.data and len(res.data) > 0:
                return res.data[0]
            return None
        except Exception as e:
            logger.error(f"根据版本获取声明失败: {e}")
            return None
    
    @staticmethod
    def create_agreement(
        version: str,
        title: str,
        content: str,
        created_by: str,
        changelog: str = ""
        ) -> Dict[str, Any]:
        """创建新版本的隐私声明"""
        db = get_supabase_admin()
        
        # 1. 检查版本号是否已存在
        try:
            existing = PrivacyService.get_agreement_by_version(version)
            if existing:
                raise Exception(f"版本号 {version} 已存在")
        except Exception as e:
            if "版本号" in str(e):
                raise
            # 其他异常忽略
        
        # 2. 将旧版本设为非活跃
        db.table("privacy_agreements") \
            .update({"is_active": False}) \
            .eq("is_active", True) \
            .execute()
        
        # 3. 创建新版本
        now = datetime.now(timezone.utc).isoformat()
        data = {
            "version": version,
            "title": title,
            "content": content,
            "is_active": True,
            "created_by": created_by,
            "updated_by": created_by,
            "changelog": changelog,
            "created_at": now,
            "updated_at": now
        }
        
        res = db.table("privacy_agreements").insert(data).execute()
        if not res or not hasattr(res, 'data') or not res.data:
            raise Exception("创建隐私声明失败")
        
        logger.info(f"隐私声明新版本创建: {version}, 创建人: {created_by}")
        return res.data[0]
    
    @staticmethod
    def update_agreement(
        agreement_id: int,
        title: str,
        content: str,
        updated_by: str,
        changelog: str = ""
        ) -> Dict[str, Any]:
        """更新隐私声明（不创建新版本）"""
        db = get_supabase_admin()
        now = datetime.now(timezone.utc).isoformat()
        
        data = {
            "title": title,
            "content": content,
            "updated_by": updated_by,
            "updated_at": now,
            "changelog": changelog if changelog else None
        }
        
        res = db.table("privacy_agreements") \
            .update(data) \
            .eq("id", agreement_id) \
            .execute()
        
        if not res or not hasattr(res, 'data') or not res.data:
            raise Exception("更新隐私声明失败")
        
        logger.info(f"隐私声明更新: ID={agreement_id}, 更新人: {updated_by}")
        return res.data[0]
    
    @staticmethod
    def get_all_agreements(limit: int = 50, offset: int = 0) -> Dict[str, Any]:
        """获取所有隐私声明版本（分页）"""
        db = get_supabase_admin()
        
        try:
            # 获取总数
            count_res = db.table("privacy_agreements") \
                .select("id", count="exact") \
                .execute()
            total = count_res.count if count_res and hasattr(count_res, 'count') else 0
        except:
            total = 0
        
        try:
            res = db.table("privacy_agreements") \
                .select("*") \
                .order("created_at", desc=True) \
                .range(offset, offset + limit - 1) \
                .execute()
            data = res.data if res and hasattr(res, 'data') else []
        except:
            data = []
        
        # 获取创建人姓名
        if data:
            creator_ids = [item.get('created_by') for item in data if item.get('created_by')]
            if creator_ids:
                try:
                    user_res = db.table("users").select("id, name_en").in_("id", creator_ids).execute()
                    if user_res and hasattr(user_res, 'data') and user_res.data:
                        user_map = {u['id']: u.get('name_en', '') for u in user_res.data}
                        for item in data:
                            item['created_by_name'] = user_map.get(item.get('created_by'), '')
                except:
                    pass
        
        return {
            "data": data,
            "total": total
        }
    
    @staticmethod
    def has_user_acknowledged(user_id: str, agreement_id: int) -> bool:
        """检查用户是否已确认某个版本的声明"""
        db = get_supabase_admin()
        try:
            res = db.table("user_agreement_acks") \
                .select("id") \
                .eq("user_id", user_id) \
                .eq("agreement_id", agreement_id) \
                .execute()
            return res and hasattr(res, 'data') and res.data and len(res.data) > 0
        except:
            return False
    
    @staticmethod
    def acknowledge_agreement(
        user_id: str,
        agreement_id: int,
        ip_address: str = None,
        user_agent: str = None
        ) -> Dict[str, Any]:
        """记录用户确认声明，同时更新 users 表"""
        db = get_supabase_admin()
        now = datetime.now(timezone.utc).isoformat()
        
        # 检查是否已确认
        if PrivacyService.has_user_acknowledged(user_id, agreement_id):
            return {"success": True, "already_acknowledged": True}
        
        # ✅ 1. 插入签署记录到 user_agreement_acks
        ack_data = {
            "user_id": user_id,
            "agreement_id": agreement_id,
            "acknowledged_at": now,
            "ip_address": ip_address,
            "user_agent": user_agent
        }
        
        ack_res = db.table("user_agreement_acks").insert(ack_data).execute()
        if not ack_res or not hasattr(ack_res, 'data') or not ack_res.data:
            raise Exception("记录确认失败")
        
        # ✅ 2. 同时更新 users 表（关键修复）
        update_res = db.table("users").update({
            "privacy_acknowledged_at": now,
            "privacy_agreement_id": agreement_id,
            "updated_at": now
        }).eq("id", user_id).execute()
        
        if not update_res or not hasattr(update_res, 'data') or not update_res.data:
            # 如果更新失败，回滚（删除刚插入的签署记录）
            db.table("user_agreement_acks").delete().eq("user_id", user_id).eq("agreement_id", agreement_id).execute()
            raise Exception("更新用户表失败")
        
        logger.info(f"用户确认隐私声明: user={user_id}, agreement={agreement_id}, 已同步更新 users 表")
        
        return {"success": True, "acknowledged": True}

    @staticmethod
    def get_user_acknowledgments(user_id: str) -> List[Dict[str, Any]]:
        """获取用户所有的确认记录"""
        db = get_supabase_admin()
        try:
            res = db.table("user_agreement_acks") \
                .select("*, privacy_agreements(version, title, created_at)") \
                .eq("user_id", user_id) \
                .order("acknowledged_at", desc=True) \
                .execute()
            return res.data if res and hasattr(res, 'data') else []
        except:
            return []
    
    @staticmethod
    def check_user_needs_acknowledgment(user_id: str) -> Dict[str, Any]:
        """
        检查用户是否需要确认隐私声明
        Returns: {
            "needs_acknowledgment": bool,
            "agreement": {...} or None
        }
        """
        active = PrivacyService.get_active_agreement()
        if not active:
            return {"needs_acknowledgment": False, "agreement": None}
        
        has_ack = PrivacyService.has_user_acknowledged(user_id, active["id"])
        
        return {
            "needs_acknowledgment": not has_ack,
            "agreement": active if not has_ack else None
        }


# 便捷函数
def get_active_agreement():
    return PrivacyService.get_active_agreement()

def check_user_needs_acknowledgment(user_id: str):
    return PrivacyService.check_user_needs_acknowledgment(user_id)

def acknowledge_agreement(user_id: str, agreement_id: int, ip_address: str = None, user_agent: str = None):
    return PrivacyService.acknowledge_agreement(user_id, agreement_id, ip_address, user_agent)