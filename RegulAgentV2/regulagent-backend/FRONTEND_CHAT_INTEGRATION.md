# Frontend Chat Integration Guide

## Problem
After sending a chat message, the frontend doesn't display the assistant's response.

## Root Cause
The backend processes messages **asynchronously** via Celery. When you POST a message, you get a `202 Accepted` response immediately, but the AI response takes 2-10 seconds to generate.

The frontend needs to **poll** for new messages after submission.

---

## Solution: Message Polling

### API Flow

```
1. User types message
2. Frontend: POST /api/chat/threads/{thread_id}/messages/
3. Backend: Returns 202 with user_message (queued for processing)
4. Frontend: Displays user message immediately
5. Frontend: Starts polling GET /api/chat/threads/{thread_id}/messages/
6. Backend (Celery): Processes message with OpenAI
7. Backend (Celery): Creates assistant message
8. Frontend (polling): Detects new assistant message
9. Frontend: Displays assistant message
10. Frontend: Stops polling
```

---

## Implementation

### TypeScript/React Example

```typescript
interface Message {
  id: number;
  role: 'user' | 'assistant' | 'tool';
  content: string;
  created_at: string;
}

interface ChatThread {
  id: number;
  messages: Message[];
}

async function sendMessage(
  threadId: number,
  content: string,
  allowPlanChanges: boolean = false
): Promise<void> {
  // 1. Send message to backend
  const response = await fetch(`/api/chat/threads/${threadId}/messages/`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${getAuthToken()}`
    },
    body: JSON.stringify({
      content,
      allow_plan_changes: allowPlanChanges,
      max_tool_calls: 10
    })
  });

  if (!response.ok) {
    throw new Error(`Failed to send message: ${response.statusText}`);
  }

  const { user_message, status_url, polling_interval_ms } = await response.json();

  // 2. Display user message immediately
  displayMessage(user_message);

  // 3. Show "thinking" indicator
  showThinkingIndicator();

  // 4. Poll for assistant response
  await pollForResponse(threadId, user_message.id, polling_interval_ms || 1000);

  // 5. Hide "thinking" indicator
  hideThinkingIndicator();
}

async function pollForResponse(
  threadId: number,
  afterMessageId: number,
  intervalMs: number = 1000,
  maxAttempts: number = 30
): Promise<void> {
  for (let attempt = 0; attempt < maxAttempts; attempt++) {
    try {
      // Fetch latest messages
      const response = await fetch(
        `/api/chat/threads/${threadId}/messages/?limit=50&offset=0`,
        {
          headers: { 'Authorization': `Bearer ${getAuthToken()}` }
        }
      );

      if (!response.ok) {
        console.error('Failed to fetch messages');
        await sleep(intervalMs);
        continue;
      }

      const { messages } = await response.json();

      // Check for assistant message created after user message
      const assistantMessage = messages.find(
        (m: Message) => m.role === 'assistant' && m.id > afterMessageId
      );

      if (assistantMessage) {
        displayMessage(assistantMessage);
        return; // Success! Stop polling
      }

      // Wait before next poll
      await sleep(intervalMs);

    } catch (error) {
      console.error('Error polling for messages:', error);
      await sleep(intervalMs);
    }
  }

  // Timeout - no response after 30 attempts
  showError('AI response timed out. Please try again.');
}

function sleep(ms: number): Promise<void> {
  return new Promise(resolve => setTimeout(resolve, ms));
}

function displayMessage(message: Message): void {
  // Add message to UI
  const messageElement = document.createElement('div');
  messageElement.className = `message message-${message.role}`;
  messageElement.textContent = message.content;
  document.getElementById('chat-messages')?.appendChild(messageElement);
  
  // Scroll to bottom
  messageElement.scrollIntoView({ behavior: 'smooth' });
}

