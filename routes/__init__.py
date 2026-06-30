# routes/__init__.py
from flask import Blueprint, url_for
from .admin_stats import (
    get_user_stats,
    get_exam_stats,
    get_training_stats,
    get_interview_stats,
    get_questions_stats,
    get_exams_for_display,
    get_sign_in_status
)


# 注意：admin_bp 是管理员主蓝图，用于挂载所有管理功能
admin_bp = Blueprint('admin', __name__)

# 然后导入 admin_message 模块（它会在内部使用 admin_bp）
from .admin_message import message_bp

# ========== 1. 定义所有蓝图 ==========
auth_bp = Blueprint('auth', __name__)
exam_bp = Blueprint('exam', __name__)
training_bp = Blueprint('training', __name__)
admin_exam_bp = Blueprint('admin_exam', __name__)
admin_user_bp = Blueprint('admin_user', __name__)
admin_training_bp = Blueprint('admin_training', __name__)
admin_inspection_bp = Blueprint('admin_inspection', __name__)
admin_export_bp = Blueprint('admin_export', __name__)
admin_wh_bp = Blueprint('admin_wh', __name__)

# ========== 2. 端点别名映射表（关键！） ==========
# 格式: '旧端点名': '新蓝图.新端点名'
ENDPOINT_ALIAS = {
    # ========== 认证模块 ==========
    'login': 'auth.login',
    'register': 'auth.register',
    'profile': 'auth.profile',
    'logout': 'auth.logout',
    'index': 'auth.index',
    'api_countries': 'auth.api_countries',
    'check_name': 'auth.check_name',
    'api_send_otp': 'auth.api_send_otp',
    'api_register': 'auth.api_register',
    'api_reset_password': 'auth.api_reset_password',
    
    # ========== 学员考试模块 ==========
    'dashboard': 'exam.dashboard',
    'take_exam': 'exam.take_exam',
    'save_exam_draft': 'exam.save_exam_draft',
    'submit_exam': 'exam.submit_exam',
    'exam_result_detail': 'exam.exam_result_detail',
    'exam_export_pdf': 'exam.exam_export_pdf',
    'take_interview': 'exam.take_interview',
    'submit_interview': 'exam.submit_interview',
    'my_interviews': 'exam.my_interviews',
    
    # ========== 学员培训模块 ==========
    'api_available_trainings': 'training.api_available_trainings',
    'api_training_sign': 'training.api_training_sign',
    'api_resign_training': 'training.api_resign_training',
    
    # ========== 管理员 - 考试管理 ==========
    'admin_dashboard': 'admin_exam.admin_dashboard',
    'admin_reset_exam': 'admin_exam.admin_reset_exam',
    'admin_delete_exam': 'admin_exam.admin_delete_exam',
    'restore_exam': 'admin_exam.restore_exam',
    'admin_import': 'admin_exam.admin_import',
    'admin_import_save': 'admin_exam.admin_import_save',
    'copy_exam': 'admin_exam.copy_exam',
    'copy_exam_preview': 'admin_exam.copy_exam_preview',
    'edit_exam_preview': 'admin_exam.edit_exam_preview',
    'update_exam_full': 'admin_exam.update_exam_full',
    'admin_exam_settings': 'admin_exam.admin_exam_settings',
    'update_exam_duration': 'admin_exam.update_exam_duration',
    'admin_exam_status': 'admin_exam.admin_exam_status',
    'admin_result_detail': 'admin_exam.admin_result_detail',
    
    # 考试清单和成绩页面 - 使用不同的键名
    'admin_exams_page': 'admin_exam.admin_exams_page',
    'admin_exam_scores_page': 'admin_exam.admin_exam_scores_page',
    'api_admin_exams_stats': 'admin_exam.api_admin_exams_stats',
    'api_admin_exam_detail': 'admin_exam.api_admin_exam_detail',
    'api_admin_exam_assignments': 'admin_exam.api_admin_exam_assignments',
    'api_admin_exam_update': 'admin_exam.api_admin_exam_update',
    'admin_push_exam_with_settings': 'admin_exam.admin_push_exam_with_settings',
    'api_admin_exams_list': 'admin_exam.api_admin_exams_list',
    'api_admin_exam_scores': 'admin_exam.api_admin_exam_scores',
    'admin_push_exam': 'admin_exam.admin_push_exam',
    'api_admin_delete_exam_result': 'admin_exam.api_admin_delete_exam_result',
    'api_admin_batch_delete_exam_results': 'admin_exam.api_admin_batch_delete_exam_results',
    'api_quarters': 'admin_exam.api_quarters',
    'admin_questions_stats': 'admin_exam.admin_questions_stats',
    
    # ========== 管理员 - 用户管理 ==========
    'admin_user_list': 'admin_user.admin_user_list',
    'api_admin_user_detail': 'admin_user.api_admin_user_detail',
    'api_admin_users': 'admin_user.api_admin_users',
    'api_admin_add_user': 'admin_user.api_admin_add_user',
    'api_admin_import_users': 'admin_user.api_admin_import_users',
    'api_admin_edit_user': 'admin_user.api_admin_edit_user',
    'api_admin_delete_user': 'admin_user.api_admin_delete_user',
    'api_admin_deleted_users': 'admin_user.api_admin_deleted_users',
    'api_admin_restore_user': 'admin_user.api_admin_restore_user',
    'api_admin_batch_restore_users': 'admin_user.api_admin_batch_restore_users',
    'api_admin_permanent_delete_user': 'admin_user.api_admin_permanent_delete_user',
    'api_admin_batch_permanent_delete_users': 'admin_user.api_admin_batch_permanent_delete_users',
    'api_admin_reset_user_password': 'admin_user.api_admin_reset_user_password',
    'refresh_permissions': 'admin_user.refresh_permissions',
    
    # ========== 管理员 - 培训管理 ==========
    'admin_trainings': 'admin_training.admin_trainings',
    'api_admin_trainings': 'admin_training.api_admin_trainings',
    'api_training_attendance': 'admin_training.api_training_attendance',
    'training_country_templates_status': 'admin_training.training_country_templates_status',
    'get_training_country_template': 'admin_training.get_training_country_template',
    'save_training_country_template': 'admin_training.save_training_country_template',
    'admin_reset_signature': 'admin_training.admin_reset_signature',
    'training_attendance_print': 'admin_training.training_attendance_print',
    'download_training_attendance_pdf': 'admin_training.download_training_attendance_pdf',
    'admin_training_attendance': 'admin_training.admin_training_attendance',
    'api_training_attendance_by_country': 'admin_training.api_training_attendance_by_country',
    'api_admin_delete_training_attendance': 'admin_training.api_admin_delete_training_attendance',
    'api_admin_batch_delete_training_attendances': 'admin_training.api_admin_batch_delete_training_attendances',
    
    # ========== 管理员 - 访谈管理 ==========
    'admin_interviews_page': 'admin_inspection.admin_interviews_page',
    'api_admin_interviews': 'admin_inspection.api_admin_interviews',
    'api_get_interview': 'admin_inspection.api_get_interview',
    'get_interview_user_ids': 'admin_inspection.get_interview_user_ids',
    'admin_interview_detail_page': 'admin_inspection.admin_interview_detail_page',
    'api_interview_results': 'admin_inspection.api_interview_results',
    'resample_interview': 'admin_inspection.resample_interview',
    'interview_preview': 'admin_inspection.interview_preview',
    'admin_interview_details_page': 'admin_inspection.admin_interview_details_page',
    'api_interview_details': 'admin_inspection.api_interview_details',
    'get_interview_user_answers': 'admin_inspection.get_interview_user_answers',
    'admin_interviewee_stats': 'admin_inspection.admin_interviewee_stats',
    'api_admin_delete_interview_user_result': 'admin_inspection.api_admin_delete_interview_user_result',
    'api_admin_resample_interview': 'admin_inspection.api_admin_resample_interview',
    'api_admin_batch_delete_interview_results': 'admin_inspection.api_admin_batch_delete_interview_results',
    'api_admin_delete_interview_by_id': 'admin_inspection.api_admin_delete_interview_by_id',
    
    # ========== 导出模块 ==========
    'export_pdf': 'admin_export.export_pdf',
    'export_bilingual_excel': 'admin_export.export_bilingual_excel',
    'export_filtered_excel': 'admin_export.export_filtered_excel',
    'admin_export_pdf_by_result': 'admin_export.admin_export_pdf_by_result',
    'admin_batch_export_by_result': 'admin_export.admin_batch_export_by_result',
    'admin_export_user_pdf': 'admin_export.admin_export_user_pdf',
    'admin_batch_export_pdf': 'admin_export.admin_batch_export_pdf',
}

