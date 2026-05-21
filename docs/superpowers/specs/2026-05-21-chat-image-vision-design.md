# Chat Image Vision Design

**Date:** 2026-05-21
**Status:** Approved
**Scope:** `apps/server`, `packages/api-client`, `apps/desktop`

## Overview

Add first-class image input to chat so ANIMA can look at user-provided images during a turn and revisit saved images in the same thread history. V1 uses provider-native vision models only. If the selected provider/model cannot process images, the server returns a clear configuration error instead of silently captioning or dropping the image.

Images are runtime conversation artifacts, not long-term memories in V1. They are saved with thread history so the current thread can display and re-use them, but they are not embedded, searched, summarized into soul memory, or promoted by the Soul Writer.

## Goals

- Let the desktop chat attach one or more images to a user message.
- Persist image files under per-user runtime storage and store metadata on the user `RuntimeMessage`.
- Include saved image attachments in the model request when the message is in active context.
- Return attachment metadata through chat history and thread-message APIs so the UI can render saved images.
- Reject unsupported file types, oversized images, and non-vision model configurations with explicit errors.

## Non-Goals

- Long-term image memory, image embeddings, or visual search.
- A fallback captioning model.
- General file/PDF/audio support.
- Editing generated images or creating image outputs.
- Sending images to text-only models as lossy descriptions.

## Data Model

Reuse `RuntimeMessage.content_json` for V1 instead of adding a separate attachment table. `content_text` remains the user's text prompt. Image metadata lives under `content_json.attachments`.

Example:

```json
{
  "attachments": [
    {
      "id": "img_8f7c2d9b",
      "kind": "image",
      "mimeType": "image/png",
      "filename": "diagram.png",
      "sizeBytes": 184203,
      "sha256": "...",
      "storagePath": "users/7/attachments/chat/img_8f7c2d9b.png"
    }
  ]
}
```

Storage path:

```text
{settings.data_dir}/users/{user_id}/attachments/chat/{attachment_id}.{ext}
```

The API never returns `storagePath` directly. It returns an authenticated attachment URL derived from the owning message and attachment id.

## Runtime Types

Add an attachment representation in `services/agent/state.py`:

```python
@dataclass(frozen=True, slots=True)
class StoredAttachment:
    id: str
    kind: Literal["image"]
    mime_type: str
    path: str
    filename: str | None = None
    size_bytes: int | None = None

@dataclass(frozen=True, slots=True)
class StoredMessage:
    role: str
    content: str
    attachments: tuple[StoredAttachment, ...] = ()
    ...
```

Only `user` messages may carry image attachments in V1. Assistant, tool, summary, and approval messages remain text-only.

## API Contract

Extend `ChatRequest`:

```json
{
  "message": "what is this?",
  "userId": 7,
  "threadId": 42,
  "stream": true,
  "attachments": [
    {
      "kind": "image",
      "filename": "board.jpg",
      "mimeType": "image/jpeg",
      "data": "<base64 bytes, no data-url prefix>"
    }
  ]
}
```

The server decodes, validates, saves, and persists attachments during turn preparation. This avoids a staged-upload lifecycle and keeps the first implementation transactional from the user's perspective.

Extend `ChatHistoryMessage` and thread-message responses:

```json
{
  "id": 123,
  "role": "user",
  "content": "what is this?",
  "attachments": [
    {
      "id": "img_8f7c2d9b",
      "kind": "image",
      "mimeType": "image/jpeg",
      "filename": "board.jpg",
      "sizeBytes": 184203,
      "url": "/api/chat/messages/123/attachments/img_8f7c2d9b"
    }
  ]
}
```

Add authenticated endpoint:

```text
GET /api/chat/messages/{message_id}/attachments/{attachment_id}
```

It verifies the unlock session, loads the message, checks `message.user_id`, finds the attachment in `content_json`, resolves the stored path under the user's data directory, and returns the file with the stored MIME type.

## Validation

V1 accepts:

- `image/png`
- `image/jpeg`
- `image/webp`
- `image/gif`

