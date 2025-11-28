# pnaexchange to RegulAgent Authentication Integration

**Purpose:** Establish secure connection from pnaexchange to RegulAgent W-3 builder  
**Date:** 2025-11-26  
**Status:** Design & Implementation Guide

---

## Executive Summary

Both pnaexchange and RegulAgent use **JWT (JSON Web Token) authentication**. Integration requires:

1. **pnaexchange creates a service account** in RegulAgent (per tenant)
2. **Store RegulAgent credentials** in pnaexchange `IntegrationProfile` 
3. **pnaexchange obtains JWT token** from RegulAgent before each W-3 build request
4. **Include token** in HTTP Authorization header when calling W-3 builder endpoint

**No new API key system needed** - leverage existing JWT infrastructure.

---

## Current Authentication Architecture

### pnaexchange Authentication
**Pattern:** Multi-integration service accounts  
**Location:** `apps/integrations/*/services.py`

Examples:
- **Mix Telematics:** OAuth with client_id + client_secret + username/password
- **ADP:** JWT with private key certificate (RS256)
- **NetSuite:** OAuth with private key certificate (PS256)

**Credentials Storage:** `IntegrationProfile` model (TBD location)

```python
class IntegrationProfile:
    """Stores credentials for external system integrations."""
    tenant = ForeignKey(Tenant)
    integration_type = CharField(choices=['mix', 'adp', 'netsuite', 'regulagent'])
    config = JSONField()  # Encrypted
    # config = {
    #     'regulagent_url': 'https://regulagent.example.com',
    #     'username': 'pnaexchange-service@tenant.com',
    #     'password': 'encrypted-password',
    # }
```

### RegulAgent Authentication
**Pattern:** JWT-based (django-rest-framework-simplejwt)  
**Location:** `ra_config/settings/base.py`

```python
SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(hours=1),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=7),
    'ROTATE_REFRESH_TOKENS': True,
    'AUTH_HEADER_TYPES': ('Bearer',),
}

REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework_simplejwt.authentication.JWTAuthentication',
        'rest_framework.authentication.SessionAuthentication',
    ],
}
```

**Token Endpoint:** `POST /api/token/` or `POST /api/auth/token/`

---

## Integration Architecture

### High-Level Flow

```
1. Tenant Admin: Configure RegulAgent Connection
   ├─ Login to pnaexchange
   ├─ Go to Settings → Integrations → RegulAgent
   ├─ Enter: RegulAgent URL, service account email, password
   └─ Save (stored encrypted in IntegrationProfile)

2. pnaexchange Service: Auto-sync credentials
   ├─ Periodic task reads IntegrationProfile
   ├─ Validates RegulAgent connection
   └─ Stores test result (last check timestamp)

3. User: Trigger W-3 Generation
   ├─ In pnaexchange, create work orders (events)
   ├─ Click "Generate W-3"
   └─ pnaexchange calls RegulAgent endpoint

4. pnaexchange Service: Call W-3 Builder
   ├─ Step 1: Get JWT token (with cached refresh)
   ├─ Step 2: Build request payload
   ├─ Step 3: POST to /api/w3/build-from-pna/ with Bearer token
   ├─ Step 4: Receive W-3 JSON
   └─ Step 5: Store & display in UI

5. Result: W-3 Form available in pnaexchange
```

---

## Implementation Details

### Step 1: Add RegulAgent Integration Profile

**Location:** pnaexchange `apps/integrations/regulagent/`

```
apps/integrations/
├── regulagent/
│   ├── __init__.py
│   ├── services.py           # RegulAgentService class
│   ├── config.py             # RegulAgentConfig
│   └── management/
│       └── commands/
│           └── test_regulagent_connection.py
├── mix/
├── adp/
└── netsuite/
```

#### New File: `apps/integrations/regulagent/services.py`

