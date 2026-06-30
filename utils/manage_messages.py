# utils/manage_messages.py
"""
管理员消息盒子工具函数
用于记录系统自动触发的异常事件和操作日志
"""
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any
from services.db import get_supabase_admin

logger = logging.getLogger(__name__)

CATEGORY_TYPES = {
    # ===== 系统自动操作 =====
    'auto_extend': '自动延期',
    'auto_assign_exam': '自动分配考试',
    'auto_assign_training': '自动分配培训',
    
    # ===== 管理员操作 =====
    'admin_reset_sign': '重置签名',
    'admin_reset_exam': '重置考试',
    'admin_unassign': '取消分配',
    'admin_delete': '删除记录',
    'admin_restore': '恢复记录',
    'admin_import': '导入数据',
    'admin_push': '推送通知',
    
    # ===== 用户行为 =====
    'user_login': '用户登录',
    'user_signin': '用户签到',
    'user_submit_exam': '提交考试',
    'user_submit_interview': '提交访谈',
    'user_resign': '用户离职',
    'user_rehire': '用户复职',
    
    # ===== 系统警告 =====
    'exam_expired': '考试过期警告',
    'training_expired': '培训过期警告',
}


def log_admin_message(
    level: str,          # 'info', 'warning', 'error', 'success'
    category: str,       # 见 CATEGORY_TYPES
    title: str,
    content: str = None,
    related_id: int = None,
    related_type: str = None,  # 'exam', 'training', 'user', 'attendance', 'interview'
    created_by: str = None,
    metadata: dict = None      # 额外的结构化数据
) -> bool:
    """
    记录管理员消息（支持元数据）
    使用管理员客户端绕过 RLS
    """
    try:
        now = datetime.now(timezone.utc).isoformat()
        
        data = {
            "level": level,
            "category": category,
            "title": title,
            "content": content,
            "related_id": related_id,
            "related_type": related_type,
            "is_read": False,
            "created_at": now,
            "created_by": created_by,
            "metadata": metadata or {}
        }
        
        # 移除 None 值
        data = {k: v for k, v in data.items() if v is not None}
        
        admin_db = get_supabase_admin()
        result = admin_db.table("admin_messages").insert(data).execute()
        
        if result.data:
            logger.info(f"📬 管理员消息已记录: [{category}] {title[:50]}...")
            return True
        return False
    except Exception as e:
        # ✅ 不影响主流程，只记录日志
        logger.error(f"❌ 记录管理员消息失败: {e}")
        return False


# ==================== 用户相关 ====================

def log_user_login(user_id: str, user_name: str, email: str, ip: str = None, user_agent: str = None):
    """记录用户登录"""
    return log_admin_message(
        level="info",
        category="user_login",
        title=f"🔑 用户 {user_name} 已登录",
        content=f"用户从 {ip or '未知IP'} 登录系统" if ip else f"用户 {email} 已登录",
        related_type="user",
        created_by=user_id,
        metadata={
            "user_name": user_name,
            "email": email,
            "ip": ip,
            "user_agent": user_agent[:200] if user_agent else None
        }
    )

def log_user_logout(user_id: str, user_name: str, email: str):
    """记录用户登出"""
    return log_admin_message(
        level="info",
        category="user_login",
        title=f"🔑 用户 {user_name} 已登出",
        content=f"用户 {email} 已登出系统",
        related_type="user",
        created_by=user_id,
        metadata={"user_name": user_name, "email": email}
    )

def log_user_resign(user_id: str, user_name: str, admin_id: str):
    """记录用户离职"""
    return log_admin_message(
        level="warning",
        category="user_resign",
        title=f"👤 用户 {user_name} 已离职",
        content=f"用户已被标记为离职状态",
        related_type="user",
        created_by=admin_id,
        metadata={"user_id": user_id, "user_name": user_name}
    )


def log_user_rehire(user_id: str, user_name: str, admin_id: str):
    """记录用户复职"""
    return log_admin_message(
        level="success",
        category="user_rehire",
        title=f"👤 用户 {user_name} 已复职",
        content=f"用户已恢复为在职状态",
        related_type="user",
        created_by=admin_id,
        metadata={"user_id": user_id, "user_name": user_name}
    )


