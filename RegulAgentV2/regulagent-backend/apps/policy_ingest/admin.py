from django.contrib import admin
from .models import PolicyRule
from .models import PolicySection

admin.site.register(PolicyRule)
admin.site.register(PolicySection)
