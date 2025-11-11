import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.getenv('SECRET_KEY', 'dev-key-change-me')
DEBUG = bool(int(os.getenv('DEBUG', '1')))
ALLOWED_HOSTS = [h for h in os.getenv('ALLOWED_HOSTS', 'localhost,127.0.0.1,dev.localhost').split(',') if h]

# django-tenants app split
SHARED_APPS = [
    'django_tenants',
    'apps.tenants',  # must be before django.contrib.contenttypes
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'rest_framework',
    'rest_framework_simplejwt',
    'django_filters',
    'corsheaders',
    'simple_history',  # for audit trails
    'django_celery_beat',  # for periodic task scheduling
    'tenant_users.permissions',
    'tenant_users.tenants',
    'apps.public_core',
    'apps.tenant_overlay',
    'apps.assistant',  # AI chat and plan modification
    'apps.policy',
    'apps.policy_ingest',
    'ordered_model',
    'plans',
]

TENANT_APPS = [
    'apps.tenant_overlay',
]

INSTALLED_APPS = SHARED_APPS + [app for app in TENANT_APPS if app not in SHARED_APPS]

MIDDLEWARE = [
    'django_tenants.middleware.main.TenantMainMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'simple_history.middleware.HistoryRequestMiddleware',  # for audit trail user tracking
]

ROOT_URLCONF = 'ra_config.urls'

AUTHENTICATION_BACKENDS = (
    # Use tenant-users authentication backend
    'tenant_users.permissions.backend.UserBackend',
    # Fallback to default model backend
    'django.contrib.auth.backends.ModelBackend',
)

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'ra_config.wsgi.application'

if os.getenv('DB_HOST'):
    DATABASES = {
        'default': {
            'ENGINE': 'django_tenants.postgresql_backend',
            'NAME': os.getenv('DB_NAME', 'regulagent'),
            'USER': os.getenv('DB_USER', 'postgres'),
            'PASSWORD': os.getenv('DB_PASSWORD', 'postgres'),
            'HOST': os.getenv('DB_HOST', 'localhost'),
            'PORT': int(os.getenv('DB_PORT', '5432')),
            'CONN_MAX_AGE': int(os.getenv('DB_CONN_MAX_AGE', '60')),
            'OPTIONS': {'connect_timeout': int(os.getenv('DB_CONNECT_TIMEOUT', '5'))},
        }
    }
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
        }
    }

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

STATIC_URL = '/static/'
STATIC_ROOT = os.path.join(BASE_DIR, 'staticfiles')
MEDIA_URL = 'media/'
MEDIA_ROOT = os.path.join(BASE_DIR, 'mediafiles')

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Custom user model
AUTH_USER_MODEL = 'tenants.User'

# django-tenants configuration
TENANT_MODEL = 'tenants.Tenant'
TENANT_DOMAIN_MODEL = 'tenants.Domain'
PUBLIC_SCHEMA_NAME = 'public'
DATABASE_ROUTERS = (
    'django_tenants.routers.TenantSyncRouter',
)
PUBLIC_SCHEMA_URLCONF = 'ra_config.urls'
TENANT_URLCONF = 'ra_config.urls'

# DRF configuration with JWT authentication
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework_simplejwt.authentication.JWTAuthentication',
        'rest_framework.authentication.SessionAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination',
    'PAGE_SIZE': 50,
}

# CORS (relaxed in dev; tightened in prod)
# Default dev frontends (Next.js/Vite)
CORS_ALLOWED_ORIGINS = [
    'http://localhost:3000',
    'http://127.0.0.1:3000',
    'http://localhost:5173',
    'http://127.0.0.1:5173',
]

# Allow credentials for local development (needed if frontend uses cookies or fetch credentials: 'include')
CORS_ALLOW_CREDENTIALS = True

# JWT Configuration
from datetime import timedelta

SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(hours=1),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=7),
    'ROTATE_REFRESH_TOKENS': True,
    'BLACKLIST_AFTER_ROTATION': True,
    'AUTH_HEADER_TYPES': ('Bearer',),
    'USER_ID_FIELD': 'id',
    'USER_ID_CLAIM': 'user_id',
}

