# Chat Image Vision Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add first-class image attachments to chat turns, persist them with runtime message history, and send them to vision-capable providers.

**Architecture:** Keep the runtime provider-neutral by storing image metadata in `RuntimeMessage.content_json.attachments` and representing attachments on `StoredMessage`. Validate and save binary image files before turn persistence, then provider clients read files from disk and serialize provider-native image blocks. The desktop sends base64 image payloads through the existing chat request and renders returned authenticated attachment URLs.

**Tech Stack:** FastAPI, Pydantic v2, SQLAlchemy runtime models, Python provider clients, Bun TypeScript API client, React/Vite desktop UI.

---

### Task 1: Backend Schemas And Attachment Validation

**Files:**
- Modify: `apps/server/src/anima_server/config.py`
- Modify: `apps/server/src/anima_server/schemas/chat.py`
- Create: `apps/server/src/anima_server/services/agent/attachments.py`
- Test: `apps/server/tests/test_chat_attachments.py`

- [ ] **Step 1: Write failing schema and validation tests**
- [ ] **Step 2: Run tests to verify they fail**
- [ ] **Step 3: Implement minimal schema/settings/validation**
- [ ] **Step 4: Run tests to verify they pass**

### Task 2: Runtime Persistence And History Serialization

**Files:**
- Modify: `apps/server/src/anima_server/services/agent/state.py`
- Modify: `apps/server/src/anima_server/services/agent/persistence.py`
- Modify: `apps/server/src/anima_server/services/agent/service.py`
- Modify: `apps/server/src/anima_server/services/agent/thread_manager.py`
- Modify: `apps/server/src/anima_server/services/agent/transcript_archive.py`
- Modify: `apps/server/src/anima_server/services/agent/compaction.py`
- Test: `apps/server/tests/test_agent_persistence.py`
- Test: `apps/server/tests/test_chat_attachments.py`

- [ ] **Step 1: Write failing persistence/history tests**
- [ ] **Step 2: Run focused tests to verify they fail**
- [ ] **Step 3: Implement runtime metadata flow**
- [ ] **Step 4: Run focused tests to verify they pass**

### Task 3: Model Capability And Provider Serialization

**Files:**
- Create: `apps/server/src/anima_server/services/agent/model_capabilities.py`
- Modify: `apps/server/src/anima_server/services/agent/messages.py`
- Modify: `apps/server/src/anima_server/services/agent/openai_compatible_client.py`
- Modify: `apps/server/src/anima_server/services/agent/anthropic_client.py`
- Modify: `apps/server/src/anima_server/services/agent/service.py`
- Test: `apps/server/tests/test_agent_openai_compatible_client.py`
- Test: `apps/server/tests/test_agent_anthropic_client.py`
- Test: `apps/server/tests/test_chat_attachments.py`

- [ ] **Step 1: Write failing provider tests**
- [ ] **Step 2: Run tests to verify they fail**
- [ ] **Step 3: Implement capability checks and serialization**
- [ ] **Step 4: Run tests to verify they pass**

### Task 4: Chat Routes And Attachment File Endpoint

**Files:**
- Modify: `apps/server/src/anima_server/api/routes/chat.py`
- Test: `apps/server/tests/test_chat.py`
- Test: `apps/server/tests/test_chat_attachments.py`

- [ ] **Step 1: Write failing route tests**
- [ ] **Step 2: Run tests to verify they fail**
- [ ] **Step 3: Implement route integration**
- [ ] **Step 4: Run tests to verify they pass**

### Task 5: API Client And Desktop UI

**Files:**
- Modify: `packages/api-client/src/types.ts`
- Modify: `packages/api-client/src/client.ts`
- Modify: `packages/standard-templates/src/composed/PromptInput.tsx`
- Modify: `apps/desktop/src/components/chat/ChatLayout.tsx`
- Modify: `apps/desktop/src/pages/chat/Chat.tsx`

- [ ] **Step 1: Add client/UI types and request shape**
- [ ] **Step 2: Add desktop picker, preview, submit, and rendering**
- [ ] **Step 3: Run desktop typecheck/build**

### Task 6: Documentation And Final Verification

**Files:**
- Modify: `docs/architecture/agent/agent-runtime.md`

- [ ] **Step 1: Document image attachment path**
- [ ] **Step 2: Run focused backend verification**
- [ ] **Step 3: Run full repo verification**
