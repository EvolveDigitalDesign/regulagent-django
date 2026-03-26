"""
Test-specific Django settings for pytest with django-tenants support.
"""
from .base import *  # noqa

# Override settings for testing
DEBUG = False
SECRET_KEY = 'test-secret-key-not-for-production'

# Allow testserver host used by Django's test client / APIClient
ALLOWED_HOSTS = ['*']

# django-tenants: fall back to public schema when no domain matches (needed for test client)
SHOW_PUBLIC_IF_NO_TENANT_FOUND = True

# Use in-memory SQLite for faster tests (not PostgreSQL)
# Note: django-tenants requires PostgreSQL, so we'll keep the DB backend
# but use a test database name
if 'default' in DATABASES:
    DATABASES['default']['NAME'] = 'test_regulagent'
    DATABASES['default']['TEST'] = {
        'NAME': 'test_regulagent',
    }

# Disable migrations for faster tests (use --reuse-db for persistence)
# Commented out by default - uncomment if you want faster test runs
# class DisableMigrations:
#     def __contains__(self, item):
#         return True
#     def __getitem__(self, item):
#         return None
# MIGRATION_MODULES = DisableMigrations()

# Disable password hashing for faster user creation in tests
PASSWORD_HASHERS = [
    'django.contrib.auth.hashers.MD5PasswordHasher',
]

# Disable CORS for tests
CORS_ALLOW_ALL_ORIGINS = False
CORS_ALLOWED_ORIGINS = []

# Faster password validation for tests
AUTH_PASSWORD_VALIDATORS = []

# Simplified logging for tests
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
        },
    },
    'root': {
        'handlers': ['console'],
        'level': 'WARNING',
    },
}

# Disable Celery for tests (use synchronous execution)
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True

# Disable S3 for tests (use local filesystem)
USE_S3 = False
DEFAULT_FILE_STORAGE = 'apps.public_core.storage.TenantLocalStorage'

# Use local Redis for tests if needed, or mock
CELERY_BROKER_URL = 'memory://'
CELERY_RESULT_BACKEND = 'cache+memory://'
