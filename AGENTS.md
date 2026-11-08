# CNV_SparkX26_Model

Spark-X2.6-1.7B served over HTTP, behind the shared model-service admin page, tokens and settings. Port 29203, env prefix `SERVICE_`, token prefix `spark_`.

## Agent instructions
This file is the one instruction file for every coding agent; `CLAUDE.md` only imports it (`@AGENTS.md`). Edit this file, never `CLAUDE.md`.

## Commands (from this folder, venv `.venv`, managed by uv)
- Install: `uv sync` (`winget install astral-sh.uv` if uv is missing)
- Test: `uv run pytest -q`. `tests/test_admin_ui.py` needs Playwright and its browser and is skipped without it. On Windows, if uv fails with "cannot move the file to a different disk drive", set `UV_CACHE_DIR` to a folder on the same drive and `UV_LINK_MODE=copy`.
- Run: `run.bat run`, or `uv run python src/app.py` (add `--dotenv` to load `.env`), or `docker compose up -d --build`

## Rules
- Load downloaded weights only; no startup, warm-up or request downloads. Only the admin Updates Download callback downloads, then replica markers invalidate old weights. Missing files keep health/admin available and inference returns 503 with an admin Download hint.
- Follow the `model-service-parity` skill and `AI/ModelServices/_docs/SHARED-FEATURES.md` (what must match in every model service) and `AI/ModelServices/_docs/SERVICE-SPECIFIC.md` (what differs on purpose). A change to a shared feature goes to every service; a new difference goes into `SERVICE-SPECIFIC.md` in the same change.
- `web/index.html`, `src/tokens.py`, `src/settings.py`, `src/updates.py` and `.dockerignore` are copies shared with the other model services. Change one, copy it to the others, run `python tools/check_sync.py` in each repo.
- Generation uses Ollama exclusively through `src/ollama_backend.py`; keep injected fake loaders in tests. Unset local URL defaults to localhost; explicitly empty URL is an error.
- API starts while Ollama is unavailable: health reports not ready and chat returns sanitized 503 with a admin Updates Download hint. Warm-up stays in the background.
- Public chat fields are messages, temperature, max_tokens and stream; unknown fields return 422. CPU thread values are display-only; Ollama manages its own threads.
- Update invalidation markers live alongside tokens in the shared data volume. Ollama stores its own weights.
- Behaviour changes update `README.md`, `deployment.md`, `docs/guide-web-ui.md`, `.env.example` and `CHANGELOG.md` in the same commit.
- Never read or print `.env`, `tokens.json` or `settings.json`.
- Commit with the noreply author email `214214011+scutiii2@users.noreply.github.com` (another address is rejected on push). Push `main`, then run `python tools/publish_live.py`. Build Docker only when the Dockerfile or the dependencies changed.
