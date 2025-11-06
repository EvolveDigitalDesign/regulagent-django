"""
Forms for tenant and user management in Django admin.
"""
from django import forms
from django.contrib.auth import password_validation
from django.contrib.auth.hashers import make_password

from apps.tenants.models import User


class UserAdminForm(forms.ModelForm):
    """
    Custom form for User admin that properly hashes passwords
    and validates password strength.
    
    Based on TestDriven.io guide:
    https://testdriven.io/blog/django-multi-tenant/#django-tenant-users
    """
    
    class Meta:
        model = User
        fields = '__all__'
    
    def clean_password(self):
        """
        Validates password strength and hashes it if needed.
        """
        password = self.cleaned_data.get('password')
        
        if not password:
            return password
        
        # Run the password validators
        try:
            password_validation.validate_password(password=password, user=self.instance)
        except forms.ValidationError as e:
            raise forms.ValidationError(e.messages)
        
        # Hash the password only if it isn't hashed yet
        # Django password hashes start with algorithm name (e.g., 'pbkdf2_sha256$')
        if password and not password.startswith('pbkdf2_'):
            return make_password(password)
        
        return password

