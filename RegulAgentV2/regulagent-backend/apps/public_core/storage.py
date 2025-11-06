"""
Tenant-aware file storage backends for local filesystem and S3.

Organizes uploaded files by tenant to maintain isolation:
- Local: /mediafiles/uploads/<tenant_id>/<document_type>/<filename>
- S3: s3://<bucket>/<tenant_id>/<document_type>/<filename>

Usage:
    # In settings.py:
    DEFAULT_FILE_STORAGE = 'apps.public_core.storage.TenantLocalStorage'
    # or
    DEFAULT_FILE_STORAGE = 'apps.public_core.storage.TenantS3Storage'
"""

from django.core.files.storage import FileSystemStorage
from django.conf import settings
import os


class TenantLocalStorage(FileSystemStorage):
    """
    Local filesystem storage with tenant-aware directory structure.
    
    Files are organized as: <MEDIA_ROOT>/<path_from_upload_view>
    Example: /app/mediafiles/uploads/tenant-uuid/w2/42-123-45678_W2.pdf
    
    This class extends Django's FileSystemStorage to:
    - Preserve original filenames (no random suffixes)
    - Support consistent path structure for tenant isolation
    """
    
    def __init__(self, location=None, base_url=None, **kwargs):
        if location is None:
            location = getattr(settings, 'MEDIA_ROOT', None)
        if base_url is None:
            base_url = getattr(settings, 'MEDIA_URL', None)
        super().__init__(location=location, base_url=base_url, **kwargs)
    
    def get_available_name(self, name, max_length=None):
        """
        Return the filename as-is without adding random suffixes.
        
        The upload view constructs unique paths like:
        <tenant_id>/<document_type>/<api>_<filename>
        
        So we don't need Django's default _random suffix behavior.
        """
        # If file exists, remove it (we want to overwrite)
        if self.exists(name):
            self.delete(name)
        
        return name


# Only import S3 storage if boto3 is available
try:
    from storages.backends.s3boto3 import S3Boto3Storage
    
    class TenantS3Storage(S3Boto3Storage):
        """
        S3 storage with tenant-aware path structure.
        
        Files are organized as: s3://<bucket>/<path_from_upload_view>
        Example: s3://regulagent-uploads/tenant-uuid/w2/42-123-45678_W2.pdf
        
        This class extends django-storages' S3Boto3Storage to:
        - Use bucket/region from settings
        - Preserve original filenames
        - Maintain consistent paths with local storage
        """
        
        def __init__(self, **kwargs):
            # Get S3 settings from Django settings
            kwargs['bucket_name'] = getattr(settings, 'AWS_STORAGE_BUCKET_NAME', None)
            kwargs['region_name'] = getattr(settings, 'AWS_S3_REGION_NAME', 'us-east-1')
            kwargs['custom_domain'] = getattr(settings, 'AWS_S3_CUSTOM_DOMAIN', None)
            kwargs['default_acl'] = getattr(settings, 'AWS_DEFAULT_ACL', None)
            kwargs['object_parameters'] = getattr(settings, 'AWS_S3_OBJECT_PARAMETERS', {})
            
            super().__init__(**kwargs)
        
        def get_available_name(self, name, max_length=None):
            """
            Return the filename as-is.
            S3 overwrites by default, which is what we want for tenant-organized paths.
            """
            return name

except ImportError:
    # boto3/storages not installed, S3 storage won't be available
    class TenantS3Storage:
        """Placeholder when boto3 is not available."""
        def __init__(self, *args, **kwargs):
            raise ImportError(
                "S3 storage requires 'django-storages[s3]' and 'boto3' packages. "
                "Install with: pip install django-storages[s3] boto3"
            )