```python
import requests
import logging
from typing import Dict, Any, Optional
from django.conf import settings
from datetime import datetime, timedelta
import json

logger = logging.getLogger(__name__)

class RegulAgentConfig:
    """Configuration for RegulAgent integration."""
    
    def __init__(self, integration_profile):
        """
        Initialize from pnaexchange IntegrationProfile.
        
        Expected config:
        {
            "base_url": "https://regulagent.example.com",
            "service_account_email": "pnaexchange-service@regulagent.com",
            "service_account_password": "encrypted-password",  # Use django-encrypted-model
        }
        """
        self.base_url = integration_profile.config.get("base_url", "").rstrip("/")
        self.service_email = integration_profile.config.get("service_account_email")
        self.service_password = integration_profile.config.get("service_account_password")
        self.tenant_id = integration_profile.tenant.id
        
    def validate(self) -> bool:
        """Check if config has required fields."""
        return bool(self.base_url and self.service_email and self.service_password)


class RegulAgentService:
    """Service to interact with RegulAgent API."""
    
    def __init__(self, config: RegulAgentConfig):
        self.config = config
        self.access_token = None
        self.token_expires_at = None
        
    def authenticate(self) -> bool:
        """
        Obtain JWT token from RegulAgent.
        
        POST /api/token/ or /api/auth/token/
        """
        if self.access_token and self.token_expires_at and datetime.utcnow() < self.token_expires_at:
            logger.info("Using cached RegulAgent token")
            return True
        
        try:
            url = f"{self.config.base_url}/api/token/"
            payload = {
                "email": self.config.service_email,
                "password": self.config.service_password,
            }
            
            response = requests.post(url, json=payload, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                self.access_token = data.get("access")
                # Token lifetime: 1 hour (from RegulAgent settings)
                self.token_expires_at = datetime.utcnow() + timedelta(minutes=55)
                logger.info("✅ RegulAgent authentication successful")
                return True
            else:
                logger.error(f"❌ RegulAgent auth failed: {response.status_code} - {response.text}")
                return False
                
        except Exception as e:
            logger.error(f"❌ RegulAgent connection error: {e}")
            return False
    
    def build_w3_from_pna(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Call RegulAgent W-3 builder endpoint.
        
        POST /api/w3/build-from-pna/
        Authorization: Bearer {token}
        """
        if not self.authenticate():
            raise Exception("Failed to authenticate with RegulAgent")
        
        try:
            url = f"{self.config.base_url}/api/w3/build-from-pna/"
            headers = {
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json",
            }
            
            response = requests.post(url, json=payload, headers=headers, timeout=30)
            
            if response.status_code == 200:
                logger.info("✅ W-3 generation successful")
                return response.json()
            else:
                logger.error(f"❌ W-3 generation failed: {response.status_code} - {response.text}")
                raise Exception(f"RegulAgent error: {response.text}")
                
        except Exception as e:
            logger.error(f"❌ W-3 build request failed: {e}")
            raise
```

#### New File: `apps/integrations/regulagent/__init__.py`

```python
from .services import RegulAgentService, RegulAgentConfig

__all__ = ['RegulAgentService', 'RegulAgentConfig']
```

### Step 2: Add IntegrationProfile Storage

**Location:** pnaexchange (existing or new) `apps/integrations/models.py`

```python
from django.db import models
from django.contrib.postgres.fields import JSONField
from encrypted_model_fields.fields import EncryptedJSONField

class IntegrationProfile(models.Model):
    """Store external service integration credentials."""
    
    INTEGRATION_TYPES = [
        ('mix', 'Mix Telematics'),
        ('adp', 'ADP'),
        ('netsuite', 'NetSuite'),
        ('regulagent', 'RegulAgent'),
    ]
    
    tenant = models.ForeignKey('Tenant', on_delete=models.CASCADE)
    integration_type = models.CharField(max_length=50, choices=INTEGRATION_TYPES)
    
    # Encrypted storage of credentials
    config = EncryptedJSONField()  # Django-encrypted-model or similar
    
    # Metadata
    is_active = models.BooleanField(default=True)
    last_tested = models.DateTimeField(null=True, blank=True)
    last_test_status = models.CharField(
        max_length=20, 
        choices=[('success', 'Success'), ('failed', 'Failed')],
        null=True,
        blank=True
    )
    last_test_error = models.TextField(null=True, blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        unique_together = ('tenant', 'integration_type')
    
    def __str__(self):
        return f"{self.get_integration_type_display()} for {self.tenant.name}"
```

