# Frontend Integration Guide

Quick reference for wiring up RegulAgent API endpoints to frontend components.

---

## **Feature → Endpoint Mapping**

### **1. Tenant Settings Page**

**Route**: `/settings/guardrails`

| Component | Endpoint | Method |
|-----------|----------|--------|
| Load current policy | `/api/tenant/settings/guardrails/` | GET |
| Get available profiles | `/api/tenant/settings/guardrails/risk-profiles/` | GET |
| Update policy | `/api/tenant/settings/guardrails/` | PATCH |
| Validate before save | `/api/tenant/settings/guardrails/validate/` | GET |

**Example Component:**
```typescript
// TenantSettingsPage.tsx
const [policy, setPolicy] = useState(null);
const [profiles, setProfiles] = useState([]);

useEffect(() => {
  // Load current policy
  fetch('/api/tenant/settings/guardrails/', {
    headers: { 'Authorization': `Bearer ${token}` }
  })
  .then(res => res.json())
  .then(data => setPolicy(data));
  
  // Load available profiles
  fetch('/api/tenant/settings/guardrails/risk-profiles/', {
    headers: { 'Authorization': `Bearer ${token}` }
  })
  .then(res => res.json())
  .then(data => setProfiles(data.profiles));
}, []);

const updatePolicy = async (changes) => {
  const response = await fetch('/api/tenant/settings/guardrails/', {
    method: 'PATCH',
    headers: {
      'Authorization': `Bearer ${token}`,
      'Content-Type': 'application/json'
    },
    body: JSON.stringify(changes)
  });
  
  if (response.ok) {
    const data = await response.json();
    showNotification(data.message);
    if (data.warnings) {
      showWarnings(data.warnings);
    }
  }
};
```

---

### **2. Chat Interface**

**Route**: `/chat/{thread_id}`

| Component | Endpoint | Method |
|-----------|----------|--------|
| List threads | `/api/chat/threads/` | GET |
| Create thread | `/api/chat/threads/` | POST |
| Load messages | `/api/chat/threads/{id}/messages/` | GET |
| Send message | `/api/chat/threads/{id}/messages/` | POST |
| Poll for response | `/api/chat/threads/{id}/messages/{msg_id}/status/` | GET |

**Example Component:**
```typescript
// ChatInterface.tsx
const sendMessage = async (content: string, allowChanges: boolean) => {
  // 1. Send message
  const response = await fetch(`/api/chat/threads/${threadId}/messages/`, {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${token}`,
      'Content-Type': 'application/json'
    },
    body: JSON.stringify({
      content,
      allow_plan_changes: allowChanges,
      async: true  // Use async processing
    })
  });
  
  if (response.status === 202) {
    const { user_message, status_url } = await response.json();
    
    // 2. Add user message to UI
    addMessage(user_message);
    
    // 3. Poll for assistant response
    const pollInterval = setInterval(async () => {
      const statusResponse = await fetch(status_url, {
        headers: { 'Authorization': `Bearer ${token}` }
      });
      
      const status = await statusResponse.json();
      
      if (status.status === 'completed' && status.assistant_message) {
        clearInterval(pollInterval);
        addMessage(status.assistant_message);
      }
    }, 1000);  // Poll every second
  }
};
```

---

### **3. Plan Viewer/Editor**

**Route**: `/plans/{plan_id}`

| Component | Endpoint | Method |
|-----------|----------|--------|
| Load plan | `/api/plans/{plan_id}/` | GET |
| Get version history | `/api/plans/{plan_id}/versions/` | GET |
| Compare versions | `/api/plans/compare/{id1}/{id2}/` | GET |
| Update status | `/api/plans/{plan_id}/status/modify/` | PATCH |
| Approve plan | `/api/plans/{plan_id}/status/approve/` | PATCH |
| File plan | `/api/plans/{plan_id}/status/file/` | PATCH |

**Example Component:**
```typescript
// PlanViewer.tsx
const [plan, setPlan] = useState(null);
const [versions, setVersions] = useState([]);
const [diff, setDiff] = useState(null);

useEffect(() => {
  // Load full plan
  fetch(`/api/plans/${planId}/`, {
    headers: { 'Authorization': `Bearer ${token}` }
  })
  .then(res => res.json())
  .then(data => setPlan(data));
  
  // Load version history
  fetch(`/api/plans/${planId}/versions/`, {
    headers: { 'Authorization': `Bearer ${token}` }
  })
  .then(res => res.json())
  .then(data => setVersions(data.versions));
}, [planId]);

const compareVersions = async (v1, v2) => {
  const response = await fetch(`/api/plans/compare/${v1}/${v2}/`, {
    headers: { 'Authorization': `Bearer ${token}` }
  });
  
  const data = await response.json();
  setDiff(data);
  
  // Highlight changes in UI
  data.steps.forEach(step => {
    highlightStep(step.step_id, step.highlight_color);
  });
};

