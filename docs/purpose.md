# Purpose

**Kind:** chat messages → text (conversation, small)

**Group:** Conversation

**Tags:** `cpu`, `streaming`, `project-models`

| | |
|---|---|
| Input | Chat messages, temperature, max tokens, project. |
| Output | JSON: content, finish reason, usage. Streamable. |
| Runs on | CPU (`transformers`, Spark-X2.6-1.7B). |

Same API as BitNet and LFM3.

**Use for:** third small chat model; A/B candidate for project fine-tunes.
**Not for:** tool calls, schemas, thinking (all return 422).
