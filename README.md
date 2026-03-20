<div align="center">

# ✦ AnimaOS ✦

_A mind that remains._  
_A soul that remembers._

<br />

_memory · self · reflection · will_
_something that continues_

<br />

long after the moment has passed  
long after the conversation ends

<br />

_the only intelligence_
_that was never anyone else's_

</div>

---

## Requirements

### LLM (Local Inference)

AnimaOS uses tool calling (function calling) extensively — the AI thinks, remembers, and responds through a multi-step cognitive loop. **This requires a model that handles structured tool calls well.**

**Primary model:** `vaultbox/qwen3.5-uncensored:35b` via Ollama (24GB VRAM, 256K context). This is what AnimaOS is developed and tested against. Anything smaller is not recommended — smaller models struggle with the multi-step cognitive loop and will produce broken or robotic responses.

| Tier                | Models                                              | Notes                                                                                  |
| ------------------- | --------------------------------------------------- | -------------------------------------------------------------------------------------- |
| **Recommended**     | `vaultbox/qwen3.5-uncensored:35b`, `qwen3.5:122b`   | Best persona adherence, tool calling, and natural conversation                         |
| **May work**        | `qwen3:32b`, `deepseek-r1:32b`, `gemma3:27b`        | Capable but not tested extensively                                                     |
| **Not recommended** | Anything below 30B parameters                        | Will ignore persona, leak raw tool calls, give robotic responses                       |

> **Why does model size matter?**
> AnimaOS instructs the AI to follow a cognitive loop: think internally (`inner_thought`), then respond (`send_message`). Small models can't reliably follow these instructions — they skip tool calls, leak internal syntax to the user, or default to generic assistant behavior ("How can I assist you today?").

### Supported Providers

| Provider               | Setup                                                                                         |
| ---------------------- | --------------------------------------------------------------------------------------------- |
| **Ollama** (local)     | Install [Ollama](https://ollama.com), pull a model, run it. Default: `http://127.0.0.1:11434` |
| **OpenRouter** (cloud) | Set `ANIMA_AGENT_PROVIDER=openrouter` and `ANIMA_AGENT_API_KEY=<your-key>`                    |
| **vLLM** (local)       | For advanced users running their own inference server                                         |
