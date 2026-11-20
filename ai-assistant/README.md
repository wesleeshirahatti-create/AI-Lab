# AI Assistant

A lightweight command-line chat assistant that talks to a locally installed Qwen
model through Ollama. It uses only the Python standard library and keeps a
bounded conversation history. Version 0.3 adds opt-in workspace tools with
native Ollama tool calling, including a controlled write tool that requires
explicit human approval.

## Structure

| Path | Purpose |
| --- | --- |
| `README.md` | Project overview and usage instructions. |
| `main.py` | Command-line entry point, chat loop, and approval prompts. |
| `requirements.txt` | Dependency list; no third-party packages are required. |
| `config.py` | Environment-backed application and Ollama settings. |
| `assistant/__init__.py` | Exposes the assistant package API. |
| `assistant/core.py` | Ollama chat client, tool-call loop, history, and errors. |
| `assistant/tools/base.py` | Tool specifications, results, and error types. |
| `assistant/tools/files.py` | `list_files`, `read_file`, and controlled `write_file`. |
| `assistant/tools/registry.py` | Explicit tool registration and defaults. |
| `assistant/tools/policy.py` | Enablement, paths, limits, and approval policy. |
| `assistant/tools/gateway.py` | Validation, permission checks, execution, and audit. |
| `assistant/tools/audit.py` | JSONL audit logging for tool calls. |
| `tests/` | Standard-library unit tests. |

## Requirements

- Python 3.10 or newer
- Ollama running locally at `http://localhost:11434`
- The `qwen2.5:1.5b` model installed in Ollama

Install the model once:

```powershell
ollama pull qwen2.5:1.5b
```

No third-party Python packages are required.

## Run

```powershell
cd ai-assistant
ollama serve
python main.py
```

If Ollama is already running, skip `ollama serve`. If the `ollama` command is
not on your PATH, use the Ollama desktop app or add the Ollama install directory
to PATH. Type a message to chat, `help` for a short prompt, or `exit`/`quit` to
stop.

## Configuration

Settings are read from optional environment variables:

- `ASSISTANT_NAME` - display name (default: `AI Assistant`)
- `MAX_HISTORY_MESSAGES` - bounded history size (default: `20`, minimum: `2`)
- `OLLAMA_BASE_URL` - Ollama server URL (default: `http://localhost:11434`)
- `OLLAMA_MODEL` - model name (default: `qwen2.5:1.5b`)
- `OLLAMA_TIMEOUT_SECONDS` - request timeout in seconds (default: `120`)
- `OLLAMA_NUM_CTX` - model context window (default: `2048`, range: `512`-`32768`)

Tool settings are opt-in:

- `TOOLS_ENABLED` - enable tools (default: `false`)
- `TOOL_READ_ROOT` - read root, must stay inside `ai-assistant` (default: `ai-assistant`)
- `TOOL_WRITE_ROOT` - write root, must stay inside `ai-assistant` (default: `ai-assistant/workspace`)
- `TOOL_ALLOW_WRITES` - enable the mutating `write_file` tool (default: `false`)
- `TOOL_AUTO_APPROVE_READS` - auto-approve read-only tools (default: `true`)
- `TOOL_REQUIRE_CONFIRMATION` - require confirmation for non-mutating tools (default: `true`)
- `TOOL_MAX_FILE_BYTES` - largest file that may be read; write content is capped at `65536` (default: `65536`)
- `TOOL_MAX_OUTPUT_BYTES` - largest tool result sent to the model (default: `8192`)
- `TOOL_MAX_LIST_ENTRIES` - largest directory listing (default: `200`)
- `TOOL_MAX_ROUNDS` - tool-call rounds per request, capped at `2` (default: `2`)
- `TOOL_LOG_PATH` - audit log path inside the project (default: `logs/tool_calls.jsonl`)
- `TOOL_LOG_CONTENT` - include short previews for non-file arguments in the audit log (default: `false`); file content is never previewed

Example:

```powershell
$env:ASSISTANT_NAME = "Lab Assistant"
$env:OLLAMA_MODEL = "qwen2.5:1.5b"
$env:TOOLS_ENABLED = "true"
$env:TOOL_ALLOW_WRITES = "1"
python main.py
```

## Tools

Tools are disabled unless `TOOLS_ENABLED=true`. Three tools are registered:

- `list_files(path=".")` - lists immediate entries in a workspace directory.
- `read_file(path, max_chars=8000)` - reads a UTF-8 text file, truncated to the
  requested character limit and the configured output byte limit.
- `write_file(path, content, overwrite=false)` - writes UTF-8 text to a file
  inside `TOOL_WRITE_ROOT`. Writes are disabled unless `TOOL_ALLOW_WRITES=1`.
  Every write asks the local user for approval. Existing files are only replaced
  when `overwrite=true` and the user approves the overwrite. Content is limited
  to 64 KB and is written atomically.

Tool calls use Ollama's native `tools` request field and `tool_calls` response
messages. The assistant executes at most two tool-call rounds per user request,
then asks the model for a final answer. Tool results are compacted and bounded
before being added back to the model context; tool messages are not stored in the
long-term conversation history.

## Security

- Tools remain disabled by default and require explicit opt-in.
- Writes require both `TOOL_ALLOW_WRITES=1` and per-call human approval.
- The model cannot approve its own tool calls; approval comes only from the
  local CLI prompt or a configured external callback.
- No Python execution or shell tools are registered.
- Reads and writes are restricted to their configured roots inside
  `ai-assistant`.
- Absolute paths, `..` traversal, home paths, symlink escapes, Windows reserved
  names, alternate data streams, and unsafe path characters are rejected.
- Existing files are never overwritten unless `overwrite=true` and the overwrite
  is approved.
- Writes use a temporary file followed by an atomic create or replace, so a
  failed write does not leave a partial target file.
- Binary files, invalid UTF-8, and oversized reads are rejected. Write content
  is limited to 64 KB of UTF-8 text.
- Each tool call is validated and recorded in the audit log. Audit records hash
  content and never log file contents or previews of mutating-tool content.

## How It Works

Each turn sends the system prompt and the bounded conversation history to
Ollama's `/api/chat` endpoint using `urllib`. Responses are non-streaming, and
the history is kept in a `deque` so memory use stays bounded.

The assistant handles:

- Ollama being offline or unreachable
- A missing model
- Request timeouts
- Invalid or empty API responses
- Empty user messages
- Empty assistant content when native tool calls are present
- Invalid tool arguments and denied permissions

Failed turns are removed from history so a retry does not duplicate the user
message.

## Low-Memory Design

- Python standard library only; no model is loaded into the Python process.
- Conversation history uses a bounded `deque`.
- Tool results are truncated per result and by a total context budget.
- Requests are non-streaming and use a small context window by default.
- Ollama manages model loading and unloading.

## Limitations

- Workspace inspection and controlled text-file writing only. The assistant
  cannot run Python, execute shell commands, browse the web, or use arbitrary
  function calling.
- `write_file` only creates UTF-8 text files up to 64 KB and requires a local
  interactive approval. Directories are created only inside the write root.
- Atomic create without overwrite relies on hard-link support in the workspace
  filesystem. If the filesystem does not support hard links, the write fails
  closed.
- Path checks and writes are separate filesystem operations. A local attacker
  who can modify the workspace concurrently could attempt a time-of-check /
  time-of-use race; the assistant itself cannot create symlinks or run code.
- Requires a local Ollama service and an installed Qwen model.
