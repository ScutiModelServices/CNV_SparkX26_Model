# CNV_SparkX26_Model: Docker Desktop on this PC

Run API replicas behind nginx on port 29203, calling Ollama on the host.

## Requirements

- Docker Desktop with Compose v2 and external citadel_core network.
- Running Ollama reachable from Docker, listening on a host interface Docker can access.
- Pull `hf.co/XHToken/Spark-X2.6-1.7B-GGUF:Q4_K_M` into Ollama. Model memory belongs to Ollama, not each API replica; deployment memory has not been measured.

## Settings

Copy `.env.example` to `.env` for Compose. Saved admin settings override their environment defaults.

| Variable | Default | Effect |
|---|---|---|
| `SERVICE_ADMIN_PASSWORD` | empty | Unlock admin and require caller tokens. |
| `SERVICE_PORT` | 29203 | Published nginx port. |
| `SERVICE_REPLICAS` | 1 | API copies sharing Ollama. |
| `SERVICE_MEM_LIMIT` | 0 | API container memory cap; excludes Ollama. |
| `SERVICE_OLLAMA_URL` | http://host.docker.internal:11434 | Required Ollama address; explicitly empty is invalid. |
| `SERVICE_OLLAMA_MODEL` | hf.co/XHToken/Spark-X2.6-1.7B-GGUF:Q4_K_M | Model selected at API startup. |
| `SERVICE_NUM_THREAD` | unset | Saved/reported card value only; Ollama chooses threads. |

Settings card: update_check_hours (6), max_inflight (0 = 4), cache_size (256, 0 = off), thinking (off/on), hide_reasoning (true), warm (true, restart). Their environment defaults are SERVICE_UPDATE_CHECK_HOURS, SERVICE_MAX_INFLIGHT, SERVICE_CACHE_SIZE, SERVICE_THINKING, SERVICE_HIDE_REASONING and SERVICE_WARM. See [admin guide](guide-web-ui.md#settings).

## Start

```bash
ollama pull hf.co/XHToken/Spark-X2.6-1.7B-GGUF:Q4_K_M
```

```bash
docker compose up -d --build
```

Only cnv_sparkx26_model_data is mounted for API persistence. Ollama stores its own weights. Existing weight storage migration is described in [CHANGELOG](../CHANGELOG.md). Docker builds were not run for this migration.

## Check

```bash
curl http://127.0.0.1:29203/health
```

Ready becomes true after successful warm-up or generation. Open http://127.0.0.1:29203/admin/ and create a caller token. Other containers on citadel_core use http://cnv_sparkx26_model_lb:29203.

## Problems

| Symptom | Fix |
|---|---|
| Ready false or chat 503 | Start Ollama, pull the configured model, and verify its address from the API container. Health and admin still answer. |
| 401 | Send a valid caller token; admin routes need the admin password. |
| 422 | Remove unknown fields or correct validation errors. |
| 429 | Wait Retry-After; token rate or in-flight limit reached. |
| Port 29203 busy | Stop the other listener or configure SERVICE_PORT. |
| nginx 502 after scaling | Restart cnv_sparkx26_model_lb. |
