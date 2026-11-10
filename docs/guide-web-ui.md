# CNV_SparkX26_Model: the admin web UI

The admin UI at `/admin/` has separate sidebar pages for health, access and model configuration. It works with every deployment mode. Only the address differs:

| Run | Address |
|---|---|
| Docker or `run.bat` on this PC | `http://127.0.0.1:29203/admin/` |
| Docker on the deployment box | `http://<box-ip>:29203/admin/` |

If `SERVICE_PORT` is changed, use that port.

## Before you open it

The page needs the admin password: the value of `SERVICE_ADMIN_PASSWORD`.
- Docker Desktop on this device: set it in `.env.compose` next to `docker-compose.yml`, then `docker compose --env-file .env.compose up -d`.
- Deployment device: set it in `.env` next to `docker-compose.yml` (Deploy option 2 copies it), then `docker compose up -d`.
- No Docker: set it in `.env.local` (or `.env`) in this folder and start with `run.bat` (it passes `--dotenv`), or set it in the environment first; a real environment variable wins over both files and `.env.local` wins over `.env`. A plain `python src/app.py` reads neither.
- Empty password = no auth at all and the page answers `Admin password required (set SERVICE_ADMIN_PASSWORD)`. Set
  one to use the page.

## Sign in and service status

Type the admin password and press **Sign in**. A wrong password shows an inline `Wrong admin password` error.
The password stays in `sessionStorage` for this browser tab, so reloading restores the session. **Sign out** clears
the password and any freshly created token. If browser storage is blocked, sign in again after reloading.

The header shows the service address and a health badge with an icon and text: **Healthy**, **Starting**, or
**Unreachable**. Health is checked without authentication every 10 seconds while signed in. **Starting** means the model
is not loaded yet (Ollama must already have the model pulled). Tiles show model readiness, replicas, Ollama parallel requests, CPU threads, and active tokens.
Use **Theme** to cycle between system, light, and dark appearance; this preference is saved in the browser.

## Tokens

Each calling project gets its own token, so one can be revoked without touching the others.
- **Create:** type a project name (up to 64 characters) and press **Create token**. The token appears once in
  an accent-tinted box with a **Copy** button and the reminder “Shown once. Copy it before you leave.”
  The box is cleared on the next create attempt or sign-out. Clipboard access has a fallback for plain HTTP;
  if both methods are blocked, select the token and copy it manually.
- **Use:** the caller sends it in the `X-Internal-Token` header on every call except `/health`.
- **List:** the table shows **Project**, **Created**, **Last used**, and **Requests** (authenticated calls so far). Last used is **never** until a token
  authenticates a request, then a relative time; hover a timestamp for its exact local time. The list refreshes
  every 10 seconds. Token values are not shown again.
- **Revoke:** press the red-outlined **Revoke** pill and confirm in the in-app dialog, which names the project.
  **Cancel**, Escape, or a click on the backdrop closes the dialog. The caller gets `401 Invalid token` from then on. Lost
  token = revoke it and create a new one.

Last-used timestamps are Unix seconds or null. They are persisted at most once per minute per token, so they
are approximate. Request counts are added to the file with that same write; uses between writes are
counted in memory, so a crash can lose up to a minute of them. Old token files without the field still load and show **never** until next use. A companion
`tokens.json.lock` file serializes updates across replicas without adding dependencies.

Tokens live in `tokens.json` (`SERVICE_TOKENS_FILE`). In Docker that is the `cnv_sparkx26_model_data` volume at `/data`, so they
survive `docker compose down` and rebuilds. Without Docker it is `tokens.json` in the folder the service starts from.
Tokens are enforced only while `SERVICE_ADMIN_PASSWORD` is set.

## CPU threads

The shared card saves 1-256 and sends it to Ollama as `num_thread` with every request; unset lets Ollama decide. Other replicas apply the saved value within a few seconds. Ollama reloads the model once when the value changes, so that first answer is slower. Set it to the physical core count.

## Replicas and parallel requests (read-only)

The tiles show replicas and parallel generations from `SERVICE_REPLICAS` and `SERVICE_OLLAMA_PARALLEL`: each replica runs that many generations at once (default 2; set it to the `OLLAMA_NUM_PARALLEL` of your Ollama).
The muted line says **Replicas: N. Change SERVICE_REPLICAS in .env.compose (or .env on the deployment device), then rebuild.** The
page cannot change them. To change, set them in `.env.compose` (or `.env` on the deployment device), then:
```bash
docker compose --env-file .env.compose up -d --build
docker compose restart cnv_sparkx26_model_lb
```
`unknown` means the variable is not set, which is normal without Docker. Without Docker there is one process.

## Problems

| Symptom | Fix |
|---|---|
| Page does not load | Service not running, wrong port, or blocked by a firewall. Test `/health`. |
| `Wrong admin password` | Password differs from `SERVICE_ADMIN_PASSWORD` in the running container. Recreate it after editing `.env.compose` or `.env`. |
| `Admin password required (set SERVICE_ADMIN_PASSWORD)` | The password is empty. Set it and restart. |
| Token lost | Revoke it and create a new one. |
| `/health` shows `ready: false` | The model is still loading or downloading, or loading failed (see the container logs). |
| Never expose port 29203 to the internet | It is a LAN tool; use a firewall or VPN for remote access. |

