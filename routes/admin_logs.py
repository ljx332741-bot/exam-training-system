# routes/admin_logs.py - 完整修复版

import os
import glob
import json
import logging
import subprocess
import platform
from datetime import datetime
from flask import Blueprint, request, jsonify, session, send_file
from routes.helpers import login_required, admin_required

logger = logging.getLogger(__name__)
admin_logs_bp = Blueprint('admin_logs', __name__, url_prefix='/api/admin/logs')

# 日志配置文件路径
CONFIG_FILE = 'log_config.json'


def get_log_config():
    """获取日志配置"""
    default_config = {'retention_days': 2}
    try:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                config = json.load(f)
                if 'retention_days' not in config:
                    config['retention_days'] = default_config['retention_days']
                return config
    except Exception as e:
        logger.error(f"读取日志配置文件失败: {e}")
    return default_config


def save_log_config(config):
    """保存日志配置"""
    try:
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
        return True
    except Exception as e:
        logger.error(f"保存日志配置文件失败: {e}")
        return False


def get_log_files(log_dir='logs'):
    """
    获取所有日志文件信息
    支持新旧两种命名格式：
    - 新格式：exam_app.2026-08-20.log
    - 旧格式：exam_app.log.2026-08-20（兼容过渡期）
    """
    if not os.path.exists(log_dir):
        return [], []
    
    log_files = []
    backup_files = []
    current_log = 'exam_app.log'
    current_log_path = os.path.join(log_dir, current_log)
    
    # 收集所有匹配的文件
    all_files = []
    
    # 1. 先检查当前日志文件是否存在
    if os.path.exists(current_log_path):
        all_files.append(current_log_path)
    
    # 2. 匹配新格式：exam_app.*.log（排除当前日志文件）
    pattern_new = os.path.join(log_dir, 'exam_app.*.log')
    for file_path in glob.glob(pattern_new):
        if file_path != current_log_path:  # 排除当前日志
            all_files.append(file_path)
    
    # 3. 匹配旧格式：exam_app.log.*（兼容过渡期）
    pattern_old = os.path.join(log_dir, 'exam_app.log.*')
    for file_path in glob.glob(pattern_old):
        if file_path != current_log_path:  # 排除当前日志
            all_files.append(file_path)
    
    # 去重
    all_files = list(set(all_files))
    
    for file_path in all_files:
        try:
            stat = os.stat(file_path)
            file_info = {
                'name': os.path.basename(file_path),
                'path': file_path,
                'size': stat.st_size,
                'size_mb': round(stat.st_size / (1024 * 1024), 2),
                'size_kb': round(stat.st_size / 1024, 1),
                'modified': stat.st_mtime,
                'modified_str': datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M:%S'),
                'is_current': file_path == current_log_path
            }
            
            if file_path == current_log_path:
                log_files.append(file_info)
            else:
                backup_files.append(file_info)
        except Exception as e:
            logger.error(f"获取日志文件 {file_path} 信息失败: {e}")
    
    # 当前日志排在最前
    log_files.sort(key=lambda x: x['modified'], reverse=True)
    # 备份文件按修改时间排序（最新的在前）
    backup_files.sort(key=lambda x: x['modified'], reverse=True)
    
    return log_files, backup_files


def get_backup_count_from_config():
    """从配置文件获取备份数量（即保留天数）"""
    config = get_log_config()
    return config.get('retention_days', 2)


# ========== API 路由 ==========

@admin_logs_bp.route('/info', methods=['GET'])
@login_required
@admin_required
def get_logs_info():
    """获取日志信息：文件列表和当前配置"""
    if session.get('role') not in ['super_admin', 'developer']:
        return jsonify({'success': False, 'message': '权限不足'}), 403
    
    config = get_log_config()
    log_files, backup_files = get_log_files()
    
    # 计算总大小
    all_files = log_files + backup_files
    total_size = sum(f['size'] for f in all_files)
    
    # 获取备份数量配置
    backup_count = config.get('retention_days', 2)
    
    return jsonify({
        'success': True,
        'data': {
            'config': config,
            'current_log': log_files[0] if log_files else None,
            'backup_files': backup_files,
            'backup_count': backup_count,
            'total_size_mb': round(total_size / (1024 * 1024), 2),
            'file_count': len(all_files),
            'backup_file_count': len(backup_files)
        }
    })


