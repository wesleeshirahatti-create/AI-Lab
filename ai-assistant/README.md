# AI Assistant

A lightweight command-line chat assistant that talks to a locally installed Qwen
model through Ollama. It uses only the Python standard library and keeps a
bounded conversation history.

## Structure

| Path | Purpose |
| --- | --- |
| `README.md` | Project overview and usage instructions. |
| `main.py` | Command-line entry point and interactive chat loop. |
| `requirements.txt` | Dependency list; no third-party packages are required. |
| `config.py` | Environment-backed application and Ollama settings. |
| `assistant/__init__.py` | Exposes the assistant package API. |
| `assistant/core.py` | Ollama chat client, conversation history, and error handling. |

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

Example:

```powershell
$env:ASSISTANT_NAME = "Lab Assistant"
$env:OLLAMA_MODEL = "qwen2.5:1.5b"
python main.py
```

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

Failed turns are removed from history so a retry does not duplicate the user
message.

## Low-Memory Design

- Python standard library only; no model is loaded into the Python process.
- Conversation history uses a bounded `deque`.
- Requests are non-streaming and use a small context window by default.
- Ollama manages model loading and unloading.

## Limitations

- Text chat only. The assistant does not use external tools, file access, web
  browsing, or function calling yet.
- Requires a local Ollama service and an installed Qwen model.
