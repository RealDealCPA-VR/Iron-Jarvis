# Local Models by RAM Tier

*Recommendations current as of early 2026 (model landscape moves fast — treat
this as a starting grid, and let the app's own quality tracking promote or
demote models on evidence). Iron Jarvis talks to local models through Ollama
(`ollama_base_url`) or any OpenAI-compatible server (`custom_base_url` —
LiteLLM, vLLM, LM Studio). Models without native tool-calling still work:
the router detects them and switches to prompted tool-calling, and weak
models get the decompose (plan → execute → verify) runtime automatically.*

**Rules of thumb**

- A Q4-quantized model needs roughly **RAM ≈ parameters × 0.6 GB** plus a few
  GB for context. VRAM counts first if the model fits on the GPU; unified
  memory (Mac, DGX Spark) counts whole.
- Pin every model's context window in `model_context_windows` — the context
  budget plans against it.
- Always pull `nomic-embed-text` in Ollama for embeddings (tiny, CPU-fine).
- Vision needs a vision model: set `model_roles.vision` or attach images only
  when a capable model answers.

---

## 8 GB — "chat and summaries, honestly"

| Role | Model | Notes |
|---|---|---|
| Daily chat | **Llama 3.2 3B** or **Qwen2.5 3B Instruct** | Snappy, fine for drafting, rewriting, summaries. |
| Slightly stronger | **Phi-4-mini** (3.8B) | Punchy for its size on reasoning-ish tasks. |
| Embeddings | `nomic-embed-text` | Always. |

Expectations: don't run agent sessions on this tier — route agent/tool work
to a connected CLI (Claude/Codex) and keep local for conversation. Keep
`decompose_local_tasks: true`.

## 16 GB — "a real local assistant"

| Role | Model | Notes |
|---|---|---|
| Daily chat | **Qwen3 8B** or **Llama 3.1 8B Instruct** | The workhorse class; Qwen3's hybrid reasoning mode is a real step up. |
| Code | **Qwen2.5-Coder 7B** | Best small coder as of the cutoff. |
| Reasoning | **DeepSeek-R1-Distill-Qwen-7B** | Slower, thinks out loud, better on multi-step problems. |
| Frontier-open (tight) | **gpt-oss-20b** (MXFP4) | OpenAI's open model runs in ~14 GB and has native tools; excellent agent candidate if it fits alongside your apps. |
| Vision | **Qwen2.5-VL 7B** | For `model_roles.vision`. |

Expectations: light agent sessions work (with decompose); keep
`max_agent_steps` at 12 and let auto-tier prove quality before
`prefer_local_when_capable`.

## 32 GB — "local agents that hold up"

| Role | Model | Notes |
|---|---|---|
| Daily chat + agents | **Qwen3 14B** or **Phi-4 14B**; **Mistral Small 3.x 24B** (Q4) for a more natural writing voice | 14B is where instruction-following stops feeling brittle. |
| Strong generalist | **Gemma 3 27B** (Q4) | Also covers vision. |
| Fast MoE | **Qwen3-30B-A3B** | MoE: 30B quality at ~3B active speed — great chat feel. |
| Code | **Qwen2.5-Coder 14B** | |
| Reasoning | **DeepSeek-R1-Distill-Qwen-14B** | |

## 64 GB — "the local dailies get serious"

| Role | Model | Notes |
|---|---|---|
| Flagship dense | **Llama 3.3 70B** (Q4, ~40 GB) or **Qwen2.5 72B** (Q4) | Instruction quality approaching last-gen cloud. |
| Flagship current | **Qwen3-32B** (Q8 or FP16) | Often beats bigger last-gen dense models; leaves headroom for real context. |
| Agent/tools | **gpt-oss-20b** with room to breathe, big context | |
| Reasoning | **DeepSeek-R1-Distill-Llama-70B** (Q4) | |

## 128 GB unified (DGX Spark / Mac Studio class) — "own the whole ladder"

| Role | Model | Notes |
|---|---|---|
| Frontier-open | **gpt-oss-120b** (MXFP4, ~60–70 GB) | The headline: native tool use, strong reasoning, fits with room for a second model. |
| Big MoE | **GLM-4.5-Air** (106B MoE) or **Qwen3-235B-A22B** (aggressive quant) | The 235B only at Q3-class quants — test before trusting. |
| Dense quality | **Llama 3.3 70B at Q8/FP8** | Full-precision-feeling 70B. |
| Vision | **Qwen2.5-VL 32B** | |
| Serving | **LiteLLM in front** (`custom_base_url`), aliases like `fleet` / `vision` / `frontier` | Then `model_roles` maps steps to aliases and the app never hardcodes an id. |

*(This is the tier the DGX Spark fleet endpoint already serves — keep the
LiteLLM aliases stable and repoint them as models improve.)*

---

## Wiring it into Iron Jarvis, step by step

1. Serve the model (Ollama pull, or LiteLLM/vLLM for the big tiers).
2. Connections page → set `ollama_base_url`+`ollama_model` or
   `custom_base_url`+`custom_model`. The health card should go green.
3. Pin the context window in `model_context_windows`.
4. Pull `nomic-embed-text` for embeddings.
5. Optional: `model_roles` for plan/extract/vision/judge routing, and
   `routing_local_ladder` for explicit fallback order.
6. Work normally for a few sessions, then check the model's average on the
   Usage/eval data; flip `prefer_local_when_capable` once it clears
   `local_quality_bar`. The router never routes client data to a cloud API as
   a fallback — an unreachable local model refuses by name.
