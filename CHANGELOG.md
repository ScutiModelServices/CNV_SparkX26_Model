# Changelog

## Unreleased

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
