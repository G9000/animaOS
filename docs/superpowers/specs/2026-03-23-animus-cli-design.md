# Animus CLI Design Spec

**Date:** 2026-03-23
**Status:** Approved
**Package:** `apps/animus/`
**CLI Command:** `anima`

## Overview

Animus is the coding agent CLI for AnimaOS. It connects to the existing Python cognitive core (apps/server) via WebSocket, executing coding tools locally on the user's machine while the server handles the agent loop, LLM calls, memory, and consciousness.

The name follows Jungian terminology: **Anima** is the soul (cognitive core), **Animus** is the active, outward-facing principle (the hands that act on the world).

```
apps/
├── server/          <- Anima (soul — cognition, memory, LLM)
├── desktop/         <- Face (companion UI)
├── anima-mod/       <- Voice (Telegram, Discord channels)
└── animus/          <- Hands (CLI coding agent)
```

## Architecture: Server-Driven Loop with Client-Side Tool Execution

The server runs the full agent loop. When it encounters a client-side action tool, it delegates execution to the connected CLI via WebSocket. The server pauses the loop, waits for the CLI to execute and return the result, then resumes.

```
Server                              CLI (Animus)
  | agent loop running                |
  | LLM says: call bash("ls")        |
  |---- ws: tool_execute ----------->|
  |     (paused)                      | checks permissions
  |                                   | executes bash("ls")
  |<--- ws: tool_result -------------|
  | resumes loop                      |
  | LLM says: send_message(...)      |
  |---- ws: assistant_message ------>|
  |---- ws: turn_complete ---------->|
```

### Tool Classification

- **Cognitive tools** (server-side): send_message, recall_memory, recall_conversation, core_memory_append, core_memory_replace, save_to_memory, create_task, list_tasks, complete_task, set_intention, complete_goal, note_to_self, dismiss_note, update_human_memory, current_datetime
- **Action tools** (client-side): bash, read_file, write_file, edit_file, grep, glob, list_dir, ask_user

The server distinguishes between them: cognitive tools execute internally as today, action tools are delegated to the connected client via WebSocket.

## WebSocket Protocol

### Endpoint

`/ws/agent` — a new FastAPI WebSocket route. Both the CLI and desktop app connect here.

### Message Format

All messages are JSON with a `type` field for routing.

### Server -> Client Messages

| Type | Fields | Purpose |
|---|---|---|
| `auth_ok` | user, agentState | Authentication succeeded |
| `tool_execute` | tool_call_id, tool_name, args | "Run this tool locally" |
| `assistant_message` | content, partial | Streaming/complete assistant text |
| `reasoning` | content | Agent's inner thinking |
| `tool_call` | tool_call_id, tool_name, args | Informational: agent decided to call a tool |
| `tool_return` | tool_call_id, tool_name, result | Result of a server-side cognitive tool |
| `approval_required` | tool_call_id, tool_name, args, run_id | Server needs user approval for cognitive tool |
| `turn_complete` | response, model, provider, tools_used | Turn finished |
| `error` | message, code | Error during turn |
| `stream_token` | token | Individual token for streaming text |

### Client -> Server Messages

| Type | Fields | Purpose |
|---|---|---|
| `auth` | username, password OR token | Authenticate connection |
| `user_message` | message | User sends a chat message |
| `tool_result` | tool_call_id, status, result, stdout?, stderr? | Result of locally executed tool |
| `tool_schemas` | tools[] | Register available action tools on connect |
| `approval_response` | run_id, tool_call_id, approved, reason? | User approves/denies cognitive tool |
| `cancel` | run_id? | Cancel current turn |

## Server-Side Changes

### 1. WebSocket Endpoint (`api/routes/ws.py`)

New FastAPI WebSocket route at `/ws/agent`:
- Client connects, sends `auth` message with credentials
- Server validates, sends `auth_ok` with user context
- Client sends `tool_schemas` to register available action tools
- Server maintains a connection registry per user (multiple clients can connect)

### 2. Tool Delegation in Agent Runtime (`services/agent/runtime.py`)

Modify `AgentRuntime` to support a `tool_delegate` callback:

```python
# In runtime step execution
if tool_name in server_tools:
    result = await self._tool_executor.execute(tool_name, args)
elif tool_name in client_tools:
    result = await self._tool_delegate(tool_name, args, tool_call_id)
else:
    result = ToolExecutionResult(error="Unknown tool")
```

When delegating:
- Send `tool_execute` message over WebSocket
- Await response via `asyncio.Event` or `asyncio.Future`
- Timeout after configurable duration (default 300s for long-running commands)
- Return result to the loop as if it were a server-side execution

### 3. Action Tool Schema Registry (`services/agent/tools.py`)

Add `get_action_tools()` that returns tool schemas registered by the connected client. These merge into `get_tools()` so the LLM sees them, but execution is delegated.

### What Stays Unchanged

Memory blocks, system prompt assembly, heartbeat protocol, ToolRulesSolver, compaction, consolidation, inner monologue, emotional intelligence, self-model — all untouched.

