# Purpose

**Kind:** chat messages → text (conversation, small)

**Group:** Conversation

**Tags:** `cpu`, `streaming`

| | |
|---|---|
| Input | Chat messages, temperature, max tokens and stream. |
| Output | JSON content, finish reason, usage; NDJSON streaming. |
| Runs on | API on CPU; Spark-X2.6-1.7B runs in external Ollama using its configured hardware. Memory has not been measured for this deployment. |

**Use for:** small-model chat through a shared Ollama service.
**Not for:** project checkpoint selection, tool calls, schemas or request-level thinking (422).