const approvePlan = async () => {
  await fetch(`/api/plans/${planId}/status/approve/`, {
    method: 'PATCH',
    headers: { 'Authorization': `Bearer ${token}` }
  });
  
  showNotification('Plan approved!');
  refreshPlan();
};
```

---

### **4. RRC Filing Tracker**

**Route**: `/filings`

| Component | Endpoint | Method |
|-----------|----------|--------|
| List outcomes | `/api/chat/outcomes/` | GET |
| Get outcome details | `/api/chat/outcomes/{id}/` | GET |
| Create outcome | `/api/chat/outcomes/` | POST |
| Mark approved | `/api/chat/outcomes/{id}/approve/` | PATCH |
| Mark rejected | `/api/chat/outcomes/{id}/reject/` | PATCH |
| Get statistics | `/api/chat/outcomes/stats/` | GET |

**Example Component:**
```typescript
// FilingTracker.tsx
const [outcomes, setOutcomes] = useState([]);
const [stats, setStats] = useState(null);

useEffect(() => {
  // Load outcomes
  fetch('/api/chat/outcomes/?status=pending', {
    headers: { 'Authorization': `Bearer ${token}` }
  })
  .then(res => res.json())
  .then(data => {
    setOutcomes(data.outcomes);
    setStats(data.summary);
  });
}, []);

