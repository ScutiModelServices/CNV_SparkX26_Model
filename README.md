# SparkX26Model

The one place Spark-X2.6-1.7B is served. Projects send it chat turns over HTTP instead of loading the
model themselves. It has the same admin page, tokens, rate limits, metrics, settings and update checks as
`BitNet2BModel`, `MiniCPM52BModel` and `LayaModel`.

Spark-X2.6-1.7B is run in this process on the CPU with `transformers` (`trust_remote_code`, so the model code comes from
the Hugging Face repository). A 4-bit GGUF in a llama.cpp runtime is lighter and faster; this service keeps one
engine shape for all LLM services instead.

## API

| Call | Purpose |
|---|---|
| `POST /v1/chat` | `{"messages", "project", "temperature", "max_tokens", "stream"}` → `{"content", "finish_reason", "model", "fallback", "usage", "duration_ms"}`. |
| `GET /health` | `{"status": "ok", "model", "ready", "num_thread", "project_models"}`; `ready` is true once the base model is loaded. No token needed. |

**Streaming.** With `"stream": true` the reply is `application/x-ndjson`: one JSON object per line while the answer is
made, so the first words arrive at once instead of after the whole answer. Lines are `{"type": "content", "text"}`
and finally `{"type": "done", ...}` holding the same body a call without `stream` returns. A refusal before
the first piece (a conversation that is too long, a full queue) is still a normal HTTP error; a failure after it is
sent as `{"type": "error", "status", ...}` and ends the stream. A kept answer (see below) is streamed back in one piece.

**Cache.** The *Answers kept per replica* setting (`SPARK_CACHE_SIZE`, default 256, 0 = off) keeps the last answers
for identical requests and sends them again without generating (`X-Cache: hit`). Only requests with `temperature` 0
(the default) are kept, because a higher temperature asks for a different answer each time. Project model files are not watched (see the note on project models), so a replaced checkpoint is only noticed after a restart; new base weights empty the cache. The cache is per
replica, so with N replicas the hit rate is about 1/N. `stream` does not change the key: a streamed answer fills the
cache for plain calls and the other way round.

`messages` use the roles `system`, `user` and `assistant`. `temperature` 0 (the default) is greedy. `max_tokens` is
at most 4096 and the whole conversation must fit the model's context window (422 otherwise).

**Not supported.** `tools`, `schema` and `thinking` are refused with 422, not ignored: this service sends plain chat turns with thinking switched off and has no
constrained decoding. Use `MiniCPM52BModel` for those.

**Project models.** Optional `"project": "my-project"` (`^[a-z0-9][a-z0-9_-]{0,47}$`) uses the checkpoint folder
`<project models folder>/my-project` when it holds a complete Hugging Face checkpoint of the same architecture
(`config.json`, weights, tokenizer). Up to *Project models loaded per replica* stay in memory, least recently used out
first. A missing or unloadable project uses the base model and returns `"fallback": true`. `model` names the model
that answered. Replacing the files of an already loaded project needs a replica restart.

## Admin page

`/admin/` (password `SPARK_ADMIN_PASSWORD`): per-project tokens with project scope, rate limit and expiry, usage
numbers, CPU threads, the Settings card (update check hours, in-flight cap, answers kept, project models), and the Updates card
(**Check now** and **Download new weights** for the Hugging Face repository). New weights are downloaded once into
the shared models volume; the other replicas notice within a few seconds and load them on their next request.
`/metrics` (admin password) is Prometheus text per token. `spark_request_seconds` is a histogram of how long each `/v1` call took on the replica that answers (to the first byte for a streamed reply); it starts from 0 when the replica restarts.

## Run

```bash
docker compose up -d --build        # http://<host>:9180
```

Without Docker: `run.bat` (uv, listens on `127.0.0.1:9180`). The first start downloads about 4 GB. Each replica holds
the model in RAM (about 5 GB), so `SPARK_REPLICAS` multiplies memory use. See `deployment.md`.

## Tests

```bash
uv sync
uv run pytest
```

## Limits worth knowing

- One generation at a time per replica; extra calls wait, and more than the in-flight cap get 429.
- The rate limit is per replica.
- A generation stops after 120 s and returns what it has with `finish_reason: "length"`.
- `TORCHDYNAMO_DISABLE=1` is set by the service; unpacking the weights otherwise tries to compile C++ and fails on
  machines without a compiler.


## Try it from the admin page

Open `/admin/` and use **Try it** to call the public API with a service token held only in memory. Runs use normal token scopes, limits and caches; the panel shows HTTP status, latency, responses, cancellation and a curl equivalent with a token placeholder. See [the web UI guide](docs/guide-web-ui.md#try-it-send-a-real-request).

Chat sends messages to `POST /v1/chat`, with optional project, temperature, max_tokens and streaming. Spark rejects tools, schema and thinking with 422; its form does not send those MiniCPM-only fields.
