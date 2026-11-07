# Changelog

## Unreleased

- Untracked folders now start with `.` or `_`: `zip_versions/` is `_zip_versions/`. A local `voices`, `tem_code` or `zip_versions` folder from an older checkout needs renaming by hand.
- `run.bat` is now a menu (run the service, deploy, build the release ZIP) and `run.bat run|deploy|zip` skips it. `tools/deploy.bat`, `tools/make_zip.bat` and `tools/deploy.local.example.bat` are gone; the local deploy target is `tools/deploy.local.ini` (copy `tools/deploy.local.example.ini`).
- Admin page: **Connect to this service** card with the addresses other services can use (this page's address, same PC, Docker network, `host.docker.internal`) and every endpoint, each with a Copy button.
- Ollama backend: `SPARK_THINKING` (`off` or `on`, also on the Settings card) and `SPARK_HIDE_REASONING` (default on). With hide on, reasoning the model wrote into its answer is dropped (everything before the last `</think>`) and a streamed reply is sent in one piece when complete. Turn it off to stream the raw text.

- Optional Ollama backend: set `SPARK_OLLAMA_URL` (and `SPARK_OLLAMA_MODEL`) to answer through an Ollama server with a quantised GGUF, thinking off. Streaming, the answer cache and the admin page work as before; project models and CPU threads are not forwarded. Unset = the torch engine as before.

- Admin **Try it** panel: descriptor-driven public API requests, live text streams, JSON/media output, status and latency, cancellation and redacted curl. Auth choice (b): pasted public token stays in memory; normal scope, limits and caches apply. Shared `web/playground.js` is copied across all model and tool services.

- `run.bat`: runs `uv sync` directly; `python -m uv` failed when uv was installed only as a program (winget).
- Docker: `.dockerignore` keeps `.git`, `.venv`, tests and local state out of the build context (the same file in every model service).
- Docs: set CPU threads to the number of physical cores, not logical CPUs.
- `/metrics` also returns `spark_request_seconds`, a histogram of how long `/v1` calls took on that replica.
- Compose: optional `SPARK_MEM_LIMIT` caps the container's RAM (default no cap).

- Answer cache (`SPARK_CACHE_SIZE`, default 256, 0 = off; `X-Cache` header): the last answers to identical `temperature` 0 requests are sent again without generating.
- `POST /v1/chat` accepts `"stream": true`: the answer arrives as JSON lines (`content` pieces, then `done`).

- First version: Spark-X2.6-1.7B served on the CPU with `transformers`, with the admin page, project
  tokens (scope, rate limit, expiry), metrics, Settings card, update checks and weight downloads shared with
  `BitNet2BModel`, `MiniCPM52BModel` and `LayaModel`.
- `POST /v1/chat` takes `messages`, `project`, `temperature` and `max_tokens`. Tools, schemas and thinking are refused
  with 422.
- Project models are checkpoint folders loaded in process (LRU).
