# Deploying SparkX26Model

SparkX26Model is one HTTP service (port 9180). It runs as `spark` replicas behind an nginx load balancer,
`spark_lb`. Callers use `http://<host>:9180`; they never reach a replica directly.

## Ways to run it

Each path has its own guide. Pick one.

| Path | Guide |
|---|---|
| Docker Desktop on this PC | [guide-docker-same-device.md](docs/guide-docker-same-device.md) |
| Docker on another always-on machine, published with `tools\deploy.bat` | [guide-docker-deployment-device.md](docs/guide-docker-deployment-device.md) |
| Directly on Windows, no Docker | [guide-no-docker-same-device.md](docs/guide-no-docker-same-device.md) |

Once it runs, the admin page is covered by [guide-web-ui.md](docs/guide-web-ui.md).