### Step 3: Add Settings UI in pnaexchange

**Location:** pnaexchange `apps/core/settings/` (existing or new)

Create a Django form and view:

```python
# apps/core/settings/forms.py
from django import forms
from apps.integrations.models import IntegrationProfile

class RegulAgentIntegrationForm(forms.ModelForm):
    service_account_password = forms.CharField(
        widget=forms.PasswordInput(),
        help_text="Service account password (will be encrypted)"
    )
    
    class Meta:
        model = IntegrationProfile
        fields = []  # Custom fields only
        
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['base_url'] = forms.URLField(
            label="RegulAgent Base URL",
            initial=self.instance.config.get('base_url') if self.instance.pk else '',
        )
        self.fields['service_account_email'] = forms.EmailField(
            label="Service Account Email",
            initial=self.instance.config.get('service_account_email') if self.instance.pk else '',
        )
        self.fields['service_account_password'].initial = ''
    
    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.config = {
            'base_url': self.cleaned_data['base_url'],
            'service_account_email': self.cleaned_data['service_account_email'],
            'service_account_password': self.cleaned_data['service_account_password'],
        }
        if commit:
            instance.save()
        return instance
```

```python
# apps/core/settings/views.py
from django.views.generic import CreateView, UpdateView
from apps.integrations.models import IntegrationProfile
from .forms import RegulAgentIntegrationForm

class RegulAgentSettingsView(UpdateView):
    """Allow tenant admin to configure RegulAgent connection."""
    model = IntegrationProfile
    form_class = RegulAgentIntegrationForm
    template_name = 'settings/regulagent_settings.html'
    success_url = '/settings/integrations/'
    
    def get_object(self):
        profile, _ = IntegrationProfile.objects.get_or_create(
            tenant=self.request.user.tenant,
            integration_type='regulagent'
        )
        return profile
```

### Step 4: Add Celery Task for W-3 Generation

**Location:** pnaexchange `apps/tasks/` (or `apps/core/tasks/`)

```python
# apps/tasks/w3_generation.py
from celery import shared_task
import logging
from django.shortcuts import get_object_or_404
from apps.integrations.models import IntegrationProfile
from apps.integrations.regulagent import RegulAgentService, RegulAgentConfig
from apps.core.models import Subproject, W3GenerationResult  # TBD model

logger = logging.getLogger(__name__)

@shared_task
def generate_w3_from_events(subproject_id: int, w3a_reference: dict):
    """
    Async task: Generate W-3 form from pnaexchange events.
    
    Args:
        subproject_id: pnaexchange Subproject ID
        w3a_reference: {"type": "regulagent", "w3a_id": 123}
    """
    try:
        subproject = get_object_or_404(Subproject, id=subproject_id)
        tenant = subproject.tenant
        
        # 1. Get RegulAgent integration profile
        integration = IntegrationProfile.objects.get(
            tenant=tenant,
            integration_type='regulagent'
        )
        
        # 2. Initialize RegulAgent service
        config = RegulAgentConfig(integration)
        if not config.validate():
            raise Exception("RegulAgent configuration incomplete")
        
        service = RegulAgentService(config)
        
        # 3. Build request payload
        events = subproject.events.all()  # TBD model
        payload = {
            "well": {
                "api_number": subproject.well.api_number,
                "well_name": subproject.well.name,
                "operator": subproject.well.operator or "",
                "well_id": subproject.well.id,
            },
            "subproject": {
                "id": subproject.id,
                "name": subproject.name,
            },
            "events": [
                {
                    "date": e.date.isoformat(),
                    "event_type": e.event_type,
                    "event_detail": e.event_detail,
                    "start_time": e.start_time.isoformat() if e.start_time else None,
                    "end_time": e.end_time.isoformat() if e.end_time else None,
                    "duration_hours": e.duration_hours,
                    "work_assignment_id": e.work_assignment_id,
                    "dwr_id": e.dwr_id,
                    "input_values": e.input_values or {},
                    "transformation_rules": e.transformation_rules or {},
                }
                for e in events
            ],
            "w3a_reference": w3a_reference,
        }
        
        # 4. Call RegulAgent W-3 builder
        result = service.build_w3_from_pna(payload)
        
        # 5. Store result
        W3GenerationResult.objects.create(
            subproject=subproject,
            status='success',
            w3_form=result.get('w3'),
            metadata=result,
        )
        
        logger.info(f"✅ W-3 generation complete for subproject {subproject_id}")
        
    except Exception as e:
        logger.error(f"❌ W-3 generation failed for subproject {subproject_id}: {e}")
        W3GenerationResult.objects.create(
            subproject=subproject,
            status='failed',
            error_message=str(e),
        )
        raise
```

