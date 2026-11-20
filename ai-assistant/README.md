# AI Assistant

A lightweight command-line chat assistant that talks to a locally installed Qwen
model through Ollama. It uses only the Python standard library and keeps a
bounded conversation history. Version 0.3 adds opt-in, read-only workspace
tools with native Ollama tool calling.

## Structure

| Path | Purpose |
| --- | --- |
| `README.md` | Project overview and usage instructions. |
| `main.py` | Command-line entry point and interactive chat loop. |
| `requirements.txt` | Dependency list; no third-party packages are required. |
| `config.py` | Environment-backed application and Ollama settings. |
| `assistant/__init__.py` | Exposes the assistant package API. |
| `assistant/core.py` | Ollama chat client, tool-call loop, history, and errors. |
| `assistant/tools/base.py` | Tool specifications, results, and error types. |
| `assistant/tools/files.py` | Read-only `list_files` and `read_file` tools. |
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

- `TOOLS_ENABLED` - enable read-only tools (default: `false`)
- `TOOL_READ_ROOT` - read root, must stay inside `ai-assistant` (default: `ai-assistant`)
- `TOOL_AUTO_APPROVE_READS` - auto-approve read-only tools (default: `true`)
- `TOOL_REQUIRE_CONFIRMATION` - require confirmation for non-auto tools (default: `true`)
- `TOOL_MAX_FILE_BYTES` - largest file that may be read (default: `65536`)
- `TOOL_MAX_OUTPUT_BYTES` - largest tool result sent to the model (default: `8192`)
- `TOOL_MAX_LIST_ENTRIES` - largest directory listing (default: `200`)
- `TOOL_MAX_ROUNDS` - tool-call rounds per request, capped at `2` (default: `2`)
- `TOOL_LOG_PATH` - audit log path inside the project (default: `logs/tool_calls.jsonl`)
- `TOOL_LOG_CONTENT` - include short argument previews in the audit log (default: `false`)

Example:

```powershell
$env:ASSISTANT_NAME = "Lab Assistant"
$env:OLLAMA_MODEL = "qwen2.5:1.5b"
$env:TOOLS_ENABLED = "true"
python main.py
```

## Tools

Tools are disabled unless `TOOLS_ENABLED=true`. Only two read-only tools are
registered:

- `list_files(path=".")` - lists immediate entries in a workspace directory.
- `read_file(path, max_chars=8000)` - reads a UTF-8 text file, truncated to the
  requested character limit and the configured output byte limit.

Tool calls use Ollama's native `tools` request field and `tool_calls` response
messages. The assistant executes at most two tool-call rounds per user request,
then asks the model for a final answer. Tool results are compacted and bounded
before being added back to the model context; tool messages are not stored in the
long-term conversation history.

## Security

- Tools remain disabled by default and require explicit opt-in.
- No write, Python execution, or shell tools are registered.
- Reads are restricted to the `ai-assistant` workspace.
- Absolute paths, `..` traversal, home paths, and symlink escapes are rejected.
- Binary files, invalid UTF-8, and oversized files are rejected.
- Permission decisions come from the local policy and an optional human
  callback. The model cannot approve its own tool calls.
- Each tool call is validated and recorded in the audit log.

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

- Read-only workspace inspection only. The assistant cannot write files, run
  Python, execute shell commands, browse the web, or use arbitrary function
  calling.
- Requires a local Ollama service and an installed Qwen model.
