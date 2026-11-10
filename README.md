# CNV_SparkX26_Model

First start: open `/admin/` → **Updates** → **Download**; see [the web UI guide](docs/guide-web-ui.md#updating-the-model-files). Startup and requests load downloaded files only; an empty installation keeps health/admin available and returns 503 for inference.

Spark-X2.6-1.7B chat over HTTP through Ollama, port 29203. API replicas share one Ollama model; they do not load weights themselves. No dedicated tool service is required.

## API

| Call | Purpose |
|---|---|
| `POST /v1/chat` | `messages`, optional `temperature`, `max_tokens`, `stream` → content, finish_reason, model, fallback, usage, duration_ms. |
| `GET /health` | Public status, model, ready and num_thread. |

**Input.** One to 64 messages with system, user, assistant or tool role; each content is at most 100,000 characters. Temperature is 0–2; max_tokens is 1–4096 (default 512). Unknown fields, including `project`, `schema` and request-level `thinking`, return 422. Optional `tools` (up to 32 `{name, description, parameters}`) go to Ollama; the answer then has `tool_calls` (`id`, `name`, `arguments`) and `finish_reason` `tool_calls`. Send the assistant turn with its `tool_calls` and the result as a `tool` message (`tool_name` names the call) to continue. `fallback` remains false for response compatibility.

**Streaming.** `stream: true` returns NDJSON content lines then done with the full result. Before the first piece, upstream unavailability returns HTTP 503 with “start Ollama and pull <model>”. Later errors become error lines. With hide_reasoning enabled (default), content is held until completion and text before the last `</think>` is removed.

**Cache.** Only temperature 0 answers are cached, per replica; `X-Cache` reports hit or miss. Plain and streamed calls share cached answers. Thinking and reasoning-visibility settings select separate cached answers. A model pull advances cache generation across replicas through a marker in the data volume.

**Tokens.** Set SERVICE_ADMIN_PASSWORD and create a caller token on the admin page. Send X-Internal-Token. Token project restrictions are removed: projects accepts null or omission only (lists return 422). Old scope metadata is hidden in token listings. Rate limits and expiry still apply.

## Admin page

`/admin/` provides tokens, usage, metrics, replicas, Settings and Updates. CPU threads are sent to Ollama as `num_thread` with every request; unset lets Ollama decide. Set it to the physical core count. The Updates card compares the downloaded Ollama manifest digest with the online registry manifest at startup, every `update_check_hours` and on **Check now**. Checks fetch metadata only; only **Download** runs `ollama pull`. A successful download invalidates every replica through shared storage; each replica reloads on its next request.

## Run

Start Ollama and pull the configured model first:

```bash
curl -X POST http://127.0.0.1:29203/admin/updates/weights -H "X-Internal-Token: <ADMIN_PASSWORD>"
```

```bash
docker compose --env-file .env.compose up -d --build
```

Compose defaults SERVICE_OLLAMA_URL to http://host.docker.internal:11434. A local run defaults to http://127.0.0.1:11434 when unset. An explicitly empty URL fails configuration. The Ollama model is the restart setting `ollama_model` (else `SERVICE_OLLAMA_MODEL`); the admin **Model variant** card chooses a quantization this machine can run, with estimated memory use. Without Docker, use `run.bat run` or `uv run python src/app.py`. See [deployment](deployment.md).

## Tests

```bash
uv run pytest
```

Tests inject fake loaders and a local fake Ollama; they need no real model.

## Limits worth knowing

- One generation per replica; max_inflight defaults to four waiting/running calls. Excess calls receive 429 with Retry-After.
- Ollama owns model RAM, hardware choice and parallelism. API replica count does not multiply model memory.
- Warm-up runs in the background. Missing or unreachable Ollama leaves health and admin available, ready false, and chat returns 503. Next chat retries.
- Real Ollama generation and Docker image builds have not been verified for this migration.

## Try it from the admin page

Open `/admin/`, sign in and use Try it (a temporary token is filled in for you). Chat sends messages, temperature, max_tokens and streaming to `/v1/chat`. The panel shows status, timing, cancellation and a redacted curl command. See [web UI guide](docs/guide-web-ui.md#try-it-send-a-real-request).

## Documentation

- [Purpose: input, output and what it is for](docs/purpose.md)
- [Deployment](deployment.md)
- [Changelog](CHANGELOG.md)
- [Docker on another machine](docs/guide-docker-deployment-device.md)
- [Docker on this PC](docs/guide-docker-same-device.md)
- [Run without Docker](docs/guide-no-docker-same-device.md)
- [Test it from the command line](docs/guide-cli.md)

## Admin pages and navigation

Open `/admin/` and sign in. Overview opens first; each sidebar destination has its own URL fragment and supports browser Back/Forward and direct links:

- `#overview`: health, model readiness, replicas, active tokens and request counts.
- `#playground`: service-specific request forms and streamed or media responses.
- `#tokens`: create, edit, expire and revoke scoped access tokens.
- `#runtime`: CPU threads where supported, replica information and live/restart settings.
- `#updates`: check versions and explicitly download weights.
- `#connections`: caller addresses and public endpoints.
- `#models` and `#voices`: project models and cloned voices, shown only when supported.

Collapse/Expand switches between labeled sidebar and icon rail on desktop and mobile. The non-secret sidebar preference is saved per service in this browser. Navigation preserves form drafts and active Playground requests; sign-out still clears credentials and cancels work. Page names appear once in the main header. No preview data or cross-service selector ships in the real admin UI. Existing authentication, quotas, settings precedence and explicit-download policy remain in effect.
