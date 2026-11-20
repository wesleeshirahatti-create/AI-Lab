# AI Assistant

A lightweight command-line chat assistant that talks to a locally installed Qwen
model through Ollama. It uses only the Python standard library and keeps a
bounded conversation history. Version 0.3 adds opt-in workspace tools with
native Ollama tool calling, including controlled file writes and opt-in Python
execution that require explicit human approval.

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
- `TOOL_ALLOW_PYTHON` - enable the `run_python` tool (default: `false`)
- `TOOL_AUTO_APPROVE_READS` - auto-approve read-only tools (default: `true`)
- `TOOL_REQUIRE_CONFIRMATION` - require confirmation for non-mutating tools (default: `true`)
- `TOOL_MAX_FILE_BYTES` - largest file that may be read; write content is capped at `65536` (default: `65536`)
- `TOOL_MAX_OUTPUT_BYTES` - largest tool result sent to the model (default: `8192`)
- `TOOL_MAX_LIST_ENTRIES` - largest directory listing (default: `200`)
- `TOOL_MAX_ROUNDS` - tool-call rounds per request, capped at `2` (default: `2`)
- `TOOL_PYTHON_TIMEOUT_SECONDS` - default wall-clock timeout, capped at `10` seconds (default: `10`)
- `TOOL_PYTHON_PROCESS_MEMORY_BYTES` - Windows per-process memory limit (default: `256 MiB`, range: `16 MiB`-`1 GiB`)
- `TOOL_PYTHON_JOB_MEMORY_BYTES` - Windows aggregate Job Object memory limit (default: `512 MiB`, range: `32 MiB`-`2 GiB`)
- `TOOL_PYTHON_CPU_TIME_SECONDS` - Windows per-process and per-job user-mode CPU limits (default: `10`, range: `1`-`60` seconds)
- `TOOL_PYTHON_MAX_PROCESSES` - Windows maximum active processes in the Job Object (default: `8`, range: `1`-`64`)
- `TOOL_LOG_PATH` - audit log path inside the project (default: `logs/tool_calls.jsonl`)
- `TOOL_LOG_CONTENT` - include short previews for non-file arguments in the audit log (default: `false`); file content is never previewed

Example:

```powershell
$env:ASSISTANT_NAME = "Lab Assistant"
$env:OLLAMA_MODEL = "qwen2.5:1.5b"
$env:TOOLS_ENABLED = "true"
$env:TOOL_ALLOW_WRITES = "1"
$env:TOOL_ALLOW_PYTHON = "1"
python main.py
```

## Tools

Tools are disabled unless `TOOLS_ENABLED=true`. Four tools are registered:

- `list_files(path=".")` - lists immediate entries in a workspace directory.
- `read_file(path, max_chars=8000)` - reads a UTF-8 text file, truncated to the
  requested character limit and the configured output byte limit.
- `write_file(path, content, overwrite=false)` - writes UTF-8 text to a file
  inside `TOOL_WRITE_ROOT`. Writes are disabled unless `TOOL_ALLOW_WRITES=1`.
  Every write asks the local user for approval. Existing files are only replaced
  when `overwrite=true` and the user approves the overwrite. Content is limited
  to 64 KB and is written atomically.
- `run_python(code, timeout_seconds=10)` - runs up to 8 KB of Python source in a
  subprocess, with output capped at 8 KB. It is disabled unless
  `TOOL_ALLOW_PYTHON=1`, and every call requires explicit user approval.

The CLI approval prompt displays the code, timeout, and working directory.
Without an approving callback, the gateway denies the call. Python runs with
the current user's permissions; the temporary working directory is not an
access-control boundary.

On Windows, `run_python` configures a Job Object before starting its process,
starts the process suspended, assigns it to the job, and resumes it only after
successful assignment. The default Job Object limits are 256 MiB per process,
512 MiB aggregate memory, 10 seconds of user-mode CPU time per process and per
job, and 8 active processes (including the Python process). The corresponding
`TOOL_PYTHON_*` variables above configure these limits within their stated
ranges. The Job Object also terminates job members when its handle closes.
These limits are Windows-specific; on other platforms the tool still enforces
its timeout and output limits, but does not apply equivalent memory or process
count limits.

The Windows resume step uses `NtResumeProcess` from `ntdll`. This is an
undocumented Windows native API, so its availability and behavior are not
covered by a stable Win32 compatibility contract. If it is unavailable or
fails, the tool does not send the submitted source and reports execution
failure.

Cleanup checks Windows termination and handle-close failures and reports them.
If Windows rejects both `TerminateJobObject` and `CloseHandle`, complete
cleanup cannot be guaranteed; remaining processes or a handle may persist
until the assistant process exits.

Tool calls use Ollama's native `tools` request field and `tool_calls` response
messages. The assistant executes at most two tool-call rounds per user request,
then asks the model for a final answer. Tool results are compacted and bounded
before being added back to the model context; tool messages are not stored in the
long-term conversation history.

## Security

- Tools remain disabled by default and require explicit opt-in.
- Writes require both `TOOL_ALLOW_WRITES=1` and per-call human approval.
- Python execution requires both `TOOLS_ENABLED=true` and
  `TOOL_ALLOW_PYTHON=1`, and every `run_python` call requires per-call human
  approval.
- The model cannot approve its own tool calls; approval comes only from the
  local CLI prompt or a configured external callback.
- No shell tool is registered.
- The file tools' reads and writes are restricted to their configured roots
  inside `ai-assistant`. `run_python` is not restricted to those roots.
- `run_python` is **NOT a security sandbox**. Approved code can access files,
  the network, and other processes with the current user's permissions. The
  Windows Job Object limits resource use; it does not restrict those
  permissions or provide isolation.
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

- Workspace inspection and controlled text-file writing are available, and
  approved Python execution can be enabled. The assistant has no shell tool or
  web-browsing tool.
- `write_file` only creates UTF-8 text files up to 64 KB and requires a local
  interactive approval. Directories are created only inside the write root.
- `run_python` is **NOT a security sandbox**. It runs with the current user's
  permissions and can access files, the network, and other processes. Windows
  Job Objects constrain selected resource usage but do not isolate the code.
- Windows cleanup can be incomplete if both Job Object termination and handle
  closure fail; `NtResumeProcess` is also an undocumented API whose
  compatibility is not guaranteed.
- Atomic create without overwrite relies on hard-link support in the workspace
  filesystem. If the filesystem does not support hard links, the write fails
  closed.
- Path checks and writes are separate filesystem operations. A local attacker
  who can modify the workspace concurrently could attempt a time-of-check /
  time-of-use race in the file tools.
- Requires a local Ollama service and an installed Qwen model.
