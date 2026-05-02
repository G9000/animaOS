# AnimaOS Evaluation Harness

This harness evaluates AnimaOS memory and agent behavior with three layers:

- conversation smoke evals for fast prompt/runtime regression checks
- LongMemEval as the primary long-memory benchmark
- LoCoMo as the heavier release/stress benchmark
- LLM-as-judge scoring and summary reporting for repeatable comparisons

Benchmark output rows include the final answer plus model, provider, tools used,
and retrieval metadata from `/api/chat` so failures can be debugged from the
result file.

## Quick Smoke

Run the lightweight behavioral suite without starting a separate server:

```bash
uv run --project apps/server python apps/server/eval/run_agent_eval.py --profile pr
```

This is the best PR-gate eval. It creates isolated temporary state, forces a
local Ollama-compatible provider by default, and disables background memory
work so the run is not affected by provider settings from the desktop app or by
sleep-time database writes.

Override the local model when needed:

```bash
uv run --project apps/server python apps/server/eval/run_agent_eval.py \
  --profile pr \
  --agent-provider ollama \
  --agent-model qwen3:8b \
  --agent-base-url http://127.0.0.1:11434
```

For a fast infrastructure-only smoke that does not call any model, use the
scaffold provider directly. This exercises routing, auth, persistence, and
reset plumbing, but it is not a real behavioral-quality score:

```bash
uv run --project apps/server python apps/server/eval/run_conversation_eval.py \
  --mode in-process \
  --agent-provider scaffold \
  --agent-model scaffold \
  --disable-background-memory \
  --limit 1
```

To see what any profile will run without executing it:

```bash
uv run --project apps/server python apps/server/eval/run_agent_eval.py --profile release --score --dry-run
```

## Benchmark Server Setup

Run LoCoMo and LongMemEval against a disposable eval data directory. Do not run
destructive eval reset against your personal development Core.

PowerShell:

```powershell
$env:ANIMA_DATA_DIR = "$PWD\.anima\eval"
$env:ANIMA_EVAL_RESET_ENABLED = "true"
$env:ANIMA_CORE_REQUIRE_ENCRYPTION = "false"
bun run dev:server
```

The benchmark runners use `eval` / `eval-password` by default. On a fresh eval
data directory, pass `--create-user` once so the runner provisions that account.

## Memory Benchmarks

Use the profile runner for standard runs.

Nightly primary memory eval:

```bash
uv run --project apps/server python apps/server/eval/run_agent_eval.py \
  --profile nightly \
  --create-user
```

Release eval with judge scoring:

```bash
uv run --project apps/server python apps/server/eval/run_agent_eval.py \
  --profile release \
  --score
```

Architecture ablation slice:

```bash
uv run --project apps/server python apps/server/eval/run_agent_eval.py \
  --profile ablation
```

The profile policy is:

- `pr`: in-process conversation smoke only; no external server required; local Ollama provider forced by default; background memory disabled
- `nightly`: LongMemEval oracle `--limit 50`
- `release`: full LongMemEval oracle, then LoCoMo categories `1,2,3,5`
- `ablation`: LongMemEval oracle `--limit 50` across `baseline`, `memory_only`, `memory_reflection`, and `full`

You can still run individual benchmark scripts directly when debugging.

LoCoMo, quick category 1-3 run:

```bash
uv run --project apps/server python apps/server/eval/run_locomo.py \
  --base-url http://127.0.0.1:3031 \
  --create-user \
  --limit 1 \
  --categories 1,2,3
```

LongMemEval oracle subset:

```bash
uv run --project apps/server python apps/server/eval/run_longmemeval.py \
  --base-url http://127.0.0.1:3031 \
  --create-user \
  --dataset oracle \
  --limit 50
```

After the eval user already exists, omit `--create-user`.

## Scoring

Start Ollama and pull the judge model, then score result files:

```bash
ollama pull qwen3:8b
uv run --project apps/server python apps/server/eval/score_results.py apps/server/eval/results/locomo_full.json --model qwen3:8b
uv run --project apps/server python apps/server/eval/score_results.py apps/server/eval/results/longmemeval_oracle_full.json --model qwen3:8b
uv run --project apps/server python apps/server/eval/print_summary.py apps/server/eval/results
```

Use a stronger local judge, such as `qwen3:32b`, for release-quality scoring
when available.

## Ablations

The `--config` value is recorded as a result label:

```bash
uv run --project apps/server python apps/server/eval/run_locomo.py --config baseline --limit 1
uv run --project apps/server python apps/server/eval/run_locomo.py --config memory_only --limit 1
uv run --project apps/server python apps/server/eval/run_locomo.py --config memory_reflection --limit 1
uv run --project apps/server python apps/server/eval/run_locomo.py --config full --limit 1
```

Use the same dataset slice and judge model across configs. Compare with:

```bash
uv run --project apps/server python apps/server/eval/print_summary.py apps/server/eval/results
```

## Industry-Standard Gate

Recommended cadence:

- PR: unit tests plus `run_conversation_eval.py --mode in-process`
- Nightly: LongMemEval oracle `--limit 50`
- Release: full LongMemEval oracle, LoCoMo categories `1,2,3,5`, and ablation comparison
- Product regression set: add Anima-specific cases for memory updates, stale-memory correction, privacy/forgetting, and tool-call correctness as real usage patterns emerge

Track these with every result file:

- git SHA
- model and provider
- eval config label
- dataset and limit
- judge model
- accuracy by category
- token/latency metadata when available
- retrieval metadata for failed cases

## Safety Notes

`/api/eval/reset` is disabled unless `ANIMA_EVAL_RESET_ENABLED=true`. It deletes
benchmark-generated memory, runtime messages, candidates, retrieval feedback,
working context, and related eval state for the authenticated user. Use it only
on disposable eval data directories.
