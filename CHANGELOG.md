# Changelog

## Unreleased

- Breaking: startup, warm-up and requests load downloaded weights only. First run: open `/admin/` → Updates → Download; health/admin remain available and inference returns 503 until ready.
- Updates compares the downloaded Ollama manifest digest with the online manifest without pulling; only admin Download pulls and invalidates replicas.

- New `SERVICE_OLLAMA_PARALLEL` (default 2): generations one replica runs at once, like CNV_MiniCPM52B_Model. Before, each replica ran one at a time. Set it to the `OLLAMA_NUM_PARALLEL` of your Ollama; the in-flight cap defaults to 4 x this value.
- Breaking: generation is now Ollama-only. Removed the torch/transformers backend, accelerate, CPU index and all weight-loader dependencies; no Hugging Face checkpoint loading remains.
- Breaking: removed model, dtype, max_project_models and project_models_dir settings and the project request field (422). Project model folders are no longer used. Token project restrictions are removed: projects accepts only null or omission, lists return 422, and old restrictions are hidden in token listings.
- SERVICE_OLLAMA_URL defaults to http://127.0.0.1:11434 locally and http://host.docker.internal:11434 in Compose; an explicitly empty value is an error. SERVICE_OLLAMA_MODEL remains startup-only.
- Missing or unreachable Ollama leaves health and admin available, ready false; chat returns 503 with a start/pull hint. Updates shows installed digests without registry comparison; Download stays available and always runs Ollama pull. CPU threads card saves/reports only; Ollama chooses threads.
- Migration: start Ollama and pull hf.co/XHToken/Spark-X2.6-1.7B-GGUF:Q4_K_M. Remove obsolete settings from operator configuration. The old cnv_sparkx26_model_models volume with HF weights is unused and can be deleted after backup; Compose now mounts only the existing data volume. Keep old volume and project folders to roll back to the preceding commit; no stored data is deleted automatically.

- Docs: `docs/guide-cli.md`, a PowerShell-only way to test the service.

### Breaking: common service environment variables

Service-owned environment variables now use `SERVICE_*`. Old names are no longer read.
Rename old names once in your real `.env`, Compose `environment:` block, and CI/secret settings.

| Old name | New name |
|---|---|
| `SPARK_ADMIN_PASSWORD` | `SERVICE_ADMIN_PASSWORD` |
| `SPARK_CACHE_SIZE` | `SERVICE_CACHE_SIZE` |
| `SPARK_DTYPE` | `SERVICE_DTYPE` |
| `SPARK_HIDE_REASONING` | `SERVICE_HIDE_REASONING` |
| `SPARK_HOST` | `SERVICE_HOST` |
| `SPARK_MAX_INFLIGHT` | `SERVICE_MAX_INFLIGHT` |
| `SPARK_MAX_PROJECT_MODELS` | `SERVICE_MAX_PROJECT_MODELS` |
| `SPARK_MEM_LIMIT` | `SERVICE_MEM_LIMIT` |
| `SPARK_MODEL` | `SERVICE_MODEL` |
| `SPARK_NUM_THREAD` | `SERVICE_NUM_THREAD` |
| `SPARK_OLLAMA_MODEL` | `SERVICE_OLLAMA_MODEL` |
| `SPARK_OLLAMA_URL` | `SERVICE_OLLAMA_URL` |
| `SPARK_PORT` | `SERVICE_PORT` |
| `SPARK_PROJECT_MODELS_DIR` | `SERVICE_PROJECT_MODELS_DIR` |
| `SPARK_REPLICAS` | `SERVICE_REPLICAS` |
| `SPARK_THINKING` | `SERVICE_THINKING` |
| `SPARK_TOKENS_FILE` | `SERVICE_TOKENS_FILE` |
| `SPARK_UPDATE_CHECK_HOURS` | `SERVICE_UPDATE_CHECK_HOURS` |
| `SPARK_WARM` | `SERVICE_WARM` |
| `SPARK_WEB_DIR` | `SERVICE_WEB_DIR` |
| `SPARK_WEIGHTS_MARKER` | `SERVICE_WEIGHTS_MARKER` |

- Default HTTP port moves from 9180 to 29203. Update explicit old ports in your real `.env`, saved tool-service `backends.json` connections, and browser/MCP client configurations once. Compose now uses 29203 inside API replicas and nginx; deployments using the old Compose default must recreate containers. A custom host-port override stays in effect until you change it; container ports still move to 29203.