function showThinkingIndicator(): void {
  const indicator = document.createElement('div');
  indicator.id = 'thinking-indicator';
  indicator.className = 'message message-assistant thinking';
  indicator.textContent = 'RegulAgent AI is thinking...';
  document.getElementById('chat-messages')?.appendChild(indicator);
}

function hideThinkingIndicator(): void {
  document.getElementById('thinking-indicator')?.remove();
}

function showError(message: string): void {
  alert(message); // Or use your UI's error display mechanism
}
```

---

## Alternative: Server-Sent Events (SSE)

For a better user experience without polling, consider Server-Sent Events:

### Backend (Django + SSE)

```python
from django.http import StreamingHttpResponse
import time
import json

def chat_message_stream(request, thread_id, after_message_id):
    """
    Stream new messages as they arrive.
    
    GET /api/chat/threads/{thread_id}/messages/stream?after_message_id=16
    """
    def event_stream():
        seen_ids = set()
        max_wait = 30  # seconds
        start_time = time.time()
        
        while (time.time() - start_time) < max_wait:
            # Check for new messages
            new_messages = ChatMessage.objects.filter(
                thread_id=thread_id,
                id__gt=after_message_id
            ).exclude(id__in=seen_ids).order_by('created_at')
            
            for msg in new_messages:
                seen_ids.add(msg.id)
                yield f"data: {json.dumps({
                    'id': msg.id,
                    'role': msg.role,
                    'content': msg.content,
                    'created_at': msg.created_at.isoformat()
                })}\n\n"
                
                # If assistant responded, we're done
                if msg.role == 'assistant':
                    return
            
            time.sleep(0.5)  # Poll every 500ms
        
        # Timeout
        yield f"data: {json.dumps({'error': 'timeout'})}\n\n"
    
    return StreamingHttpResponse(
        event_stream(),
        content_type='text/event-stream'
    )
```

### Frontend (SSE)

```typescript
function pollForResponseSSE(threadId: number, afterMessageId: number): void {
  const eventSource = new EventSource(
    `/api/chat/threads/${threadId}/messages/stream?after_message_id=${afterMessageId}`,
    { headers: { 'Authorization': `Bearer ${getAuthToken()}` } }
  );

  eventSource.onmessage = (event) => {
    const data = JSON.parse(event.data);
    
    if (data.error) {
      showError('AI response timed out');
      eventSource.close();
      return;
    }
    
    displayMessage(data);
    
    if (data.role === 'assistant') {
      eventSource.close(); // Done!
    }
  };

  eventSource.onerror = (error) => {
    console.error('SSE error:', error);
    eventSource.close();
    showError('Connection error');
  };
}
```

---

## Quick Fix (Simplest)

If you just want to see messages without implementing polling:

**Manually refresh the messages list after ~5 seconds:**

```typescript
// After sending message
sendMessage(content);

// Wait for AI to respond
setTimeout(() => {
  fetchAndDisplayMessages();
}, 5000);
```

---

## Testing

1. Send a message
2. Check browser DevTools Network tab:
   - POST returns `202 Accepted` with `user_message`
   - GET polls `/api/chat/threads/{id}/messages/` every 1 second
   - After 2-10 seconds, GET returns array with new `assistant` message
3. Assistant message appears in UI

---

## Troubleshooting

**Problem**: "Message disappears and never comes back"
- **Solution**: Implement polling as shown above

**Problem**: "Polling never finds the response"
- **Check**: Backend logs - is Celery running?
- **Check**: Database - does assistant message exist?
- **Check**: Frontend token - is it valid?

**Problem**: "Response takes too long"
- **Check**: OpenAI API key is set
- **Check**: Celery worker logs for errors
- **Increase**: `maxAttempts` in polling loop

---

## Performance Notes

- **Polling interval**: 1 second is fine for chat UX
- **Max attempts**: 30 seconds should be enough (most responses in 2-10s)
- **Connection pooling**: Reuse fetch connections
- **Rate limiting**: Backend may throttle rapid polling

