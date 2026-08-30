# utils/training_helpers.py
import json
import logging
from typing import List, Union, Optional, Any
from datetime import datetime, timezone 
from services.db import get_supabase, get_supabase_admin

logger = logging.getLogger(__name__)

def _save_country_template(db, training_id, country_code, header_template):
    """内部辅助函数：保存培训的国家表头模板"""
    
    # 检查是否已存在该国家的模板记录
    check_res = db.table("training_country_templates")\
        .select("id")\
        .eq("training_id", training_id)\
        .eq("country", country_code)\
        .execute()
    
    if check_res.data and len(check_res.data) > 0:
        # 更新现有记录
        db.table("training_country_templates")\
            .update({
                "header_template": header_template, 
                "updated_at": datetime.now(timezone.utc).isoformat()
            })\
            .eq("id", check_res.data[0]['id'])\
            .execute()
    else:
        # 插入新记录
        db.table("training_country_templates")\
            .insert({
                "training_id": training_id, 
                "country": country_code, 
                "header_template": header_template
            })\
            .execute()

def get_training_country_template(training_id, country_code):
    """获取特定培训+国家的表头模板，若不存在则返回 None"""
    db = get_supabase()
    res = db.table("training_country_templates").select("header_template").eq("training_id", training_id).eq("country", country_code).maybe_single().execute()
    return res.data['header_template'] if res.data else None

def set_training_country_template(training_id, country_code, template):
    """保存或更新培训+国家的表头模板"""
    db = get_supabase()
    existing = db.table("training_country_templates").select("id").eq("training_id", training_id).eq("country", country_code).maybe_single().execute()
    if existing.data:
        db.table("training_country_templates").update({"header_template": template, "updated_at": "now()"}).eq("id", existing.data['id']).execute()
    else:
        db.table("training_country_templates").insert({"training_id": training_id, "country": country_code, "header_template": template}).execute()

def get_training_country_templates_status(training_id):
    """获取培训下所有国家的模板存在情况，并判断是否所有国家模板一致（仅当所有国家都有模板且内容相同时返回一致标志）"""
    db = get_supabase()
    # 获取该培训所有签到学员的国家（去重）
    att_res = db.table("training_attendances").select("users!inner(country)").eq("training_id", training_id).execute()
    country_codes = set([att['users']['country'] for att in att_res.data if att['users'].get('country')])
    if not country_codes:
        return {"countries": [], "all_consistent": True, "any_template_exists": False}
    # 获取已有模板
    templates_res = db.table("training_country_templates").select("country, header_template").eq("training_id", training_id).execute()
    templates_map = {t['country']: t['header_template'] for t in templates_res.data}
    consistent = True
    first_template = None
    for c in country_codes:
        if c in templates_map:
            if first_template is None:
                first_template = templates_map[c]
            elif templates_map[c] != first_template:
                consistent = False
                break
        else:
            # 若某个国家没有模板，且其他有模板，则不一致
            if templates_map:
                consistent = False
                break
    # 若存在至少一个国家有模板，则 any_template_exists 为 True
    any_exists = len(templates_map) > 0
    return {
        "countries": list(country_codes),
        "all_consistent": consistent,
        "any_template_exists": any_exists,
        "templates": templates_map
    }

# utils/training_helpers.py - 培训多国家支持工具函数

def parse_training_countries(training: Union[dict, None]) -> List[str]:
    """
    解析培训的国家列表，支持新旧格式
    
    新格式: {"countries": ["NP", "LK"]}
    旧格式: {"country": "NP"} 或 {"country": ["NP", "LK"]}
    
    Args:
        training: 培训数据字典
    
    Returns:
        list: 国家代码列表，如果没有则返回空列表
    """
    if not training:
        return []
    
    # 1. 优先检查 countries 字段（新格式，多国家）
    countries_data = training.get('countries')
    if countries_data is not None:
        return _parse_countries_field(countries_data)
    
    # 2. 降级：使用旧的 country 字段
    old_country = training.get('country')
    if old_country is not None:
        return _parse_countries_field(old_country)
    
    return []


def _parse_countries_field(field_data: Any) -> List[str]:
    """
    解析国家字段数据（内部函数）
    支持: 字符串、JSON字符串、列表
    """
    if not field_data:
        return []
    
    # 如果是列表
    if isinstance(field_data, list):
        return [str(c).strip() for c in field_data if c]
    
    # 如果是字符串
    if isinstance(field_data, str):
        # 尝试解析 JSON
        try:
            parsed = json.loads(field_data)
            if isinstance(parsed, list):
                return [str(c).strip() for c in parsed if c]
            elif isinstance(parsed, str):
                return [parsed.strip()] if parsed.strip() else []
            else:
                return []
        except json.JSONDecodeError:
            # 不是 JSON，可能是单个国家或逗号分隔
            if ',' in field_data:
                # 逗号分隔 "NP,LK"
                return [c.strip() for c in field_data.split(',') if c.strip()]
            else:
                return [field_data.strip()] if field_data.strip() else []
    
    return []


