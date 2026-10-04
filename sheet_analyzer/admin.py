from django.contrib import admin
from django.contrib.auth.models import User, Group
from django.core.exceptions import PermissionDenied
from .models import UploadedFile, Profile


class SuperuserAdminSite(admin.AdminSite):
    """Admin site restricted to superusers only."""
    site_header = 'SheetAI Administration'
    site_title  = 'SheetAI Admin'
    index_title = 'Admin Panel'

    def has_permission(self, request):
        return request.user.is_active and request.user.is_superuser

    def login(self, request, extra_context=None):
        if request.user.is_authenticated and not request.user.is_superuser:
            raise PermissionDenied
        return super().login(request, extra_context)


admin_site = SuperuserAdminSite(name='restricted_admin')


@admin.register(UploadedFile)
class UploadedFileAdmin(admin.ModelAdmin):
    list_display    = ('id', 'filename', 'prompt', 'uploaded_at')
    list_filter     = ('uploaded_at',)
    search_fields   = ('file', 'prompt')
    ordering        = ('-uploaded_at',)
    readonly_fields = ('uploaded_at',)

    def has_module_perms(self, request, app_label):
        return request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_add_permission(self, request):
        return request.user.is_superuser

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser


@admin.register(Profile, site=admin_site)
class ProfileAdmin(admin.ModelAdmin):
    list_display    = ('user', 'created_at', 'updated_at')
    list_filter     = ('created_at', 'updated_at')
    search_fields   = ('user__username', 'user__email')
    ordering        = ('-updated_at',)
    readonly_fields = ('created_at', 'updated_at')


# Register on the default admin site too (with same restrictions)
admin_site.register(UploadedFile, UploadedFileAdmin)
admin_site.register(User)
admin_site.register(Group)