# django-plans configuration
PLANS_CURRENCY = 'USD'
PLANS_PLAN_MODEL = 'plans.Plan'


# ==============================================================================
# FILE UPLOAD & STORAGE SETTINGS
# ==============================================================================

# Toggle between S3 and local filesystem storage
USE_S3 = os.getenv('USE_S3', 'false').lower() == 'true'

if USE_S3:
    # ========== S3 STORAGE CONFIGURATION ==========
    # AWS credentials (set via environment variables)
    AWS_ACCESS_KEY_ID = os.getenv('AWS_ACCESS_KEY_ID')
    AWS_SECRET_ACCESS_KEY = os.getenv('AWS_SECRET_ACCESS_KEY')
    AWS_STORAGE_BUCKET_NAME = os.getenv('AWS_STORAGE_BUCKET_NAME', 'regulagent-uploads')
    AWS_S3_REGION_NAME = os.getenv('AWS_S3_REGION_NAME', 'us-east-1')
    AWS_S3_CUSTOM_DOMAIN = f'{AWS_STORAGE_BUCKET_NAME}.s3.amazonaws.com'
    
    # Security and permissions
    AWS_DEFAULT_ACL = None  # Inherit bucket ACL (recommended)
    AWS_S3_OBJECT_PARAMETERS = {
        'CacheControl': 'max-age=86400',  # 24 hours
    }
    
    # Use S3 for file uploads
    DEFAULT_FILE_STORAGE = 'apps.public_core.storage.TenantS3Storage'
    
    # Media URLs will point to S3
    MEDIA_URL = f'https://{AWS_S3_CUSTOM_DOMAIN}/'
    
else:
    # ========== LOCAL FILESYSTEM STORAGE ==========
    # Store uploads in Docker container (or local dev)
    MEDIA_ROOT = os.path.join(BASE_DIR, 'mediafiles', 'uploads')
    MEDIA_URL = '/media/uploads/'
    
    # Use local filesystem for file uploads
    DEFAULT_FILE_STORAGE = 'apps.public_core.storage.TenantLocalStorage'

# File upload limits
FILE_UPLOAD_MAX_MEMORY_SIZE = 52428800  # 50MB in bytes
DATA_UPLOAD_MAX_MEMORY_SIZE = 52428800  # 50MB in bytes
FILE_UPLOAD_PERMISSIONS = 0o644

# Allowed upload file types (validation in view layer)
ALLOWED_UPLOAD_EXTENSIONS = ['.pdf']


# ==============================================================================
# CELERY SETTINGS
# ==============================================================================

# Celery broker (Redis)
CELERY_BROKER_URL = os.getenv('REDIS_URL', 'redis://localhost:6379/0')
CELERY_RESULT_BACKEND = os.getenv('REDIS_URL', 'redis://localhost:6379/0')

# Celery configuration
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_TIMEZONE = TIME_ZONE
CELERY_ENABLE_UTC = True

# Task result expiration
CELERY_RESULT_EXPIRES = 3600  # 1 hour

# Task routing (optional - for when you want to split tasks across queues)
# CELERY_TASK_ROUTES = {
#     'apps.assistant.tasks.*': {'queue': 'assistant'},
# }
# Note: Commented out - using default 'celery' queue for all tasks

# Task time limits (prevent runaway tasks)
CELERY_TASK_TIME_LIMIT = 300  # 5 minutes hard limit
CELERY_TASK_SOFT_TIME_LIMIT = 240  # 4 minutes soft limit

# Worker configuration
CELERY_WORKER_PREFETCH_MULTIPLIER = 4
CELERY_WORKER_MAX_TASKS_PER_CHILD = 1000  # Restart worker after 1000 tasks (memory cleanup)

# Beat scheduler (for periodic tasks)
CELERY_BEAT_SCHEDULER = 'django_celery_beat.schedulers:DatabaseScheduler'