## Token project restrictions

Leave project lists empty in the shared token editor. Restrictions are removed: projects accepts null or omission only; lists return 422. Old restrictions are hidden in token listings. The chat request accepts no `project` field (422).

## Rate limit, expiry, edits, usage and metrics

- **Requests per minute** (optional, when creating or editing a token): over the limit a call gets `429` with `Retry-After`. The count is per replica, so with several replicas behind the balancer the real limit can be up to that many times higher.
- **Expires in (days)** (optional, only when creating): after that the token is refused like a revoked one, and the row shows "Expired". Create a new token to continue.
- **Edit** changes the rate limit; leave the project list empty without revoking the token.
- **Usage:** each row shows errors (answers with status 500 or above) and the average time per call. The counters are written at most once a minute.
- **Metrics:** `GET /metrics` (send the admin password in `X-Internal-Token`) returns the same numbers as Prometheus text, one series per token. It reads the shared token file, so any replica answers with the same data. `spark_request_seconds` is a histogram of how long each `/v1` call took on the replica that answers (to the first byte for a streamed reply); it starts from 0 when the replica restarts.

## Model variant

The **Model variant** card lists the Spark-X2.6 quantizations (`Q4_K_M` and `Q8_0`). Each row shows the estimated memory it takes (the file size plus a margin; not measured), the download size, whether Ollama already has it, and whether it would run on the GPU or the CPU (slower). The top line shows what this machine has. A quantization that fits neither the free GPU memory nor the free RAM is greyed with the reason and cannot be chosen. When Ollama runs on another machine its memory cannot be seen, so nothing is blocked and the card says so. **Use** saves the Ollama model name; restart the service to apply it, then press Download on the Updates card if Ollama does not have it yet. The model that is already loaded counts as fitting.

## Settings

The **Settings** card changes options without editing `.env.compose`, `.env` or restarting: update-check interval, caches and limits, and similar. Each row shows where its value comes from (saved, environment or default). A value saved here beats the environment variable; **Reset** removes it. Every replica reads the shared settings file, so a change applies within a few seconds. Rows marked "applies after a restart" are read once at start. The admin password, ports, replica count and other Docker settings stay in `.env` or the compose file, because the service cannot change its own container.

## Updating the model files

The service loads only downloaded weights and tokenizer files. Startup, warm-up and requests never download. With no downloaded model, `/health` and `/admin/` answer, `ready` is false and inference returns 503 with an admin Download hint. First start: open `/admin/` → **Updates** → **Download**. The Updates card compares the downloaded Ollama manifest digest with the online registry manifest at startup, every `update_check_hours` and on **Check now**. Checks fetch metadata only; only **Download** runs `ollama pull`. A successful download invalidates every replica through shared storage; each replica reloads on its next request.

Admin curl alternative:

```bash
curl -X POST http://127.0.0.1:29203/admin/updates/weights -H "X-Internal-Token: <ADMIN_PASSWORD>"
```

## Connect to this service

The **Connect to this service** card lists the addresses other services can use: the address you opened the page with, the same PC (`127.0.0.1`), another Docker container on the same network (the `_lb` name from the compose file), and a Docker container calling this PC (`host.docker.internal`), then every endpoint with a **Copy** button. Callers send a token from the tokens card in `X-Internal-Token`.

## Try it: send a real request

1. Sign in to `/admin/` and find **Try it**.
2. The token field is hidden. Signing in to the admin page makes a temporary public token named `playground (temporary)` (valid 1 day) and fills it in, and choices such as voices load by themselves. It is the public client token, not the admin password.
3. Choose an operation, complete its labeled fields and send it. Requests go to this service's public route, with the same token scope, rate/admission limits and cache as a separate client.
4. Read the HTTP status, elapsed time and response. NDJSON/SSE text appears while it arrives; JSON is formatted. HTTP errors, including 401, 422, 429 and 503, keep their response body visible. Use **Cancel** to abort an active request.
5. Copy the curl equivalent to repeat the request elsewhere. The token is always a placeholder; fill it locally. For an upload, provide the indicated input file. Multi-step operations show their follow-up request too.

Authentication is **a temporary public token made from the admin session and held only in memory**. The playground does not persist it in browser storage, put it in a URL, log it or include it in curl. Sign out or close the tab revokes it; opening the admin page in a second tab replaces it. It appears in the tokens list and counts as an active token while it lives. The existing admin sign-in is separate. A playground run consumes normal service quotas and can generate retained files where the public API normally does so; cancelling the browser fetch does not promise to undo work already accepted by a backend.

The browser caps uploads at **10 MiB**, displayed text at **256 KiB**, and binary audio/image responses at **32 MiB**. Server limits still apply and may be lower. Media uses bounded Blob URLs, revoked when replaced or cleared. Text is rendered as text, never executed as HTML. Audio players become available after the bounded audio download finishes. No new environment setting is needed.

### This service's request

Chat sends messages to `POST /v1/chat`, with optional temperature, max_tokens and streaming. Spark rejects schema and thinking with 422; its form does not send those MiniCPM-only fields or tools (call the API for tools).

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
