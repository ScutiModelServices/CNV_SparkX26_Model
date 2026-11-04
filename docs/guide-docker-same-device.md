# SparkX26Model: Docker Desktop on this PC

Run Spark-X2.6-1.7B on the CPU in Docker Desktop on the machine you work on, so other programs on it can call it.

## Requirements
- Docker Desktop with Compose v2 (Windows: WSL2 backend).
- RAM for the model **per replica**. Keep `SPARK_REPLICAS=1` on a PC. Docker Desktop on Windows caps its engine at about half of your RAM unless you raise it in `.wslconfig`.
- Internet on the first start (about 4 GB of weights).

## Steps
1. Configure. In `SparkX26Model/`:
   ```bash
   cp .env.example .env
   ```
   Set `SPARK_ADMIN_PASSWORD` (empty = no auth and no admin page, fine for a local test). Leave `SPARK_PORT=9180`. Leave `SPARK_REPLICAS=1`.
2. Start:
   ```bash
   docker compose up -d --build
   ```
   The first start downloads about 4 GB of weights into the `models` volume. `spark_lb` starts only after the service is healthy
   (up to 300 s grace). Watch with `docker compose ps` and `docker compose logs -f spark`.
3. Check:
   ```bash
   curl http://127.0.0.1:9180/health
   ```
   `ready` is `false` until the model has loaded, then `true`.
4. Open the admin page: `http://127.0.0.1:9180/admin/` (see [guide-web-ui.md](guide-web-ui.md)) and make a token for each caller.
5. Try a call:
   ```bash
   curl -X POST http://127.0.0.1:9180/v1/chat -H "Content-Type: application/json" -H "X-Internal-Token: spark_..." -d '{"messages":[{"role":"user","content":"Say hello in one sentence."}]}'
   ```
   Expect a JSON answer.
6. Callers connect to the published port. The caller holds the URL and token:
   - Caller on the host: `http://127.0.0.1:9180`.
   - Caller in another Docker stack on the same host: `http://host.docker.internal:9180`.

## Daily use
```bash
docker compose ps          # state
docker compose logs -f spark
docker compose down        # stop (the weights volume is kept)
docker compose up -d --build && docker compose restart spark_lb   # after a code change
```

## Problems
| Symptom | Fix |
|---|---|
| `address already in use` on 9180 | Another copy (`run.bat`, or the same stack on another compose project) is running. Stop it or change `SPARK_PORT`. |
| `/health` shows `ready: false` for minutes | First start: the weights are downloading or loading. Check `docker compose logs -f spark`. |
| 401 Invalid token | The caller's token is missing or revoked; make one on `/admin/`. |

| Container restarts in a loop | Out of RAM. Raise the Docker Desktop memory limit or lower the replica count. |