- Renamed `SparkX26Model` to `CNV_SparkX26_Model`; Docker project, API image, services, load balancer and volumes now use `cnv_sparkx26_model` names. Callers on `citadel_core` connect to `cnv_sparkx26_model_lb` at the existing port.
- Docker volume migration: existing volumes with old names are orphaned; renaming does not move data. Stop the old stack before copying; never use `down -v`. The commands below assume the previous default Compose project name; substitute actual volume names if `-p` or `COMPOSE_PROJECT_NAME` overrode it.
  - `spark_models` → `cnv_sparkx26_model_models`: `docker volume inspect spark_models && docker volume create cnv_sparkx26_model_models && docker run --rm -v spark_models:/from:ro -v cnv_sparkx26_model_models:/to alpine:3.21 sh -c "cd /from && cp -a . /to/"`
  - `spark_tokens` → `cnv_sparkx26_model_data`: `docker volume inspect spark_tokens && docker volume create cnv_sparkx26_model_data && docker run --rm -v spark_tokens:/from:ro -v cnv_sparkx26_model_data:/to alpine:3.21 sh -c "cd /from && cp -a . /to/"`
- To retain an old volume instead, set its top-level volume definition to `external: true` and `name: <old-volume-name>` in a local Compose override. Rollback: stop the renamed stack, restore the previous checkout and folder name, and restart the old stack with its original volumes; copy back newer data first if the renamed stack accepted writes.

- `run.bat` loads `.env` from the service folder for a local run: it passes `--dotenv`, the service prints which variable names it loaded (never the values), and a real environment variable still wins. A plain `python src/app.py` and Docker never read `.env`.
- Untracked folders now start with `.` or `_`: `zip_versions/` is `_zip_versions/`. A local `voices`, `tem_code` or `zip_versions` folder from an older checkout needs renaming by hand.
- `run.bat` is now a menu (run the service, deploy, build the release ZIP) and `run.bat run|deploy|zip` skips it. `tools/deploy.bat`, `tools/make_zip.bat` and `tools/deploy.local.example.bat` are gone; the local deploy target is `tools/deploy.local.ini` (copy `tools/deploy.local.example.ini`).
- Admin page: **Connect to this service** card with the addresses other services can use (this page's address, same PC, Docker network, `host.docker.internal`) and every endpoint, each with a Copy button.
- Ollama backend: `SERVICE_THINKING` (`off` or `on`, also on the Settings card) and `SERVICE_HIDE_REASONING` (default on). With hide on, reasoning the model wrote into its answer is dropped (everything before the last `</think>`) and a streamed reply is sent in one piece when complete. Turn it off to stream the raw text.

- Optional Ollama backend: set `SERVICE_OLLAMA_URL` (and `SERVICE_OLLAMA_MODEL`) to answer through an Ollama server with a quantised GGUF, thinking off. Streaming, the answer cache and the admin page work as before; project models and CPU threads are not forwarded. Unset = the torch engine as before.

- Admin **Try it** panel: descriptor-driven public API requests, live text streams, JSON/media output, status and latency, cancellation and redacted curl. Auth choice (b): pasted public token stays in memory; normal scope, limits and caches apply. Shared `web/playground.js` is copied across all model and tool services.

- `run.bat`: runs `uv sync` directly; `python -m uv` failed when uv was installed only as a program (winget).
- Docker: `.dockerignore` keeps `.git`, `.venv`, tests and local state out of the build context (the same file in every model service).
- Docs: set CPU threads to the number of physical cores, not logical CPUs.
- `/metrics` also returns `spark_request_seconds`, a histogram of how long `/v1` calls took on that replica.
- Compose: optional `SERVICE_MEM_LIMIT` caps the container's RAM (default no cap).

- Answer cache (`SERVICE_CACHE_SIZE`, default 256, 0 = off; `X-Cache` header): the last answers to identical `temperature` 0 requests are sent again without generating.
- `POST /v1/chat` accepts `"stream": true`: the answer arrives as JSON lines (`content` pieces, then `done`).

- First version: Spark-X2.6-1.7B served on the CPU with `transformers`, with the admin page, project
  tokens (scope, rate limit, expiry), metrics, Settings card, update checks and weight downloads shared with
  `CNV_BitNet2B_Model`, `CNV_MiniCPM52B_Model` and `DSC_Laya_Model`.
- `POST /v1/chat` takes `messages`, `project`, `temperature` and `max_tokens`. Tools, schemas and thinking are refused
  with 422.
- Project models are checkpoint folders loaded in process (LRU).