@admin_logs_bp.route('/open_with_notepad', methods=['POST'])
@login_required
@admin_required
def open_with_notepad():
    """
    用系统默认编辑器（记事本）打开日志文件
    """
    if session.get('role') not in ['super_admin', 'developer']:
        return jsonify({'success': False, 'message': '权限不足'}), 403
    
    data = request.get_json()
    file_name = data.get('file')
    
    if not file_name:
        return jsonify({'success': False, 'message': '缺少文件名参数'}), 400
    
    # 安全检查
    if '..' in file_name or '/' in file_name or '\\' in file_name:
        return jsonify({'success': False, 'message': '非法文件名'}), 400
    
    log_dir = 'logs'
    file_path = os.path.join(log_dir, file_name)
    
    if not os.path.exists(file_path):
        return jsonify({'success': False, 'message': '文件不存在'}), 404
    
    try:
        # 检测操作系统
        system = platform.system()
        
        # 转换为绝对路径，确保编辑器能正确打开
        abs_path = os.path.abspath(file_path)
        
        if system == 'Windows':
            # Windows: 使用记事本打开
            subprocess.Popen(['notepad.exe', abs_path])
            message = f'已在记事本中打开 {file_name}'
        elif system == 'Darwin':  # macOS
            subprocess.Popen(['open', '-a', 'TextEdit', abs_path])
            message = f'已在文本编辑中打开 {file_name}'
        else:  # Linux
            subprocess.Popen(['xdg-open', abs_path])
            message = f'已用默认编辑器打开 {file_name}'
        
        logger.info(f"管理员 {session.get('user_id')} 在服务器端打开日志文件: {file_name}")
        
        return jsonify({
            'success': True,
            'message': message,
            'file': file_name,
            'path': abs_path
        })
        
    except Exception as e:
        logger.error(f"打开日志文件失败: {e}")
        return jsonify({
            'success': False, 
            'message': f'打开失败: {str(e)}'
        }), 500


@admin_logs_bp.route('/download', methods=['GET'])
@login_required
@admin_required
def download_log_file():
    """
    下载日志文件（供用户下载后本地查看）
    """
    if session.get('role') not in ['super_admin', 'developer']:
        return jsonify({'success': False, 'message': '权限不足'}), 403
    
    file_name = request.args.get('file')
    if not file_name:
        return jsonify({'success': False, 'message': '缺少文件名参数'}), 400
    
    # 安全检查
    if '..' in file_name or '/' in file_name or '\\' in file_name:
        return jsonify({'success': False, 'message': '非法文件名'}), 400
    
    log_dir = 'logs'
    file_path = os.path.join(log_dir, file_name)
    
    if not os.path.exists(file_path):
        return jsonify({'success': False, 'message': '文件不存在'}), 404
    
    return send_file(file_path, as_attachment=True, download_name=file_name)


@admin_logs_bp.route('/config', methods=['POST'])
@login_required
@admin_required
def update_log_config():
    """更新日志保留天数配置"""
    if session.get('role') not in ['super_admin', 'developer']:
        return jsonify({'success': False, 'message': '权限不足'}), 403
    
    data = request.get_json()
    retention_days = data.get('retention_days')
    
    if retention_days is None:
        return jsonify({'success': False, 'message': '缺少保留天数参数'}), 400
    
    try:
        retention_days = int(retention_days)
        if retention_days < 1 or retention_days > 365:
            return jsonify({'success': False, 'message': '保留天数必须在 1 到 365 之间'}), 400
    except ValueError:
        return jsonify({'success': False, 'message': '保留天数必须是整数'}), 400
    
    # 保存配置
    config = get_log_config()
    config['retention_days'] = retention_days
    
    if not save_log_config(config):
        return jsonify({'success': False, 'message': '保存配置失败'}), 500
    
    # 触发清理
    from utils.logger import clean_old_logs
    clean_old_logs('logs', retention_days)
    
    logger.info(f"日志保留天数已更新为 {retention_days} 天，由管理员 {session.get('user_id')} 操作")
    
    return jsonify({
        'success': True,
        'message': f'日志保留天数已更新为 {retention_days} 天，并已清理旧日志',
        'data': {'retention_days': retention_days}
    })


@admin_logs_bp.route('/clean', methods=['POST'])
@login_required
@admin_required
def clean_logs():
    """手动触发清理旧日志"""
    if session.get('role') not in ['super_admin', 'developer']:
        return jsonify({'success': False, 'message': '权限不足'}), 403
    
    config = get_log_config()
    retention_days = config.get('retention_days', 2)
    
    from utils.logger import clean_old_logs
    clean_old_logs('logs', retention_days)
    
    logger.info(f"手动清理旧日志完成，保留 {retention_days} 天，由管理员 {session.get('user_id')} 操作")
    
    return jsonify({
        'success': True,
        'message': f'已清理保留 {retention_days} 天之前的旧日志'
    })