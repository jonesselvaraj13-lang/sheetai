from django import forms
from .models import UploadedFile, Profile


class FileUploadForm(forms.ModelForm):
    class Meta:
        model = UploadedFile
        fields = ['file', 'file2', 'url_link', 'url_link_2']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for f in ['file', 'file2', 'url_link', 'url_link_2']:
            self.fields[f].required = False
        accepted_files = '.csv,.xlsx,.xls,.pdf,.docx,.txt,.json'
        self.fields['file'].widget.attrs.update({'accept': accepted_files})
        self.fields['file2'].widget.attrs.update({'accept': accepted_files})

    def clean_url_link(self):
        url = self.cleaned_data.get('url_link', '') or ''
        url = url.strip()
        if url and not url.startswith(('http://', 'https://')):
            url = 'https://' + url
        return url

    def clean_url_link_2(self):
        url = self.cleaned_data.get('url_link_2', '') or ''
        url = url.strip()
        if url and not url.startswith(('http://', 'https://')):
            url = 'https://' + url
        return url

    def clean(self):
        cleaned_data = super().clean()
        # Clear all URL-related errors — we handle URLs manually in the view
        for field in ['url_link', 'url_link_2']:
            if field in self.errors:
                self.errors.pop(field)
                cleaned_data[field] = self.data.get(field, '').strip()
        return cleaned_data

    def is_valid(self):
        """Always valid — we do our own validation in the view."""
        self.errors  # trigger full_clean
        # Remove all non-file errors so form never blocks submission
        for field in ['url_link', 'url_link_2']:
            if field in self.errors:
                del self.errors[field]
        return True


class ProfilePhotoForm(forms.ModelForm):
    class Meta:
        model = Profile
        fields = ['photo']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['photo'].required = False
        self.fields['photo'].widget.attrs.update({'accept': 'image/*'})