const fileWithRRC = async (planId: string, filingNumber: string) => {
  const response = await fetch('/api/chat/outcomes/', {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${token}`,
      'Content-Type': 'application/json'
    },
    body: JSON.stringify({
      plan_id: planId,
      filing_number: filingNumber,
      agency: 'RRC'
    })
  });
  
  if (response.ok) {
    const outcome = await response.json();
    showNotification(`Filed as ${outcome.filing_number}`);
    refreshOutcomes();
  }
};

const markApproved = async (outcomeId: number) => {
  const notes = prompt('Enter reviewer notes:');
  const reviewer = prompt('Reviewer name:');
  
  await fetch(`/api/chat/outcomes/${outcomeId}/approve/`, {
    method: 'PATCH',
    headers: {
      'Authorization': `Bearer ${token}`,
      'Content-Type': 'application/json'
    },
    body: JSON.stringify({
      reviewer_notes: notes,
      reviewer_name: reviewer
    })
  });
  
  showNotification('✅ Marked as approved! Learning loop triggered.');
  refreshOutcomes();
};
```

---

### **5. Well History Dashboard**

**Route**: `/wells`

| Component | Endpoint | Method |
|-----------|----------|--------|
| List wells interacted with | `/api/tenant/wells/history/` | GET |
| Get well details | `/api/tenant/wells/{api}/` | GET |
| Bulk get wells | `/api/tenant/wells/bulk/` | POST |

**Example Component:**
```typescript
// WellHistoryDashboard.tsx
const [wells, setWells] = useState([]);

useEffect(() => {
  fetch('/api/tenant/wells/history/', {
    headers: { 'Authorization': `Bearer ${token}` }
  })
  .then(res => res.json())
  .then(data => setWells(data.wells));
}, []);

// Display wells with interaction history
wells.map(well => (
  <WellCard
    key={well.api14}
    api={well.api14}
    operator={well.operator_name}
    lastInteraction={well.tenant_interaction.last_interaction_type}
    interactionCount={well.tenant_interaction.interaction_count}
  />
));
```

---

### **6. Analytics Dashboard**

**Route**: `/analytics`

| Component | Endpoint | Method |
|-----------|----------|--------|
| Get outcome stats | `/api/chat/outcomes/stats/` | GET |

**Example Component:**
```typescript
// AnalyticsDashboard.tsx
const [stats, setStats] = useState(null);

useEffect(() => {
  fetch('/api/chat/outcomes/stats/?district=08A', {
    headers: { 'Authorization': `Bearer ${token}` }
  })
  .then(res => res.json())
  .then(data => setStats(data));
}, []);

// Display stats
<StatsCard
  title="Approval Rate"
  value={`${(stats.approval_rate * 100).toFixed(1)}%`}
  subtitle={`${stats.approved} of ${stats.total_outcomes} plans`}
/>
<StatsCard
  title="Avg Review Time"
  value={`${stats.avg_review_duration_days} days`}
/>
<StatsCard
  title="Confidence Score"
  value={stats.avg_confidence.toFixed(2)}
/>
```

---

## **Common Patterns**

### **Authentication**

```typescript
// auth.ts
export const getAuthHeaders = () => {
  const token = localStorage.getItem('access_token');
  return {
    'Authorization': `Bearer ${token}`,
    'Content-Type': 'application/json'
  };
};

export const refreshTokenIfNeeded = async () => {
  const refresh = localStorage.getItem('refresh_token');
  const response = await fetch('/api/token/refresh/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ refresh })
  });
  
  if (response.ok) {
    const { access } = await response.json();
    localStorage.setItem('access_token', access);
    return access;
  }
  
  // Token refresh failed, redirect to login
  window.location.href = '/login';
};
```

### **Error Handling**

```typescript
// api.ts
export const apiCall = async (url: string, options: RequestInit = {}) => {
  try {
    const response = await fetch(url, {
      ...options,
      headers: {
        ...getAuthHeaders(),
        ...options.headers
      }
    });
    
    if (response.status === 401) {
      // Token expired, try refresh
      await refreshTokenIfNeeded();
      return apiCall(url, options);  // Retry
    }
    
    if (!response.ok) {
      const error = await response.json();
      throw new Error(error.error || 'Request failed');
    }
    
    return await response.json();
  } catch (error) {
    console.error('API Error:', error);
    throw error;
  }
};
```

### **Async Message Polling**

```typescript
// chat-utils.ts
export const pollForResponse = async (
  statusUrl: string,
  onComplete: (message: any) => void,
  onError: (error: string) => void
) => {
  const maxAttempts = 60;  // 60 seconds max
  let attempts = 0;
  
  const poll = setInterval(async () => {
    attempts++;
    
    if (attempts >= maxAttempts) {
      clearInterval(poll);
      onError('Response timeout');
      return;
    }
    
    try {
      const response = await apiCall(statusUrl);
      
      if (response.status === 'completed') {
        clearInterval(poll);
        onComplete(response.assistant_message);
      } else if (response.status === 'error') {
        clearInterval(poll);
        onError(response.error);
      }
    } catch (error) {
      clearInterval(poll);
      onError(error.message);
    }
  }, 1000);
  
  return () => clearInterval(poll);  // Cleanup function
};
```

---

## **UI State Management**

### **Tenant Settings State**

```typescript
// store/guardrails.ts
interface GuardrailState {
  policy: TenantGuardrailPolicy | null;
  profiles: RiskProfile[];
  loading: boolean;
  error: string | null;
}

const useGuardrails = () => {
  const [state, setState] = useState<GuardrailState>({
    policy: null,
    profiles: [],
    loading: true,
    error: null
  });
  
  const loadPolicy = async () => {
    try {
      const data = await apiCall('/api/tenant/settings/guardrails/');
      setState(prev => ({ ...prev, policy: data, loading: false }));
    } catch (error) {
      setState(prev => ({ ...prev, error: error.message, loading: false }));
    }
  };
  
  const updatePolicy = async (changes: Partial<TenantGuardrailPolicy>) => {
    try {
      const data = await apiCall('/api/tenant/settings/guardrails/', {
        method: 'PATCH',
        body: JSON.stringify(changes)
      });
      setState(prev => ({ ...prev, policy: { ...prev.policy, ...changes } }));
      return data;
    } catch (error) {
      throw error;
    }
  };
  
  return { ...state, loadPolicy, updatePolicy };
};
```

---

## **TypeScript Types**

```typescript
// types.ts

export interface TenantGuardrailPolicy {
  id: number;
  tenant_id: string;
  risk_profile: 'conservative' | 'balanced' | 'aggressive' | 'custom';
  require_confirmation_above_risk: number;
  max_material_delta_percent: number;
  max_steps_removed: number;
  allow_new_violations: boolean;
  max_modifications_per_session: number;
  allowed_operations: string[];
  blocked_operations: string[];
  district_overrides: Record<string, any>;
  global_baseline: GlobalBaseline;
  is_stricter_than_global: boolean;
}

export interface RegulatorOutcome {
  id: number;
  plan_id: string;
  api: string;
  filing_number: string;
  status: 'pending' | 'under_review' | 'approved' | 'rejected';
  agency: string;
  filed_at: string;
  approved_at?: string;
  review_duration_days?: number;
  confidence_score: number;
  modifications_count: number;
}

export interface ChatThread {
  id: number;
  title: string;
  well: {
    api: string;
    operator: string;
  };
  baseline_plan_id: string;
  current_plan_id: string;
  message_count: number;
  is_active: boolean;
  created_by: string;
  created_at: string;
}

export interface ChatMessage {
  id: number;
  role: 'user' | 'assistant' | 'system';
  content: string;
  tool_calls?: any[];
  tool_results?: any[];
  created_at: string;
}
```

---

## **Testing Endpoints**

```bash
# Set up environment
export API_URL="http://localhost:8001"
export TOKEN="your-jwt-token-here"

# Test guardrails
curl -X GET $API_URL/api/tenant/settings/guardrails/ \
  -H "Authorization: Bearer $TOKEN"

# Test chat
curl -X POST $API_URL/api/chat/threads/1/messages/ \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"content": "Test message", "allow_plan_changes": true}'

# Test outcomes
curl -X GET $API_URL/api/chat/outcomes/stats/ \
  -H "Authorization: Bearer $TOKEN"
```

---

**Last Updated**: 2025-11-02  
**Frontend Framework**: React/Next.js recommended  
**State Management**: Redux Toolkit or Zustand recommended