# ==================== 考试相关 ====================

def log_exam_auto_extend(exam_id: int, exam_title: str, new_end_time: str, triggered_by: str = None):
    """记录考试自动延期"""
    return log_admin_message(
        level="info",
        category="auto_extend",
        title=f"📅 考试「{exam_title}」有效期已自动延长",
        content=f"因学员签到触发，考试有效期已自动延长至 {new_end_time}",
        related_id=exam_id,
        related_type="exam",
        created_by=triggered_by,
        metadata={"new_end_time": new_end_time, "trigger": "sign_in"}
    )

def log_exam_assign_from_signin(exam_id: int, exam_title: str, user_id: str, user_name: str):
    """记录签到后自动分配考试消息"""
    return log_admin_message(
        level="success",
        category="exam_assign",
        title=f"✅ 考试「{exam_title}」已自动分配给学员 {user_name}",
        content=f"学员签到后触发自动分配",
        related_id=exam_id,
        related_type="exam",
        created_by=user_id
    )

def log_exam_auto_assign(exam_id: int, exam_title: str, user_id: str, user_name: str):
    """记录签到后自动分配考试"""
    return log_admin_message(
        level="success",
        category="auto_assign_exam",
        title=f"✅ 考试「{exam_title}」已自动分配给学员 {user_name}",
        content=f"学员完成培训签到后触发自动分配",
        related_id=exam_id,
        related_type="exam",
        created_by=user_id,
        metadata={"user_name": user_name, "trigger": "sign_in"}
    )


def log_exam_reset(exam_id: int, exam_title: str, user_id: str, user_name: str, admin_id: str, is_force: bool = False):
    """记录管理员重置考试（强制重推）"""
    level = "warning" if is_force else "info"
    action = "强制重推" if is_force else "重置"
    return log_admin_message(
        level=level,
        category="admin_reset_exam",
        title=f"🔄 学员 {user_name} 的考试「{exam_title}」已被{action}",
        content=f"管理员{action}考试，学员可以重新参加考试" + ("（有效期2小时）" if is_force else ""),
        related_id=exam_id,
        related_type="exam",
        created_by=admin_id,
        metadata={"user_id": user_id, "user_name": user_name, "is_force": is_force}
    )


def log_exam_unassign(exam_id: int, exam_title: str, user_id: str, user_name: str, admin_id: str):
    """记录取消考试分配"""
    return log_admin_message(
        level="warning",
        category="admin_unassign",
        title=f"📤 学员 {user_name} 的考试「{exam_title}」分配已被取消",
        content=f"管理员取消了该学员的考试分配",
        related_id=exam_id,
        related_type="exam",
        created_by=admin_id,
        metadata={"user_id": user_id, "user_name": user_name, "action": "unassign_exam"}
    )


def log_exam_delete(exam_id: int, exam_title: str, admin_id: str, is_permanent: bool = False):
    """记录删除考试"""
    level = "error" if is_permanent else "warning"
    action = "永久删除" if is_permanent else "软删除"
    return log_admin_message(
        level=level,
        category="admin_delete",
        title=f"🗑️ 考试「{exam_title}」已被{action}",
        content=f"管理员{action}了该考试" + ("（不可恢复）" if is_permanent else "（可恢复）"),
        related_id=exam_id,
        related_type="exam",
        created_by=admin_id,
        metadata={"exam_title": exam_title, "is_permanent": is_permanent}
    )


def log_exam_restore(exam_id: int, exam_title: str, admin_id: str):
    """记录恢复考试"""
    return log_admin_message(
        level="success",
        category="admin_restore",
        title=f"♻️ 考试「{exam_title}」已被恢复",
        content=f"管理员从回收站恢复了该考试",
        related_id=exam_id,
        related_type="exam",
        created_by=admin_id,
        metadata={"exam_title": exam_title}
    )


