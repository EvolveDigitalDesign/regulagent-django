from django.contrib import admin
from .models import WellRegistry, ExtractedDocument, DocumentVector

admin.site.register(WellRegistry)
admin.site.register(ExtractedDocument)
admin.site.register(DocumentVector)

