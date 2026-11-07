# CNV_SparkX26_Model: without Docker, on Windows

Run Spark-X2.6-1.7B on the CPU directly with Python: one process, no load balancer, listening on `127.0.0.1:29203`.

## Requirements
- Windows with Python 3.11 or newer and uv (`winget install astral-sh.uv`).
- Internet on the first start (about 4 GB of weights).
- RAM for the model (one copy).

## Start
Double-click `run.bat`, or run it in a terminal from `CNV_SparkX26_Model\`. Choose **1 Run the service** in the menu, or run `run.bat run` to skip the menu. It runs `uv sync` to create or update `.venv`, then starts
`python src/app.py`. The first start downloads about 4 GB of weights.

Linux or macOS:
```bash
cd CNV_SparkX26_Model
uv sync
uv run python src/app.py
```

## Settings
`run.bat` loads `.env` in this folder (copy `.env.example`) through `--dotenv` and prints the names it loaded, never the values; a real environment variable wins over it. A plain `python src/app.py` does not read `.env`. Set variables in `.env` or in the environment before starting:

| Variable | Default | Effect |
|---|---|---|
| `SERVICE_HOST` | `127.0.0.1` | Bind address. Use `0.0.0.0` only if other devices must reach it. |
| `SERVICE_PORT` | `29203` | Port. |
| `SERVICE_ADMIN_PASSWORD` | empty | Unlocks `/admin/` and requires tokens on every call but `/health`. Empty = no auth. |
| `SERVICE_WARM` | `1` | Load the model at start; with `0` the first request pays the load time. |
| `SERVICE_NUM_THREAD` | the library decides | CPU threads. Set it to the number of **physical** cores. |
| `SERVICE_MODELS_DIR` | the library's default cache | Folder for downloaded model weights. Sets `HF_HOME` (and `OLLAMA_MODELS` to `<folder>\ollama`) at start; a real `HF_HOME` or `OLLAMA_MODELS` wins. Docker keeps weights in the `_models` volume and ignores it. |

PowerShell: `$env:SERVICE_ADMIN_PASSWORD = "secret"; .\run.bat`
Bash: `SERVICE_ADMIN_PASSWORD=secret uv run python src/app.py`

## Check
```bash
curl http://127.0.0.1:29203/health
```
`ready` is `false` until the model has loaded. Then open `http://127.0.0.1:29203/admin/` (see [guide-web-ui.md](guide-web-ui.md)),
make a token, and try a call:
```bash
curl -X POST http://127.0.0.1:29203/v1/chat -H "Content-Type: application/json" -H "X-Internal-Token: spark_..." -d '{"messages":[{"role":"user","content":"Say hello in one sentence."}]}'
```

## Stop
Ctrl+C in the terminal. Do not run this and the Docker stack together: both use port 29203.

## Tests
`uv run pytest`.