def get_training_primary_country(training: Union[dict, None]) -> Optional[str]:
    """
    获取培训的主要国家（第一个国家），用于向后兼容
    
    Args:
        training: 培训数据字典
    
    Returns:
        str: 第一个国家代码，如果没有则返回 None
    """
    countries = parse_training_countries(training)
    return countries[0] if countries else None


def training_has_country(training: Union[dict, None], country_code: str) -> bool:
    """
    检查培训是否包含指定的国家
    
    Args:
        training: 培训数据字典
        country_code: 国家代码
    
    Returns:
        bool: True 如果培训包含该国家
    """
    if not training or not country_code:
        return False
    countries = parse_training_countries(training)
    return country_code in countries


def training_matches_any_country(training: Union[dict, None], country_codes: List[str]) -> bool:
    """
    检查培训是否匹配任意一个国家
    
    Args:
        training: 培训数据字典
        country_codes: 国家代码列表
    
    Returns:
        bool: True 如果培训包含任意一个国家
    """
    if not training or not country_codes:
        return False
    countries = parse_training_countries(training)
    return any(c in countries for c in country_codes)


def filter_trainings_by_country(trainings: List[dict], allowed_countries: Optional[List[str]]) -> List[dict]:
    """
    根据国家权限过滤培训列表
    
    Args:
        trainings: 培训列表
        allowed_countries: 允许的国家代码列表（None 表示无限制）
    
    Returns:
        list: 过滤后的培训列表
    """
    if allowed_countries is None:
        return trainings
    
    if not allowed_countries:
        return []
    
    result = []
    for training in trainings:
        countries = parse_training_countries(training)
        if any(c in allowed_countries for c in countries):
            result.append(training)
    
    return result


def get_training_countries_display(training: Union[dict, None], 
                                   allowed_countries: Optional[List[str]] = None,
                                   lang: str = 'zh') -> str:
    """
    获取培训的国家显示字符串（带权限过滤）
    
    Args:
        training: 培训数据字典
        allowed_countries: 允许的国家列表（用于过滤）
        lang: 语言 'zh' 或 'en'
    
    Returns:
        str: 显示字符串
    """
    countries = parse_training_countries(training)
    
    if not countries:
        return '-'
    
    if allowed_countries is not None:
        countries = [c for c in countries if c in allowed_countries]
        if not countries:
            return '-'
    
    return ', '.join(countries)


def normalize_training_countries(countries_input: Union[str, List[str], None]) -> Optional[str]:
    """
    规范化国家数据为 JSON 字符串存储格式
    
    Args:
        countries_input: 国家数据（字符串、列表或 None）
    
    Returns:
        str: JSON 字符串，或 None
    """
    if not countries_input:
        return None
    
    if isinstance(countries_input, list):
        # 过滤空值
        cleaned = [c.strip() for c in countries_input if c and c.strip()]
        if not cleaned:
            return None
        return json.dumps(cleaned)
    
    if isinstance(countries_input, str):
        # 尝试解析 JSON
        try:
            parsed = json.loads(countries_input)
            if isinstance(parsed, list):
                cleaned = [c.strip() for c in parsed if c and c.strip()]
                return json.dumps(cleaned) if cleaned else None
            elif isinstance(parsed, str) and parsed.strip():
                return json.dumps([parsed.strip()])
        except json.JSONDecodeError:
            # 普通字符串
            if ',' in countries_input:
                # 逗号分隔
                cleaned = [c.strip() for c in countries_input.split(',') if c.strip()]
                return json.dumps(cleaned) if cleaned else None
            elif countries_input.strip():
                return json.dumps([countries_input.strip()])
    
    return None


def get_training_country_for_query(training: Union[dict, None]) -> Optional[str]:
    """
    获取培训的国家用于数据库查询（返回第一个国家）
    主要用于 WHERE 条件查询
    
    Args:
        training: 培训数据字典
    
    Returns:
        str: 第一个国家代码，或 None
    """
    return get_training_primary_country(training)


# ============================================================
# 向后兼容的别名
# ============================================================

