# utils/ip_location.py - 新建文件

"""
IP 归属地查询（使用免费 API）
"""

import requests
import logging
from functools import lru_cache

logger = logging.getLogger(__name__)

# 免费 IP 查询服务
IP_API_URL = 'http://ip-api.com/json/{}?fields=status,message,country,regionName,city,isp,query&lang=zh-CN'


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
    # 内网 IP 不查询
    if ip.startswith(('127.', '192.168.', '10.', '172.16.', '172.17.', '172.18.', '172.19.', '172.20.', '172.21.', '172.22.', '172.23.', '172.24.', '172.25.', '172.26.', '172.27.', '172.28.', '172.29.', '172.30.', '172.31.')):
        return {
            'country': '内网',
            'region': '-',
            'city': '-',
            'isp': '-',
            'ip': ip
        }
    
    # 本地开发环境
    if ip in ('127.0.0.1', 'localhost', '::1'):
        return {
            'country': '本地',
            'region': '-',
            'city': '-',
            'isp': '-',
            'ip': ip
        }
    
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
    """IP 地址脱敏"""
    if not ip:
        return ''
    parts = ip.split('.')
    if len(parts) == 4:
        return f"{parts[0]}.{parts[1]}.***.***"
    return ip