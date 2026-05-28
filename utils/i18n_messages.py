# utils/i18n_messages.py

class I18nMessages:
    """国际化消息模板"""
    
    MESSAGES = {
        # 用户导入相关
        'user_already_exists': {
            'zh': '第{row}行: 用户 {name} 已存在',
            'en': 'Row {row}: User {name} already exists'
        },
        'insert_failed': {
            'zh': '第{row}行: 插入失败 - {error}',
            'en': 'Row {row}: Insert failed - {error}'
        },
        'country_not_allowed': {
            'zh': '第{row}行: 国家 {country} 不在您的权限范围内',
            'en': 'Row {row}: Country {country} not in your permission scope'
        },
        'country_wh_mismatch': {
            'zh': '第{row}行: 国家({country})与库房ID({wh_id})提取的国家不一致',
            'en': 'Row {row}: Country({country}) mismatch with warehouse ID({wh_id})'
        },
        'name_required': {
            'zh': '第{row}行: 姓名不能为空',
            'en': 'Row {row}: Name cannot be empty'
        },
        'admin_countries_required': {
            'zh': '第{row}行: {role}角色必须填写权限范围',
            'en': 'Row {row}: {role} role requires permission scope'
        },
        'admin_countries_invalid': {
            'zh': '第{row}行: 权限范围包含无权管理的国家: {countries}',
            'en': 'Row {row}: Permission scope contains unauthorized countries: {countries}'
        },
        'email_required': {
            'zh': '第{row}行: 邮箱不能为空',
            'en': 'Row {row}: Email cannot be empty'
        },
        'email_invalid': {
            'zh': '第{row}行: 邮箱格式无效: {email}',
            'en': 'Row {row}: Invalid email format: {email}'
        },
        'role_invalid': {
            'zh': '第{row}行: 角色 {role} 无效',
            'en': 'Row {row}: Invalid role: {role}'
        },
        # 库房导入相关
        'wh_id_required': {
            'zh': '第{row}行: 库房ID不能为空',
            'en': 'Row {row}: Warehouse ID cannot be empty'
        },
        'wh_id_exists': {
            'zh': '第{row}行: 库房ID {wh_id} 已存在',
            'en': 'Row {row}: Warehouse ID {wh_id} already exists'
        },
    }
    
    @classmethod
    def get_message(cls, code, lang='zh', **params):
        """获取翻译后的消息字符串"""
        template = cls.MESSAGES.get(code, {}).get(lang, code)
        for key, value in params.items():
            template = template.replace(f'{{{key}}}', str(value))
        return template
    
    @classmethod
    def format_error(cls, row, code, **params):
        """格式化错误对象，供前端使用"""
        # ✅ 将 row 添加到 params 中
        params['row'] = row
        return {
            "code": code,
            "row": row,
            "params": params
        }
    
    @classmethod
    def format_error_list(cls, errors):
        """格式化错误列表，用于返回给前端"""
        result = []
        for error in errors:
            if isinstance(error, dict):
                result.append(error)
            else:
                result.append({"code": "unknown_error", "message": str(error)})
        return result