from django.urls import path
from .views import upload_and_query, delete_record, clear_all_history, view_record, clean_data, edit_data, change_password, custom_admin, admin_delete_user, admin_create_user, admin_delete_record, upload_profile_photo, delete_profile_photo

urlpatterns = [
    path('', upload_and_query, name='upload_query'),
    path('delete/<int:pk>/', delete_record, name='delete_record'),
    path('clear-history/', clear_all_history, name='clear_all_history'),
    path('view/<int:pk>/', view_record, name='view_record'),
    path('clean/<int:pk>/', clean_data, name='clean_data'),
    path('edit/<int:pk>/', edit_data, name='edit_data'),
    path('change-password/', change_password, name='change_password'),
    path('profile-photo/upload/', upload_profile_photo, name='upload_profile_photo'),
    path('profile-photo/delete/', delete_profile_photo, name='delete_profile_photo'),
    path('panel/', custom_admin, name='custom_admin'),
    path('panel/delete-user/<int:uid>/', admin_delete_user, name='admin_delete_user'),
    path('panel/create-user/', admin_create_user, name='admin_create_user'),
    path('panel/delete-record/<int:pk>/', admin_delete_record, name='admin_delete_record'),
]