V1 rejects SVG because it can carry script-like payloads in browser contexts. The server validates both declared MIME type and magic bytes. Maximum size defaults to 10 MB per image and 4 images per message, with settings-backed constants so the limits can be changed later.

Validation happens before creating the run. If validation fails, the user message and files are not persisted.

## Model Capability

Before persisting an image turn, the server checks whether the selected provider/model is allowed to receive image blocks.

The first pass should use a conservative helper:

```python
def supports_image_input(provider: str, model: str) -> bool:
    ...
```

Supported patterns include known vision-capable OpenAI/OpenRouter/Ollama-compatible names such as `gpt-4o`, `gpt-4.1`, `gpt-5`, `vision`, `llava`, `qwen-vl`, `qwen2.5-vl`, `qwen2.5vl`, and `llama3.2-vision`. This helper should be covered by tests and easy to extend from config later.

If unsupported, raise an `LLMConfigError` with a message like:

```text
The selected model cannot process image attachments. Choose a vision-capable model or remove the image.
```

## Provider Serialization

The runtime stays provider-neutral. It passes `StoredMessage.attachments` through `HumanMessage` content blocks.

OpenAI-compatible payloads should serialize a user message with text and images as:

```json
{
  "role": "user",
  "content": [
    { "type": "text", "text": "what is this?" },
    {
      "type": "image_url",
      "image_url": {
        "url": "data:image/png;base64,..."
      }
    }
  ]
}
```

For Anthropic-compatible payloads, use Anthropic image source blocks if that adapter is enabled:

```json
{
  "type": "image",
  "source": {
    "type": "base64",
    "media_type": "image/png",
    "data": "..."
  }
}
```

The adapter reads bytes from the saved attachment path when building the provider payload. It should not store base64 in the database.

## Agent Context and History

`load_thread_history()` deserializes `content_json.attachments` into `StoredAttachment` instances. Existing history-trimming and compaction behavior continues to decide which messages are in context. If a user image message remains in context, the image is sent again. If it is compacted out of context, it is not sent again.

Compaction summaries remain text-only. V1 should not ask a text summarizer to describe images. The summary transcript can render image messages as placeholders such as:

```text
User: what is this? [image: board.jpg]
```

Transcript archives should preserve attachment metadata, not binary data.

## Desktop UI

Add image attachment support to the chat input:

- Image button opens a file picker accepting PNG, JPEG, WebP, and GIF.
- Selected images render as removable thumbnails above the text area.
- Send is enabled when either text or at least one image is present.
- On send, convert selected files to base64 request attachments.
- Render saved user-message attachments from history using their authenticated URLs.
- Clear selected images after a successful send.

The UI should show server validation errors inline in the existing chat error area.

## Error Handling

- Invalid type: `400 Bad Request`
- Too large: `413 Payload Too Large`
- Too many images: `400 Bad Request`
- Unsupported model/provider: `503 Service Unavailable` via existing LLM configuration error handling
- Missing attachment file on history render: return `404` from the attachment endpoint and render a broken/missing state in the UI

## Testing

Server tests:

- `ChatRequest` accepts valid image attachments and rejects malformed payloads.
- Attachment validation rejects MIME spoofing, SVG, oversized files, and too many images.
- `append_user_message` persists attachment metadata in `content_json`.
- `load_thread_history` returns `StoredMessage.attachments`.
- OpenAI-compatible serialization emits text + `image_url` content blocks.
- Unsupported models fail before message persistence.
- Attachment file endpoint enforces user ownership.

Desktop/client tests:

- API client sends attachment payloads for streaming chat.
- Chat input can attach, preview, remove, and submit images.
- History renders saved image attachments.

Verification:

- `uv run pytest -q apps/server/tests/test_agent_persistence.py apps/server/tests/test_agent_openai_compatible_client.py`
- relevant chat route tests
- `bun run lint`
- `bun run build`

## Rollout

1. Add backend attachment types, validation, persistence, and tests.
2. Add provider serialization and model capability checks.
3. Extend API client types and streaming request shape.
4. Add desktop attach/preview/render UI.
5. Update agent runtime docs with the new image path.

