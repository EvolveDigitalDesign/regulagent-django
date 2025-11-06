from .base import *  # noqa

DEBUG = True

# Dev CORS
CORS_ALLOW_ALL_ORIGINS = True

# Allow ngrok/tunneling services for v0 preview testing
# Add your ngrok URL here when testing with v0
ALLOWED_HOSTS = ALLOWED_HOSTS + [
    '*.ngrok.io',
    '*.ngrok-free.app',
]