def log_result_delete(exam_id: int, exam_title: str, user_id: str, user_name: str, admin_id: str):
    """记录删除考试成绩"""
    return log_admin_message(
        level="warning",
        category="admin_delete",
        title=f"🗑️ 学员 {user_name} 的考试成绩已被删除",
        content=f"考试「{exam_title}」的成绩记录已删除，学员可以重新考试",
        related_id=exam_id,
        related_type="exam",
        created_by=admin_id,
        metadata={"user_id": user_id, "user_name": user_name}
    )


def log_admin_push_exam(exam_id: int, exam_title: str, user_count: int, admin_id: str, is_all: bool = False):
    """记录管理员推送考试"""
    scope = "全国" if is_all else f"{user_count}名学员"
    return log_admin_message(
        level="info",
        category="admin_push",
        title=f"📢 考试「{exam_title}」已推送给 {scope}",
        content=f"管理员推送考试通知，共 {user_count} 人收到",
        related_id=exam_id,
        related_type="exam",
        created_by=admin_id,
        metadata={"exam_title": exam_title, "user_count": user_count, "is_all": is_all}
    )


def log_import_exam(exam_id: int, exam_title: str, question_count: int, admin_id: str):
    """记录导入考试"""
    return log_admin_message(
        level="success",
        category="admin_import",
        title=f"📥 考试「{exam_title}」已导入",
        content=f"从Word文档导入，共 {question_count} 道题目",
        related_id=exam_id,
        related_type="exam",
        created_by=admin_id,
        metadata={"exam_title": exam_title, "question_count": question_count}
    )


def log_import_users(user_count: int, admin_id: str, file_name: str = None):
    """记录导入用户"""
    return log_admin_message(
        level="success",
        category="admin_import",
        title=f"📥 已导入 {user_count} 名用户",
        content=f"从 {file_name or 'Excel文件'} 导入" if file_name else f"共导入 {user_count} 名用户",
        related_type="user",
        created_by=admin_id,
        metadata={"user_count": user_count, "file_name": file_name}
    )

def log_force_reset_exam(exam_id: int, exam_title: str, user_id: str, user_name: str, admin_id: str):
    """
    记录强制重推考试消息
    """
    return log_admin_message(
        level="warning",
        category="admin_force_reset",
        title=f"⚡ 学员 {user_name} 的考试「{exam_title}」已被强制重推",
        content=f"管理员强制重推了该学员的考试，有效期2小时",
        related_id=exam_id,
        related_type="exam",
        created_by=admin_id,
        metadata={
            "user_id": user_id, 
            "user_name": user_name,
            "exam_title": exam_title,
            "action": "force_reset"
        }
    )


def log_cancel_force_reset_exam(exam_id: int, exam_title: str, user_id: str, user_name: str, admin_id: str):
    """
    记录取消强制重推考试消息
    """
    return log_admin_message(
        level="info",
        category="admin_force_reset",
        title=f"⚡ 学员 {user_name} 的考试「{exam_title}」已取消强制重推",
        content=f"管理员取消了该学员的强制重推资格",
        related_id=exam_id,
        related_type="exam",
        created_by=admin_id,
        metadata={
            "user_id": user_id, 
            "user_name": user_name,
            "exam_title": exam_title,
            "action": "cancel_force_reset"
        }
    )

# ==================== 培训相关 ====================

def log_training_auto_assign(training_id: int, training_name: str, user_id: str, user_name: str):
    """记录考试完成后自动分配培训"""
    return log_admin_message(
        level="success",
        category="auto_assign_training",
        title=f"✅ 培训「{training_name}」已自动分配给学员 {user_name}",
        content=f"学员完成绑定考试后触发自动分配",
        related_id=training_id,
        related_type="training",
        created_by=user_id,
        metadata={"user_name": user_name, "trigger": "exam_completed"}
    )


def log_signature_reset(training_id: int, training_name: str, user_id: str, user_name: str, admin_id: str):
    """记录管理员重置签名"""
    try:
        return log_admin_message(
            level="warning",
            category="admin_reset_sign",
            title=f"✍️ 学员 {user_name} 的签到签名已被重置",
            content=f"培训「{training_name}」的签到签名已清空，学员需要重新签名",
            related_id=training_id,
            related_type="training",
            created_by=admin_id,
            metadata={"user_id": user_id, "user_name": user_name, "action": "reset_signature"}
        )
    except Exception as e:
        logger.warning(f"记录重置签名消息失败: {e}")
        return False


