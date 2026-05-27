# utils/training_helpers.py
from services.db import get_supabase
from utils.common import match_country_code  # 假设存在

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