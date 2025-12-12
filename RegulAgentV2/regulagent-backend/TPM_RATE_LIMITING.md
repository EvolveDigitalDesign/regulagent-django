# Token Per Minute (TPM) Rate Limiting Implementation

## Problem

With GPT-4o at 30k TPM limit, the backend was hitting 429 rate limit errors when making concurrent chat requests:

```
20:54:31 - First OpenAI call: prompt=12,767 tokens
20:54:32 - Second OpenAI call: prompt=12,880 tokens → 429 TOO MANY REQUESTS
```

**Analysis:**
- Each call uses ~12,800 tokens = 42% of 30k TPM limit
- Two consecutive calls = 84% of limit used in 1 second
- With concurrent users, TPM limit was exhausted immediately
- Standard OpenAI retries weren't aggressive enough

## Root Cause

The `process_chat_with_openai` function implements a tool-using loop:

1. **Call 1:** AI decides to use `remove_steps` tool
2. **Tool execution** (no API call)
3. **Call 2:** AI responds to tool result → **429 Rate Limited**

This is the correct OpenAI agentic pattern, but each iteration uses significant tokens.

## Solution: TokenRateLimiter

Implemented a `TokenRateLimiter` class that:
- Tracks cumulative tokens used in the current 60-second window
- Calculates whether a new request would exceed the TPM limit
- Sleeps proactively to stay under the limit
- Automatically resets the window every 60 seconds

### Configuration

```python
# In openai_config.py
_token_limiter = TokenRateLimiter(
    tokens_per_minute=int(os.getenv("OPENAI_TPM_LIMIT", "30000")),
    window_seconds=60
)
```

### Usage

Before making an OpenAI API call:

```python
from apps.public_core.services.openai_config import check_rate_limit

# Check rate limit (waits if needed)
check_rate_limit(estimated_tokens=15000)

# Now safe to make the call
response = client.chat.completions.create(...)

# Log usage (automatically updates rate limiter)
log_openai_usage(response, "chat_thread_89")
```

## Changes Made

### 1. `apps/public_core/services/openai_config.py`
- Added `TokenRateLimiter` class for TPM tracking
- Added `check_rate_limit()` function to throttle before requests
- Updated `get_openai_client()` with `max_retries=5` and `timeout=120`
- Updated `log_openai_usage()` to track tokens in rate limiter

### 2. `apps/assistant/services/openai_service.py`
- Import `check_rate_limit` from openai_config
- Call `check_rate_limit(estimated_tokens=15000)` before each OpenAI API call in the tool loop

## How It Works

### Example Scenario

**Before fix:**
```
User: "Remove the UQW plug"
  ↓
Call 1: 12,833 tokens → 200 OK (42% of TPM)
Call 2: 12,952 tokens → 429 RATE LIMITED! (84% of TPM exceeded)
```

**After fix:**
```
User: "Remove the UQW plug"
  ↓
check_rate_limit(15000)
  - Current usage: 0
  - Would be: 15,000 (50% of TPM)
  - OK, proceed ✓
  ↓
Call 1: 12,833 tokens → 200 OK (43% of TPM)
  ↓
check_rate_limit(15000)
  - Current usage: 12,833
  - Would be: 27,833 (93% of TPM)
  - Sleep 0.5s to spread requests ✓
  ↓
Call 2: 12,952 tokens → 200 OK (85% of TPM)
```

## Benefits

1. **Prevents 429 errors** - Proactively throttles before hitting limit
2. **Smoother rate limiting** - Better than OpenAI's automatic retry which causes 20+ second delays
3. **Concurrent-friendly** - Handles multiple users making requests simultaneously
4. **Observable** - Logs when throttling occurs for debugging
5. **Configurable** - TPM limit can be set via `OPENAI_TPM_LIMIT` env var

## Performance Impact

- **Minimal latency** - Most requests proceed without delay
- **Burst protection** - When limit approached, adds small sleep (usually <1 second)
- **Recovery time** - System automatically recovers after 60 seconds

## Testing

To verify the fix works:

```python
# Force a test by reducing TPM limit
export OPENAI_TPM_LIMIT=1000

# Make concurrent chat requests - should no longer get 429 errors
```

Monitor logs for messages like:
```
[Rate Limiter] TPM limit approaching. Waiting 0.5s before next request.
```

## Future Improvements

1. **Per-tenant quotas** - Allocate TPM budget per customer
2. **Metrics tracking** - Export throttling stats to Datadog/New Relic
3. **Adaptive backoff** - Learn optimal delays based on historical patterns
4. **Batch API integration** - Use cheaper batch API for non-time-sensitive operations


