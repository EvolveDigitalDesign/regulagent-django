from .base import *  # noqa

DEBUG = False

# Security hardening (annotated for cost/practicality)
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
SECURE_HSTS_SECONDS = int(os.getenv('SECURE_HSTS_SECONDS', '0'))  # enable after ALB/HTTPS
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = False
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

# CORS: restrict to production frontend domains
CORS_ALLOWED_ORIGINS = [o for o in os.getenv('CORS_ALLOWED_ORIGINS', '').split(',') if o]

# Allow credentials when requested by the frontend (sets Access-Control-Allow-Credentials)
CORS_ALLOW_CREDENTIALS = os.getenv('CORS_ALLOW_CREDENTIALS', 'false').lower() == 'true'

# CSRF trusted origins for cookie-based interactions (schemes required, e.g., https://example.com)
CSRF_TRUSTED_ORIGINS = [o for o in os.getenv('CSRF_TRUSTED_ORIGINS', '').split(',') if o]

# Allowed hosts must be set via env
ALLOWED_HOSTS = [h for h in os.getenv('ALLOWED_HOSTS', '').split(',') if h]


