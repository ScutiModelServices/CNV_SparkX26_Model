# CNV_SparkX26_Model: without Docker, on Windows

First start: open `/admin/` → **Updates** → **Download**; see [the web UI guide](guide-web-ui.md#updating-the-model-files). Startup and requests load downloaded files only; an empty installation keeps health/admin available and returns 503 for inference.

API runs on 127.0.0.1:29203 and uses a separate running Ollama.

## Requirements

- Python 3.11 or newer and uv.
- Ollama with `hf.co/XHToken/Spark-X2.6-1.7B-GGUF:Q4_K_M` pulled. API process holds no model weights.

## Start

```bash
curl -X POST http://127.0.0.1:29203/admin/updates/weights -H "X-Internal-Token: <ADMIN_PASSWORD>"
```

Run `run.bat run`, or choose Run in its menu. It syncs .venv and starts the API with --dotenv. A plain invocation does not load .env:

```bash
uv run python src/app.py
```

Unset SERVICE_OLLAMA_URL defaults to http://127.0.0.1:11434; empty is invalid. When copying .env.example for a local run, change its Docker address to this local address. SERVICE_OLLAMA_MODEL is read at startup. SERVICE_HOST defaults to 127.0.0.1; SERVICE_PORT defaults to 29203. Set SERVICE_ADMIN_PASSWORD to unlock admin and require caller tokens. A real environment value beats .env; saved admin settings beat their environment defaults. See [settings](guide-docker-same-device.md#settings).

## Check

```bash
curl http://127.0.0.1:29203/health
```

Admin remains available while Ollama is down or its model is missing. Ready stays false and chat gives 503 with a admin Updates Download hint. Open http://127.0.0.1:29203/admin/ and create a caller token.

## Stop

Ctrl+C stops the API. Ollama is managed separately.

## Tests

```bash
uv run pytest
```

## Problems

See [problems](guide-docker-same-device.md#problems). A real Ollama run has not been verified for this migration.
