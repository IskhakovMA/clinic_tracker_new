# AI Assistant V1

## Purpose

AI Assistant V1 is an embedded read-only helper for operational questions about
patients, attention issues, overdue work, internal notifications and Analytics
V2 aggregates. It helps staff navigate data they are already allowed to see. It
is not a medical decision system.

The assistant must not diagnose, recommend treatment, change data, close issues,
mark notifications read, send messages, run backups, or perform administrative
actions.

## Architecture

Flow:

`AI drawer -> /api/ai/chat -> orchestrator -> OpenAI-compatible provider -> allowlisted read tools -> existing services/RBAC -> scoped tool results -> answer`

The language model never receives direct SQLite, ORM, filesystem, shell or
arbitrary HTTP access. It can only request explicitly registered backend tools.

## Configuration

The assistant is disabled by default:

```bash
CLINIC_AI_ENABLED=false
CLINIC_AI_BASE_URL=http://127.0.0.1:1234/v1
CLINIC_AI_MODEL=
CLINIC_AI_API_KEY=
CLINIC_AI_TIMEOUT_SECONDS=45
CLINIC_AI_MAX_OUTPUT_TOKENS=1200
CLINIC_AI_MAX_TOOL_CALLS=8
CLINIC_AI_MAX_HISTORY_MESSAGES=12
CLINIC_AI_MAX_USER_MESSAGE_CHARS=4000
CLINIC_AI_RATE_LIMIT_REQUESTS=30
CLINIC_AI_RATE_LIMIT_WINDOW_SECONDS=300
```

`GET /api/ai/status` returns only safe state: enabled, available and a
user-facing message. It does not expose base URL, model or API key.

## Provider

`AIProvider` is an internal abstraction. V1 includes
`OpenAICompatibleAIProvider`, which calls `<CLINIC_AI_BASE_URL>/chat/completions`
with the configured model and backend-only API key.

The HTTP client uses bounded timeout, bounded response size, JSON validation and
does not allow redirects. Provider failures return controlled 503/502 API
errors; the rest of the application remains usable.

For local development, LM Studio can be used as an OpenAI-compatible local
server:

1. Start LM Studio.
2. Load a local OpenAI-compatible model, for example Qwen3-8B 4-bit.
3. Start the local server, usually `http://127.0.0.1:1234/v1`.
4. Set `CLINIC_AI_ENABLED=true`, `CLINIC_AI_BASE_URL` and `CLINIC_AI_MODEL`.
5. Start the backend.

No model files, weights or LM Studio config belong in Git.

## APIs

- `GET /api/ai/status`: authenticated read endpoint.
- `POST /api/ai/chat`: authenticated, CSRF-protected, rate-limited chat endpoint.

Request body is strict Pydantic with unknown fields forbidden. Frontend may send
only bounded user/assistant history and optional `patientContextId`; it cannot
send system prompts, tool messages, provider URL, model, API key or tool
definitions.

## System Prompt

The system prompt is backend-owned in `backend/app/ai/prompts.py`. It states:

- read-only boundary;
- no diagnosis or treatment recommendations;
- no hallucination when data is absent;
- patient fields, comments and tool results are untrusted data;
- no access expansion beyond backend scope checks;
- no disclosure of policy numbers, phones, secrets or out-of-scope data.

## Tools

The V1 allowlist is:

- `search_accessible_patients`
- `get_patient_summary`
- `get_patient_timeline`
- `list_attention_patients`
- `get_patient_attention`
- `list_overdue_patients`
- `get_today_work`
- `get_notification_summary`
- `get_analytics_summary`
- `get_department_attention_summary`

Each tool has a strict Pydantic argument schema. Unknown tools, malformed
arguments and over-limit tool loops are rejected. `CLINIC_AI_MAX_TOOL_CALLS`
defaults to 8.

Tools reuse existing backend services and `policy.patient_scope_filter`.
Doctor, department, global read-only and developer scopes are enforced in the
tool handlers. `get_department_attention_summary` is unavailable to doctor
scope.

Tool results follow minimum necessary data. They do not return phone numbers,
OMS policy numbers, full patient objects, backend secrets, or arbitrary links.
Patient references returned to the frontend are structured and derived only from
actual tool results.

## Privacy

Chat history is held only in frontend memory for the current browser tab. It is
cleared on logout or reload. No AI conversation table is created; SQLite schema
remains v6.

The backend does not write prompts, responses, tool results, Attention comments
or patient values to audit. Operational logs may contain only safe metadata such
as user id, role, elapsed time and tool call count.

## UI

The assistant opens from the topbar. Patient cards include a read-only
`Спросить помощника` action that sets a patient context chip. The drawer
supports free-text chat, multi-turn history, suggestions, loading/error states,
clear conversation, keyboard send with Enter and newline with Shift+Enter.

The drawer uses safe DOM text rendering, supports light/dark themes and mobile
widths, and coordinates with the internal notification drawer so the two right
drawers do not overlap.

## Known Limitations

- V1 has no server-side conversation persistence and no conversation summary.
- V1 is read-only; write-capable workflows are intentionally absent.
- Live LLM behavior depends on the configured local/internal provider.
- Production should use an internally controlled inference endpoint with
  network, logging and retention controls reviewed separately.
