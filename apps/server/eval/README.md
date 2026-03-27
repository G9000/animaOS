# AnimaOS Evaluation Harness

Benchmarks AnimaOS memory against LoCoMo and LongMemEval datasets.

## Quick Start

```bash
# 1. Download datasets
python download_data.py

# 2. Run LoCoMo benchmark (requires running AnimaOS server)
python run_locomo.py --base-url http://localhost:8000

# 3. Run LongMemEval benchmark
python run_longmemeval.py --base-url http://localhost:8000

# 4. Score results (uses LLM-as-judge via Ollama)
python score_results.py results/locomo_results.json
python score_results.py results/longmemeval_results.json

# 5. Print summary
python print_summary.py results/
```

## Ablation Configs

Run with features toggled to measure each component's contribution:

```bash
# Baseline: no memory system, just conversation history
python run_locomo.py --config baseline

# +Memory: fact extraction + retrieval, no self-model/emotions/reflection
python run_locomo.py --config memory_only

# +Reflection: memory + sleep-time consolidation
python run_locomo.py --config memory_reflection

# Full: everything enabled
python run_locomo.py --config full
```

## Datasets

- **LoCoMo** (Long Conversation Memory): 10 multi-session conversations, ~1986 QA pairs
  across 5 categories: single-hop, temporal, multi-hop, open-ended, adversarial
- **LongMemEval** (ICLR 2025): 500 questions across 5 abilities: information extraction,
  multi-session reasoning, knowledge updates, temporal reasoning, abstention

## Metrics

- **Accuracy**: LLM-as-judge (does the response contain the correct answer?)
- **Per-category breakdown**: scores by question type
- **Ablation delta**: improvement from each enabled feature