def parse_country_list(training_country):
    """
    向后兼容的别名函数（用于 api_training.py）
    建议逐步迁移到 parse_training_countries
    """
    if isinstance(training_country, dict):
        return parse_training_countries(training_country)
    
    # 直接传入字符串或列表的情况
    if isinstance(training_country, list):
        return training_country
    
    if isinstance(training_country, str):
        try:
            parsed = json.loads(training_country)
            if isinstance(parsed, list):
                return parsed
            return [training_country]
        except json.JSONDecodeError:
            if ',' in training_country:
                return [c.strip() for c in training_country.split(',') if c.strip()]
            return [training_country] if training_country else []
    
    return []

def calculate_dynamic_status(start_time, end_time):
    """
    计算培训的动态状态（工具函数，供其他地方调用）
    如果数据库已有 dynamic_status 字段，可以直接读取
    """
    if not start_time or not end_time:
        return 'draft'
    try:
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        start_dt = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
        end_dt = datetime.fromisoformat(end_time.replace('Z', '+00:00'))
        if now < start_dt:
            return 'pending'
        elif now > end_dt:
            return 'closed'
        else:
            return 'active'
    except Exception:
        return 'draft'

"""
及格分数同步工具模块
确保 exams 表和 training_exam_bindings 表的 pass_score 保持一致
"""
logger = logging.getLogger(__name__)

def sync_pass_score_to_bindings(exam_id, new_pass_score, db=None):
    """
    当考试自身的及格分数更新时，同步更新所有关联的绑定记录
    
    Args:
        exam_id: 考试ID
        new_pass_score: 新的及格分数
        db: 数据库连接（可选，如果没有则新建）
    
    Returns:
        int: 更新的绑定记录数量
    """
    if db is None:
        db = get_supabase_admin()
    
    try:
        # 更新所有未删除的绑定记录
        result = db.table("training_exam_bindings")\
            .update({
                "pass_score": new_pass_score,
                "updated_at": datetime.now(timezone.utc).isoformat()
            })\
            .eq("exam_id", exam_id)\
            .is_("deleted_at", "null")\
            .execute()
        
        updated_count = len(result.data) if result.data else 0
        logger.info(f"✅ 同步及格分数: 考试 {exam_id} -> {new_pass_score}, 更新了 {updated_count} 条绑定记录")
        return updated_count
    except Exception as e:
        logger.error(f"❌ 同步及格分数失败: exam_id={exam_id}, error={e}")
        raise


def sync_binding_pass_score_to_exam(binding_id, new_pass_score, db=None):
    """
    当绑定关系的及格分数更新时，同步更新考试自身的及格分数
    但仅在考试没有被其他培训绑定时才同步（避免意外覆盖）
    
    Args:
        binding_id: 绑定关系ID
        new_pass_score: 新的及格分数
        db: 数据库连接（可选）
    
    Returns:
        dict: 包含同步结果的字典
    """
    if db is None:
        db = get_supabase_admin()
    
    try:
        # 1. 获取绑定信息
        binding_res = db.table("training_exam_bindings")\
            .select("exam_id, training_id")\
            .eq("id", binding_id)\
            .is_("deleted_at", "null")\
            .maybe_single()\
            .execute()
        
        if not binding_res.data:
            return {"synced": False, "reason": "绑定关系不存在"}
        
        exam_id = binding_res.data['exam_id']
        
        # 2. 检查该考试是否被多个培训绑定
        count_res = db.table("training_exam_bindings")\
            .select("id", count="exact")\
            .eq("exam_id", exam_id)\
            .is_("deleted_at", "null")\
            .execute()
        
        binding_count = count_res.count or 0
        
        # 3. 如果考试只被当前这一个培训绑定，同步更新 exam 表
        if binding_count <= 1:
            db.table("exams")\
                .update({
                    "pass_score": new_pass_score,
                    "updated_at": datetime.now(timezone.utc).isoformat()
                })\
                .eq("id", exam_id)\
                .execute()
            logger.info(f"✅ 绑定及格分数反向同步到考试: exam_id={exam_id}, pass_score={new_pass_score}")
            return {"synced": True, "reason": f"考试仅被 {binding_count} 个培训绑定，已同步更新"}
        else:
            logger.info(f"⏭️ 跳过反向同步: 考试 {exam_id} 被 {binding_count} 个培训绑定，不覆盖考试默认分数")
            return {"synced": False, "reason": f"考试被 {binding_count} 个培训绑定，不自动覆盖默认分数"}
            
    except Exception as e:
        logger.error(f"❌ 反向同步及格分数失败: binding_id={binding_id}, error={e}")
        return {"synced": False, "reason": str(e)}