# ========== 3. 注册蓝图函数 ==========
def register_blueprints(app):
    # 延迟导入路由模块
    from . import api_auth
    from . import api_exam
    from . import api_training
    from . import admin_exam
    from . import admin_user
    from . import admin_training
    from . import admin_inspection
    from . import admin_export
    from . import admin_wh

    # 注册 admin_bp 和 admin_message_bp
    app.register_blueprint(message_bp)
    app.register_blueprint(admin_bp)

    # 注册 API 蓝图
    app.register_blueprint(api_auth.auth_bp)
    app.register_blueprint(api_exam.exam_bp)
    app.register_blueprint(api_training.training_bp)
    app.register_blueprint(admin_exam.admin_exam_bp)
    app.register_blueprint(admin_user.admin_user_bp)
    app.register_blueprint(admin_training.admin_training_bp)
    app.register_blueprint(admin_inspection.admin_inspection_bp)
    app.register_blueprint(admin_export.admin_export_bp)
    app.register_blueprint(admin_wh.admin_wh_bp)

    # 注入兼容版 url_for
    @app.context_processor
    def inject_compatible_url_for():
        def compat_url_for(endpoint, **values):
            try:
                # 1. 先尝试直接解析
                return url_for(endpoint, **values)
            except Exception:
                # 2. 尝试别名映射
                if endpoint in ENDPOINT_ALIAS:
                    try:
                        return url_for(ENDPOINT_ALIAS[endpoint], **values)
                    except:
                        pass
                # 3. 终极兜底：返回绝对路径（根据常见端点硬编码）
                path_map = {
                    'admin_exams_page': '/admin/exams',
                    'admin_dashboard': '/admin/dashboard',
                    'admin_user_list': '/admin/users',
                    'admin_trainings': '/admin/trainings',
                    'admin_interviews_page': '/admin/interviews',
                    'dashboard': '/dashboard',
                    'login': '/login',
                    'index': '/',
                }
                if endpoint in path_map:
                    return path_map[endpoint]
                # 4. 仍失败则抛出原始错误
                raise
        return {'url_for': compat_url_for}


# ========== 调试：打印已注册的路由 ==========
print("=" * 60)
print("🔍 admin_message 模块已加载")
print(f"   message_bp 名称: {message_bp.name}")
print(f"   message_bp url_prefix: {message_bp.url_prefix}")
print("=" * 60)