def log_training_unassign(training_id: int, training_name: str, user_id: str, user_name: str, admin_id: str):
    """记录取消培训分配"""
    return log_admin_message(
        level="warning",
        category="admin_unassign",
        title=f"📤 学员 {user_name} 的培训「{training_name}」分配已被取消",
        content=f"管理员取消了该学员的培训分配，签到记录已删除",
        related_id=training_id,
        related_type="training",
        created_by=admin_id,
        metadata={"user_id": user_id, "user_name": user_name, "action": "unassign_training"}
    )


def log_admin_push_training(training_id: int, training_name: str, user_count: int, admin_id: str, is_all: bool = False):
    """记录管理员推送培训"""
    scope = "全国" if is_all else f"{user_count}名学员"
    return log_admin_message(
        level="info",
        category="admin_push",
        title=f"📢 培训「{training_name}」已推送给 {scope}",
        content=f"管理员推送培训通知，共 {user_count} 人收到",
        related_id=training_id,
        related_type="training",
        created_by=admin_id,
        metadata={"training_name": training_name, "user_count": user_count, "is_all": is_all}
    )


def log_training_expired_warning(training_id: int, training_name: str, expired_at: str):
    """记录培训过期警告"""
    return log_admin_message(
        level="warning",
        category="training_expired",
        title=f"⚠️ 培训「{training_name}」已过期",
        content=f"培训已于 {expired_at} 过期，有用户尝试签到",
        related_id=training_id,
        related_type="training",
        metadata={"expired_at": expired_at}
    )


def log_exam_expired_warning(exam_id: int, exam_title: str, expired_at: str, days_overdue: int = 0):
    """记录考试过期警告"""
    level = "error" if days_overdue > 7 else "warning"
    days_text = f"（已过期 {days_overdue} 天）" if days_overdue > 0 else ""
    return log_admin_message(
        level=level,
        category="exam_expired",
        title=f"⚠️ 考试「{exam_title}」已过期 {days_text}",
        content=f"考试已于 {expired_at} 过期，有学员签到触发了自动延期",
        related_id=exam_id,
        related_type="exam",
        metadata={"expired_at": expired_at, "days_overdue": days_overdue}
    )


# ==================== 查询函数 ====================

def get_unread_message_count() -> int:
    """获取未读消息数量（使用 admin 客户端）"""
    try:
        admin_db = get_supabase_admin()
        result = admin_db.table("admin_messages").select("id", count="exact").eq("is_read", False).execute()
        return result.count or 0
    except Exception as e:
        logger.error(f"获取未读数量失败: {e}")
        return 0


def get_recent_messages(limit: int = 20, unread_only: bool = False, category: str = None, level: str = None) -> list:
    """获取最近的消息列表"""
    try:
        admin_db = get_supabase_admin()
        query = admin_db.table("admin_messages").select("*").order("created_at", desc=True).limit(limit)
        
        if unread_only:
            query = query.eq("is_read", False)
        if category:
            query = query.eq("category", category)
        if level:
            query = query.eq("level", level)
        
        result = query.execute()
        return result.data or []
    except Exception as e:
        logger.error(f"获取消息列表失败: {e}")
        return []


def mark_message_read(message_id: int) -> bool:
    """标记单条消息为已读"""
    try:
        admin_db = get_supabase_admin()
        result = admin_db.table("admin_messages").update({"is_read": True}).eq("id", message_id).execute()
        return len(result.data or []) > 0
    except Exception as e:
        logger.error(f"标记消息已读失败: {e}")
        return False


def mark_all_messages_read() -> int:
    """标记所有消息为已读"""
    try:
        admin_db = get_supabase_admin()
        result = admin_db.table("admin_messages").update({"is_read": True}).eq("is_read", False).execute()
        return len(result.data or [])
    except Exception as e:
        logger.error(f"标记所有消息已读失败: {e}")
        return 0