## CLI Architecture

```
apps/animus/
├── package.json
├── tsconfig.json
├── src/
│   ├── index.ts              # Entry point, parse args, launch app
│   ├── client/
│   │   ├── connection.ts     # WebSocket manager (connect, reconnect, heartbeat)
│   │   ├── auth.ts           # Login flow, token storage (~/.animus/config.json)
│   │   └── protocol.ts       # Message types, serialize/deserialize
│   ├── tools/
│   │   ├── registry.ts       # Register tools, export schemas to server
│   │   ├── executor.ts       # Dispatch tool_execute -> tool impl -> tool_result
│   │   ├── permissions.ts    # CLI-side permission checks
│   │   ├── bash.ts           # Shell execution with streaming, abort, timeout
│   │   ├── read.ts           # Read file with line numbers, offset/limit
│   │   ├── write.ts          # Write/create file
│   │   ├── edit.ts           # Surgical edit (line range or search/replace)
│   │   ├── grep.ts           # Regex search (ripgrep wrapper)
│   │   ├── glob.ts           # File pattern matching
│   │   ├── list_dir.ts       # Directory listing
│   │   └── ask_user.ts       # Prompt user for input mid-turn
│   └── ui/
│       ├── App.tsx           # Root ink component
│       ├── Chat.tsx          # Message list (assistant, user, tool calls)
│       ├── Input.tsx         # User input with history
│       ├── ToolCall.tsx      # Tool call display (collapsible, syntax highlighted)
│       ├── Approval.tsx      # Permission prompt (allow/deny/always allow)
│       ├── Spinner.tsx       # Loading/thinking indicator
│       └── Header.tsx        # Status bar (model, connection, cwd)
```

### CLI Commands

```bash
anima                          # Interactive TUI mode
anima "fix the auth bug"       # Headless mode — send prompt, show result, exit
anima --server <url>           # Connect to remote server
```

### Tech Stack

- **Runtime:** Bun
- **TUI:** ink (React for terminal)
- **WebSocket:** ws
- **Search:** @vscode/ripgrep (optional dep for grep tool)

## Tool Implementation Details

### bash.ts
- Spawns child process via Bun.spawn or node-pty
- Streams stdout/stderr to TUI in real-time
- Abort via AbortSignal (user presses Esc)
- Timeout: default 120s, configurable
- Output truncation for large results (keep last N lines, note truncation)
- Working directory tracking: cd commands update effective cwd

### edit.ts
- Two modes: line range replacement and search/replace
- Diff preview before applying (in approval prompt if permission required)
- Atomic write (write to temp file, rename)

### permissions.ts — CLI-Side Safety
- Read-only tools (read, grep, glob, list_dir): always allow
- Write tools (write, edit): allow within cwd, ask outside cwd
- Bash: pattern matching — read-only commands (ls, cat, git status) auto-allow, destructive commands (rm, sudo, git push) ask for approval
- ask_user: always allow
- User can respond with "always allow" to create a session-scoped rule

### Split Permission Model
- **Server** enforces rules for cognitive tools via existing ToolRulesSolver (terminal rules, init rules, approval rules)
- **CLI** enforces rules for local action tools (bash safety, file write boundaries)
- Each side owns its domain — server knows about memory safety, CLI knows about filesystem safety

## Authentication

### Startup Flow

1. Check `~/.animus/config.json` for stored token + server URL
2. If no token: prompt username/password, POST /api/auth/login, store token
3. Connect WebSocket to ws://localhost:3031/ws/agent
4. Send `auth` message with token
5. Server validates, sends `auth_ok` with user info
6. CLI registers action tool schemas
7. Show TUI with greeting (from /api/chat/greeting)
8. Ready for input

### Token Storage

```json
// ~/.animus/config.json
{
  "serverUrl": "ws://localhost:3031",
  "token": "unlock_token_here",
  "username": "leo"
}
```

## Connection Lifecycle

### Reconnection
- Auto-reconnect with exponential backoff (1s, 2s, 4s, max 30s)
- "Reconnecting..." shown in header status bar
- If token expired: re-prompt login
- Queued user messages held and sent after reconnect

### Server Not Running
- Connection fails on startup: show error message with instructions
- No auto-launching server in v1

### Mid-Turn Errors
- Tool execution fails: send tool_result with status "error", server handles gracefully
- WebSocket drops mid-turn: turn is lost, user notified, can retry
- Server error: error message displayed in TUI

## Scope: What's NOT in v1

- No subagents/tasks
- No MCP server support
- No skill system
- No plan mode
- No auto-server-launch
- No file watching
- No image/PDF support
- No custom tool extensions
- No multi-conversation (one per session, /clear resets)
- No update checker
- No telemetry

## Future Considerations

- Desktop app can connect via same WebSocket protocol to gain coding capabilities
- Subagent support for parallel task execution
- Skill learning from coding sessions (like Letta Code)
- MCP server support for external tool providers
- Auto-launch server if not running
- Multi-conversation support with /new and /switch
