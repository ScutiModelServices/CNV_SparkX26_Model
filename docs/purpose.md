# Purpose

**Kind:** chat messages → text (conversation, small)

**Group:** Conversation

**Tags:** `cpu`, `streaming`, `host-threads`, `variants`

| | |
|---|---|
| Input | Chat messages, temperature, max tokens and stream. |
| Output | JSON content, finish reason, usage; NDJSON streaming. |
| Runs on | API on CPU; Spark-X2.6-1.7B runs in external Ollama using its configured hardware. The Model variant card lists the quantizations (`Q4_K_M`, `Q8_0`) with an estimate of the memory each takes and whether this machine can run it. Memory has not been measured for this deployment. |

**Use for:** small-model chat through a shared Ollama service.
**Not for:** project checkpoint selection, schemas or request-level thinking (422).
