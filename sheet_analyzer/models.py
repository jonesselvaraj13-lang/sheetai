from django.db import models
from django.contrib.auth.models import User

class Profile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    photo = models.ImageField(upload_to='profile_photos/', blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user.username}'s Profile"


class UploadedFile(models.Model):
    file = models.FileField(upload_to='uploads/', blank=True, null=True)
    file2 = models.FileField(upload_to='uploads/', blank=True, null=True)
    url_link = models.CharField(max_length=2000, blank=True, default='')
    url_link_2 = models.CharField(max_length=2000, blank=True, default='')
    prompt = models.TextField(blank=True, default='')
    uploaded_at = models.DateTimeField(auto_now_add=True)

    def filename(self):
        if self.url_link:
            return self.url_link.split('/')[-1].split('?')[0] or 'url_data'
        return self.file.name.split('/')[-1]
