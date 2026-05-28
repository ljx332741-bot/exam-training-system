# utils/i18n_messages.py
class I18nMessages:
    """国际化消息管理"""
    
    MESSAGES = {
        "user_already_exists": {
            "zh": "第{row}行: 用户 {name} 已存在",
            "en": "Row {row}: User {name} already exists"
        },
        "insert_failed": {
            "zh": "第{row}行: 插入失败 - {error}",
            "en": "Row {row}: Insert failed - {error}"
        },
        "country_not_allowed": {
            "zh": "第{row}行: 国家 {country} 不在您的权限范围内",
            "en": "Row {row}: Country {country} is not within your permission scope"
        },
        "country_wh_mismatch": {
            "zh": "第{row}行: 国家({country})与库房ID({wh_id})提取的国家({wh_country})不一致",
            "en": "Row {row}: Country({country}) does not match warehouse ID({wh_id}) extracted country({wh_country})"
        }
    }
    
    @classmethod
    def get_message(cls, code, lang='zh', **kwargs):
        """获取国际化消息"""
        if code not in cls.MESSAGES:
            return code
        
        message = cls.MESSAGES[code].get(lang, cls.MESSAGES[code].get('zh', code))
        
        # 替换参数
        for key, value in kwargs.items():
            message = message.replace(f'{{{key}}}', str(value))
        
        return message
    
    @classmethod
    def format_error(cls, row_idx, code, **kwargs):
        """格式化错误信息"""
        return {
            "row": row_idx,
            "code": code,
            "params": kwargs
        }