# utils/ip_location.py - 新建文件

"""
IP 归属地查询（使用免费 API）
"""

import requests
import logging
from functools import lru_cache

logger = logging.getLogger(__name__)

# 内网 IP 段列表
PRIVATE_IP_PREFIXES = (
    '127.', '10.', '172.16.', '172.17.', '172.18.', '172.19.',
    '172.20.', '172.21.', '172.22.', '172.23.', '172.24.', '172.25.',
    '172.26.', '172.27.', '172.28.', '172.29.', '172.30.', '172.31.',
    '192.168.', '::1', '0.0.0.0'
)

# 免费 IP 查询服务
IP_API_URL = 'http://ip-api.com/json/{}?fields=status,message,country,regionName,city,isp,query&lang=zh-CN'


def is_private_ip(ip):
    """判断是否为内网 IP"""
    if not ip:
        return True
    return ip.startswith(PRIVATE_IP_PREFIXES)

@lru_cache(maxsize=1000)
def get_ip_location(ip):
    """
    查询 IP 归属地（带缓存）
    
    Returns:
        dict: {
            'country': '中国',
            'region': '广东省',
            'city': '深圳市',
            'isp': '中国电信',
            'ip': 'xxx.xxx.xxx.xxx'
        }
    """
    # 1. 本地回环
    if ip in ('127.0.0.1', '::1', '0.0.0.0'):
        return {
            'country': '本地 (localhost)',
            'region': '-',
            'city': '-',
            'isp': '本地回环',
            'ip': ip
        }
    
    # 2. 内网 IP
    if is_private_ip(ip):
        return {
            'country': '内网 (LAN)',
            'region': '-',
            'city': '-',
            'isp': '内网地址',
            'ip': ip
        }
    
    # 3. 外网 IP 查询（带缓存）
    try:
        response = requests.get(IP_API_URL.format(ip), timeout=5)
        data = response.json()
        
        if data.get('status') == 'success':
            return {
                'country': data.get('country', '未知'),
                'region': data.get('regionName', ''),
                'city': data.get('city', ''),
                'isp': data.get('isp', ''),
                'ip': data.get('query', ip)
            }
    except Exception as e:
        logger.warning(f"IP 归属地查询失败: {e}")
    
    return {
        'country': '未知',
        'region': '-',
        'city': '-',
        'isp': '-',
        'ip': ip
    }


def format_ip_location(location):
    """格式化 IP 位置信息为显示字符串"""
    if not location:
        return '未知位置'
    
    parts = []
    if location.get('country') and location['country'] not in ('未知', '内网', '本地'):
        parts.append(location['country'])
    if location.get('city') and location['city'] != '-':
        parts.append(location['city'])
    
    # 如果有 ISP 信息
    if location.get('isp') and location['isp'] not in ('-', ''):
        parts.append(f"({location['isp']})")
    
    return ' '.join(parts) if parts else '未知位置'


def mask_ip(ip):
    """IP 地址脱敏（只显示前两段）"""
    if not ip:
        return ''
    if is_private_ip(ip):
        return ip  # 内网 IP 不脱敏
    parts = ip.split('.')
    if len(parts) == 4:
        return f"{parts[0]}.{parts[1]}.***.***"
    return ip