### Step 5: Add Connection Test Utility

**Location:** pnaexchange `apps/integrations/regulagent/management/commands/`

```python
# apps/integrations/regulagent/management/commands/test_regulagent_connection.py
from django.core.management.base import BaseCommand
from django.utils import timezone
from apps.integrations.models import IntegrationProfile
from apps.integrations.regulagent import RegulAgentService, RegulAgentConfig
import logging

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Test RegulAgent connection for a tenant'
    
    def add_arguments(self, parser):
        parser.add_argument('tenant_id', type=int, help='Tenant ID')
    
    def handle(self, *args, **options):
        tenant_id = options['tenant_id']
        
        try:
            profile = IntegrationProfile.objects.get(
                tenant_id=tenant_id,
                integration_type='regulagent'
            )
            
            config = RegulAgentConfig(profile)
            service = RegulAgentService(config)
            
            if service.authenticate():
                profile.last_test_status = 'success'
                profile.last_test_error = None
                self.stdout.write(
                    self.style.SUCCESS(
                        f"✅ RegulAgent connection successful for tenant {tenant_id}"
                    )
                )
            else:
                profile.last_test_status = 'failed'
                profile.last_test_error = "Authentication failed"
                self.stdout.write(
                    self.style.ERROR(
                        f"❌ RegulAgent connection failed for tenant {tenant_id}"
                    )
                )
            
            profile.last_tested = timezone.now()
            profile.save()
            
        except IntegrationProfile.DoesNotExist:
            self.stdout.write(
                self.style.ERROR(
                    f"RegulAgent integration not configured for tenant {tenant_id}"
                )
            )
```

### Step 6: Update RegulAgent W-3 View with Tenant Tracking

**Location:** RegulAgent `apps/public_core/views/w3_from_pna.py`

```python
# apps/public_core/views/w3_from_pna.py
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.authentication import JWTAuthentication, SessionAuthentication
from rest_framework.permissions import IsAuthenticated
from apps.public_core.services.rrc.w3.builder import W3Builder
from apps.public_core.serializers.w3_from_pna import W3FromPnaRequestSerializer
import logging

logger = logging.getLogger(__name__)

class W3FromPnaView(APIView):
    """
    Build W-3 form from pnaexchange events + W-3A reference.
    
    Expects:
    - pnaexchange to authenticate with JWT token (service account)
    - Request body with well, subproject, events, w3a_reference
    - Optional: w3a_file (multipart upload)
    
    Returns:
    - W-3 form JSON ready for RRC submission
    """
    
    authentication_classes = [JWTAuthentication, SessionAuthentication]
    permission_classes = [IsAuthenticated]
    
    def post(self, request):
        logger.info(f"W-3 build request from tenant: {request.user.tenant_id}")
        
        # 1. Validate request
        serializer = W3FromPnaRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            # 2. Extract payload
            well_data = serializer.validated_data['well']
            subproject_data = serializer.validated_data['subproject']
            events = serializer.validated_data['events']
            w3a_reference = serializer.validated_data['w3a_reference']
            
            logger.info(f"Processing {len(events)} events for well {well_data['api_number']}")
            
            # 3. Load W-3A form
            from apps.public_core.services.rrc.w3.extraction import load_w3a_form
            w3a_form = load_w3a_form(w3a_reference, request)
            
            # 4. Build W-3
            builder = W3Builder(w3a_form)
            w3_result = builder.build_w3_form(events)
            
            # 5. Log success (for audit trail)
            logger.info(f"✅ W-3 generation successful for well {well_data['api_number']}")
            
            # 6. Return response
            return Response({
                "status": "success",
                "w3": w3_result,
                "tenant_id": str(request.user.tenant_id),
                "timestamp": timezone.now().isoformat(),
            }, status=status.HTTP_200_OK)
            
        except Exception as e:
            logger.error(f"❌ W-3 generation failed: {e}", exc_info=True)
            return Response({
                "status": "error",
                "detail": str(e),
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
```

