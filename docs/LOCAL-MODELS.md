# Local Models by RAM Tier

*Updated 2026-08 for the current landscape. Iron Jarvis talks to local models
through Ollama (`ollama_base_url`) or any OpenAI-compatible server
(`custom_base_url` — LiteLLM, vLLM, LM Studio). Models without native
tool-calling still work: the router detects them and switches to prompted
tool-calling, and weak models get the decompose (plan → execute → verify)
runtime automatically. Let the app's own quality tracking
(`local_quality_bar`) promote or demote models on evidence.*

**Rules of thumb**

- A low-bit-quantized model needs roughly **RAM ≈ total parameters ×
  0.5–0.6 GB** plus context headroom; MoE models run at their *active*
  parameter speed but occupy their *total* parameter memory.
- Pin every model's context window in `model_context_windows` — the context
  budget plans against it.
- Always pull `nomic-embed-text` in Ollama for embeddings (tiny, CPU-fine).
- Vision needs a vision-capable model: set `model_roles.vision`.

---

## The simple default ladder

| Available RAM | Default agent |
|---|---|
| 8 GB | **Ornith 1.0 9B** |
| 16 GB | **Muse Glimmer 30B** low-bit / **Gemma 4 12B QAT** |
| 24 GB | **Muse Glimmer 30B Q4** |
| 32 GB | **Qwen3.6 35B-A3B MTP** |
| 64 GB | **Qwen3-Coder-Next** / **Qwen3-Next 80B-A3B** |
| 128 GB / 1× DGX Spark | **Qwen3.5 122B-A10B** / **gpt-oss-120b** |
| 256 GB / 2× DGX Spark | **DeepSeek V4-Flash 284B-A13B** |
| 256 GB / 2× Spark, maximum | **Qwen3.5 397B-A17B** |

---

## 2× DGX Spark — 256 GB cluster — frontier local agents

Two DGX Sparks provide 256 GB across two nodes, and NVIDIA officially
supports inference of models up to roughly **400–405B parameters** in a
two-Spark configuration.

| Model / configuration | Best for |
|---|---|
| **DeepSeek V4-Flash 284B-A13B** | Best giant-agent recommendation. Coding, reasoning, long-running agents, huge context. |
| **Qwen3.5 397B-A17B** | Maximum-capability multimodal agent that still falls inside NVIDIA's ~400B dual-Spark target. |
| **2× Qwen3.5 122B-A10B** | Best multi-agent configuration. One large independent agent per Spark. |
| **2× Qwen3-Coder-Next / Qwen3-Next** | Parallel coding agents, repository work, research, testing, subagent orchestration. |
| **Mixed agents** | One Spark runs the main reasoning/coding agent; the other handles vision, research, verification, or another concurrent agent. |

### Recommended giant model: DeepSeek V4-Flash 284B-A13B

284B total parameters, **13B active**, native **1M context**. DeepSeek
specifically reports strong coding, reasoning, and agentic performance, and
its compressed attention architecture dramatically reduces long-context
KV-cache requirements. For Iron Jarvis this is the most interesting model to
span both Sparks when you want **one extremely capable local agent**.

### Maximum model: Qwen3.5 397B-A17B

397B total / **17B active**, native 262K context expandable to ~1M,
multimodal input, and explicit agent/tool-use training — strong reported
results across general-agent, MCP/tool, search-agent, and coding-agent
benchmarks. It sits almost exactly at NVIDIA's stated ~400B dual-Spark
inference ceiling, so use an appropriately low-bit quant and expect **less
context headroom** than with DeepSeek V4-Flash.

### But don't always combine the Sparks

For agentic workloads, **two independent models can beat one giant sharded
model**. NVIDIA notes that workloads with minimal communication between
Sparks scale much better, while sharded LLM inference requires repeated
synchronization across nodes. That makes this configuration especially
attractive:

- **Spark 1** → primary coding / reasoning agent
- **Spark 2** → research / verifier / reviewer / second coding agent
- **Iron Jarvis** → orchestrates both

For example:

- Qwen3.5 122B **Agent A → implement**, Qwen3.5 122B **Agent B → review /
  test / verify**, or
- Qwen3-Coder-Next → **coding**, Qwen3.5 122B → **reasoning / review /
  vision**.

This is often more useful for real autonomous work than consuming both
machines to run one 397B model — and it maps directly onto Iron Jarvis's
supervisor → specialist delegation: register each Spark's endpoint as its own
provider (or LiteLLM alias) and let the Team job / delegate tool split
implement-vs-verify across them.

### The 2-Spark agent recommendation, in one line each

- **Default:** run **DeepSeek V4-Flash** across the cluster when a task needs
  maximum single-agent intelligence.
- **Multi-agent work:** run **one powerful model per Spark** and let Iron
  Jarvis delegate work between them.
- **Maximum:** use **Qwen3.5 397B-A17B** when the task warrants spending
  nearly the entire cluster on one multimodal frontier agent.

---

## Wiring it into Iron Jarvis, step by step

1. Serve the model (Ollama pull, or LiteLLM/vLLM for the big tiers; on the
   Spark cluster keep **LiteLLM in front** with stable aliases like `fleet` /
   `vision` / `frontier` and repoint them as models improve — the app never
   hardcodes an id).
2. Connections page → set `ollama_base_url`+`ollama_model` or
   `custom_base_url`+`custom_model`. The health card should go green.
3. Pin the context window in `model_context_windows` (especially the 1M-class
   models — pin what you actually serve, not the theoretical max).
4. Pull `nomic-embed-text` for embeddings.
5. Optional: `model_roles` for plan/extract/vision/judge routing, and
   `routing_local_ladder` for explicit fallback order. On a 2-Spark split,
   point `plan`/`judge` at the reasoning Spark and `extract`/`vision` at the
   other.
6. Work normally for a few sessions, then check the model's averages; flip
   `prefer_local_when_capable` once it clears `local_quality_bar`. The router
   never routes client data to a cloud API as a fallback — an unreachable
   local model refuses by name.
