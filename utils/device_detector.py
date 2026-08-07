# utils/device_detector.py - 新建文件

"""
设备检测工具
"""

from user_agents import parse

def detect_device(user_agent_string):
    """
    解析 User-Agent 获取设备信息
    
    Returns:
        dict: {
            'os': 'Windows 10',
            'os_family': 'Windows',
            'browser': 'Chrome',
            'browser_family': 'Chrome',
            'device_type': 'desktop',  # 'desktop', 'mobile', 'tablet', 'bot'
            'device_brand': 'Apple',
            'device_model': 'iPhone',
            'is_mobile': False,
            'is_tablet': False,
            'is_bot': False
        }
    """
    if not user_agent_string:
        return {
            'os': '未知',
            'os_family': '未知',
            'browser': '未知',
            'device_type': 'unknown',
            'is_mobile': False,
            'is_tablet': False,
            'is_bot': False
        }
    
    try:
        ua = parse(user_agent_string)
        
        # 检测设备类型
        if ua.is_bot:
            device_type = 'bot'
        elif ua.is_mobile:
            device_type = 'mobile'
        elif ua.is_tablet:
            device_type = 'tablet'
        elif ua.is_pc:
            device_type = 'desktop'
        else:
            device_type = 'unknown'
        
        return {
            'os': ua.os.family or '未知',
            'os_version': ua.os.version_string or '',
            'browser': ua.browser.family or '未知',
            'browser_version': ua.browser.version_string or '',
            'device_type': device_type,
            'device_brand': ua.device.brand or '',
            'device_model': ua.device.model or '',
            'is_mobile': ua.is_mobile,
            'is_tablet': ua.is_tablet,
            'is_bot': ua.is_bot,
            'is_pc': ua.is_pc
        }
    except Exception as e:
        return {
            'os': '解析失败',
            'device_type': 'unknown',
            'is_mobile': False,
            'is_tablet': False,
            'is_bot': False
        }


def format_device_info(device_info):
    """格式化设备信息为显示字符串"""
    if not device_info:
        return '未知设备'
    
    # 如果是爬虫
    if device_info.get('is_bot'):
        return f"🤖 搜索引擎爬虫"
    
    # 设备类型图标
    type_icons = {
        'desktop': '🖥️',
        'mobile': '📱',
        'tablet': '📟',
        'unknown': '❓'
    }
    icon = type_icons.get(device_info.get('device_type'), '❓')
    
    parts = []
    
    # 操作系统
    os_name = device_info.get('os', '')
    if os_name:
        parts.append(os_name)
    
    # 浏览器
    browser = device_info.get('browser', '')
    if browser and browser != '未知':
        parts.append(browser)
    
    # 设备型号（仅移动端）
    if device_info.get('is_mobile'):
        model = device_info.get('device_model', '')
        brand = device_info.get('device_brand', '')
        if model:
            parts.append(f"{brand} {model}".strip())
    
    return f"{icon} {' / '.join(parts)}" if parts else '未知设备'