---

## Security Considerations

### 1. Credential Encryption
- Use Django-encrypted-model or django-cryptography
- Encrypt service account password in `IntegrationProfile.config`
- Rotate credentials regularly

### 2. Token Management
- Cache JWT token with expiry check (55 min for 1-hour lifetime)
- Auto-refresh on expiry
- Never log tokens

### 3. API Rate Limiting
- Implement per-tenant rate limiting on W-3 endpoint (RegulAgent)
- Recommended: 100 requests/hour per tenant
- Use DRF throttling: `ScopedRateThrottle`

### 4. Audit Trail
- Log all W-3 generation requests (who, when, well, result)
- Include pnaexchange tenant ID in logs
- Use simple-history or django-audit-log

### 5. IP Whitelisting (Optional)
- If pnaexchange has fixed IP(s), configure firewall rules
- RegulAgent can restrict API to trusted IPs only

---

## Configuration Example

### pnaexchange: `.env` or settings

```
REGULAGENT_BASE_URL=https://regulagent.example.com
REGULAGENT_SERVICE_EMAIL=pnaexchange-service@regulagent.com
REGULAGENT_SERVICE_PASSWORD=secure-password-here
```

### RegulAgent: Create Service Account

```bash
# In RegulAgent container/shell
python manage.py createsuperuser --username pnaexchange-service --email pnaexchange-service@regulagent.com
# Or create via tenant user management UI
```

---

## Deployment Checklist

### RegulAgent
- [ ] Ensure JWT auth is **enabled** on W3FromPnaView
- [ ] Create service account user: `pnaexchange-service@regulagent.com`
- [ ] Test token endpoint: `POST /api/token/`
- [ ] Configure CORS to allow pnaexchange origin
- [ ] Set up logging for audit trail

### pnaexchange
- [ ] Add `IntegrationProfile` model
- [ ] Implement `RegulAgentService` class
- [ ] Create settings UI for RegulAgent configuration
- [ ] Add Celery task for async W-3 generation
- [ ] Implement test connection command
- [ ] Add encrypted credential storage
- [ ] Create W-3 generation UI trigger

---

## Testing Flow

### 1. Unit Test: Service Authentication
```python
def test_regulagent_authentication():
    config = RegulAgentConfig(integration_profile)
    service = RegulAgentService(config)
    assert service.authenticate() == True
    assert service.access_token is not None
```

### 2. Integration Test: W-3 Generation
```python
def test_w3_generation_e2e():
    # Create test integration profile
    # Call W-3 generation task
    # Verify W-3 result stored
    # Check response format
```

### 3. Live Test (After Deployment)
```bash
# Test pnaexchange can call RegulAgent
curl -X POST http://pnaexchange.local/api/w3/generate/ \
  -H "Content-Type: application/json" \
  -d '{"subproject_id": 123, "w3a_reference": {...}}'
```

---

## Summary

| Component | Technology | Location |
|-----------|-----------|----------|
| **Auth Method** | JWT (Bearer token) | RegulAgent SIMPLE_JWT |
| **Credentials Storage** | Encrypted JSONField | pnaexchange IntegrationProfile |
| **Service Account** | Django User (tenant scoped) | RegulAgent |
| **Token Refresh** | Auto with cache/expiry | RegulAgentService |
| **Request Flow** | HTTP POST + Bearer header | pnaexchange → RegulAgent |
| **Rate Limiting** | DRF throttling | RegulAgent (optional) |
| **Audit Logging** | Django signals + logging | Both systems |

This design **reuses existing JWT infrastructure** without creating a separate API key system, maintaining consistency across both platforms.

