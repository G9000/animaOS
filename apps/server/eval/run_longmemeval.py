"""Run AnimaOS against the LongMemEval benchmark.

LongMemEval has 500 questions across memory abilities:
  - single-session user facts
  - single-session assistant facts
  - single-session preferences
  - temporal reasoning
  - knowledge updates
  - multi-session reasoning
  - abstention

Usage:
  python run_longmemeval.py --base-url http://127.0.0.1:3031 --create-user --limit 50
  python run_longmemeval.py --dataset oracle --config full
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from datetime import datetime
from pathlib import Path

from eval_client import (
    DEFAULT_EVAL_PASSWORD,
    DEFAULT_EVAL_USERNAME,
    DEFAULT_HTTP_BASE_URL,
    HttpAnimaClient,
)

DATA_DIR = Path(__file__).parent / "data"
RESULTS_DIR = Path(__file__).parent / "results"
_ANTHROPIC_HAIKU45_PRICING = {
    "input_usd_per_million_tokens": 1.0,
    "output_usd_per_million_tokens": 5.0,
    "source": "anthropic:claude-haiku-4-5",
}


def load_longmemeval(variant: str = "oracle") -> list[dict]:
    filenames = {
        "oracle": "longmemeval_oracle.json",
        "small": "longmemeval_s_cleaned.json",
    }
    fname = filenames.get(variant)
    if not fname:
        raise ValueError(f"Unknown variant: {variant}. Use 'oracle' or 'small'.")
    path = DATA_DIR / fname
    if not path.exists():
        raise FileNotFoundError(f"Dataset not found at {path}. Run download_data.py first.")
    return json.loads(path.read_text(encoding="utf-8"))


def build_import_sessions(item: dict) -> list[dict[str, object]]:
    sessions: list[dict[str, object]] = []
    haystack_sessions = item.get("haystack_sessions", [])
    dates = item.get("haystack_dates", [])

    for sess_idx, session in enumerate(haystack_sessions):
        turns: list[dict[str, str]] = []
        for turn in session:
            role = str(turn.get("role", "user")).strip().lower()
            if role not in {"user", "assistant"}:
                continue
            content = str(turn.get("content", "")).strip()
            if not content:
                continue
            turns.append({"role": role, "content": content})
        if turns:
            date_str = dates[sess_idx] if sess_idx < len(dates) else ""
            sessions.append({"date": str(date_str), "turns": turns})

    return sessions


def select_dataset(
    dataset: list[dict],
    *,
    offset: int = 0,
    limit: int | None = None,
    sample: str = "sequential",
) -> list[tuple[int, dict]]:
    indexed = list(enumerate(dataset))
    if offset > 0:
        indexed = indexed[offset:]

    if sample == "mixed":
        indexed = _mixed_question_slice(indexed)
    elif sample != "sequential":
        raise ValueError(f"Unknown sample strategy: {sample}")

    if limit is not None:
        indexed = indexed[:limit]
    return indexed


def _mixed_question_slice(indexed: list[tuple[int, dict]]) -> list[tuple[int, dict]]:
    by_type: dict[str, list[tuple[int, dict]]] = {}
    type_order: list[str] = []
    for item in indexed:
        question_type = str(item[1].get("question_type", "unknown"))
        if question_type not in by_type:
            by_type[question_type] = []
            type_order.append(question_type)
        by_type[question_type].append(item)

    mixed: list[tuple[int, dict]] = []
    while any(by_type.values()):
        for question_type in type_order:
            bucket = by_type[question_type]
            if bucket:
                mixed.append(bucket.pop(0))
    return mixed


def load_existing_results(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    results = payload.get("results") if isinstance(payload, dict) else None
    return [result for result in results if isinstance(result, dict)] if isinstance(results, list) else []


def pending_selected_items(
    selected: list[tuple[int, dict]],
    existing_results: list[dict],
) -> list[tuple[int, dict]]:
    completed_ids = {
        str(result.get("question_id"))
        for result in existing_results
        if result.get("question_id") is not None
    }
    return [
        (index, item)
        for index, item in selected
        if str(item.get("question_id", index)) not in completed_ids
    ]


def estimate_response_cost(
    *,
    provider: object,
    model: object,
    usage: object,
) -> dict[str, object] | None:
    if not isinstance(provider, str) or provider.lower() != "anthropic":
        return None
    if not isinstance(model, str) or not model.startswith("claude-haiku-4-5"):
        return None
    if not isinstance(usage, dict):
        return None

    prompt_tokens = _coerce_token_count(
        usage.get("promptTokens") or usage.get("prompt_tokens") or usage.get("input_tokens")
    )
    completion_tokens = _coerce_token_count(
        usage.get("completionTokens")
        or usage.get("completion_tokens")
        or usage.get("output_tokens")
    )
    if prompt_tokens is None or completion_tokens is None:
        return None

    input_rate = _ANTHROPIC_HAIKU45_PRICING["input_usd_per_million_tokens"]
    output_rate = _ANTHROPIC_HAIKU45_PRICING["output_usd_per_million_tokens"]
    prompt_usd = prompt_tokens / 1_000_000 * input_rate
    completion_usd = completion_tokens / 1_000_000 * output_rate
    return {
        "currency": "USD",
        "inputUsdPerMillionTokens": input_rate,
        "outputUsdPerMillionTokens": output_rate,
        "promptUsd": round(prompt_usd, 8),
        "completionUsd": round(completion_usd, 8),
        "totalUsd": round(prompt_usd + completion_usd, 8),
        "pricingSource": _ANTHROPIC_HAIKU45_PRICING["source"],
    }


def _coerce_token_count(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


async def run_question(
    client: HttpAnimaClient,
    item: dict,
    q_index: int,
    *,
    import_mode: str = "raw_chunks",
    embed_import_chunks: bool = False,
) -> dict:
    """Ingest history sessions, then ask the evaluation question."""

    started = time.time()
    question = item["question"]
    expected = item["answer"]
    q_type = item.get("question_type", "unknown")
    q_id = item.get("question_id", q_index)
    sessions = item.get("haystack_sessions", [])

    is_abstention = str(q_id).endswith("_abs")

    await client.reset_memory()
    import_started = time.time()
    import_result = await client.import_transcript_sessions(
        build_import_sessions(item),
        extraction_mode=import_mode,
        embed_raw_chunks=embed_import_chunks,
    )
    import_seconds = time.time() - import_started
    await asyncio.sleep(0.2)

    response_data: dict[str, object] = {}
    answer_started = time.time()
    try:
        response_data = await client.send_message_data(question)
        response = str(response_data.get("response") or response_data.get("message") or "")
    except Exception as exc:
        response = f"ERROR: {exc}"
    answer_seconds = time.time() - answer_started
    total_seconds = time.time() - started

    return {
        "dataset_index": q_index,
        "question_id": q_id,
        "question_type": q_type,
        "question": question,
        "expected_answer": str(expected),
        "ai_response": response,
        "is_abstention": is_abstention,
        "num_sessions": len(sessions),
        "model": response_data.get("model"),
        "provider": response_data.get("provider"),
        "tools_used": response_data.get("toolsUsed", []),
        "retrieval": response_data.get("retrieval"),
        "usage": response_data.get("usage"),
        "cost": estimate_response_cost(
            provider=response_data.get("provider"),
            model=response_data.get("model"),
            usage=response_data.get("usage"),
        ),
        "import": import_result,
        "import_mode": import_mode,
        "embed_import_chunks": embed_import_chunks,
        "timing": {
            "totalSeconds": round(total_seconds, 3),
            "importSeconds": round(import_seconds, 3),
            "answerSeconds": round(answer_seconds, 3),
        },
        "evaluated": False,
    }


async def main() -> None:
    parser = argparse.ArgumentParser(description="Run AnimaOS against LongMemEval")
    parser.add_argument("--base-url", default=DEFAULT_HTTP_BASE_URL)
    parser.add_argument("--username", default=DEFAULT_EVAL_USERNAME)
    parser.add_argument("--password", default=DEFAULT_EVAL_PASSWORD)
    parser.add_argument(
        "--create-user",
        action="store_true",
        help="Create the eval user if login fails. Use only on a disposable eval data directory.",
    )
    parser.add_argument(
        "--reset-endpoint",
        default="/api/eval/reset",
        help="Endpoint used to isolate each benchmark question.",
    )
    parser.add_argument("--dataset", choices=["oracle", "small"], default="oracle")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument(
        "--sample",
        choices=["sequential", "mixed"],
        default="sequential",
        help="Question selection strategy after offset is applied.",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Load an existing output file and skip already completed question IDs.",
    )
    parser.add_argument("--config", default="full")
    parser.add_argument(
        "--import-mode",
        choices=["raw_chunks", "llm_pairs"],
        default="raw_chunks",
        help=(
            "How benchmark transcripts are imported. raw_chunks is fast and "
            "indexes transcript evidence directly; llm_pairs runs the full "
            "LLM extraction pipeline for every turn pair."
        ),
    )
    parser.add_argument(
        "--embed-import-chunks",
        action="store_true",
        help="Generate embeddings for raw import chunks. Slower, but can help when the Rust lexical index is unavailable.",
    )
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    dataset = load_longmemeval(args.dataset)

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    output_path = (
        Path(args.output)
        if args.output
        else RESULTS_DIR / f"longmemeval_{args.dataset}_{args.config}.json"
    )
    selected_items = select_dataset(
        dataset,
        offset=args.offset,
        limit=args.limit,
        sample=args.sample,
    )
    selected_ids = {
        str(item.get("question_id", index))
        for index, item in selected_items
    }
    all_results = (
        [
            result
            for result in load_existing_results(output_path)
            if str(result.get("question_id")) in selected_ids
        ]
        if args.resume
        else []
    )
    pending_items = pending_selected_items(selected_items, all_results)

    client = HttpAnimaClient(
        args.base_url,
        username=args.username,
        password=args.password,
        create_user=args.create_user,
        reset_endpoint=args.reset_endpoint,
    )

    print(f"LongMemEval Benchmark - variant={args.dataset}, config={args.config}")
    print(f"Questions selected: {len(selected_items)}")
    print(f"Questions pending: {len(pending_items)}")
    if args.resume:
        print(f"Resumed existing results: {len(all_results)}")
    print(f"Server: {args.base_url}")
    print(f"Import mode: {args.import_mode}")
    print(f"Embed import chunks: {args.embed_import_chunks}")
    print(f"Sample: {args.sample}, offset: {args.offset}")

    start = time.time()

    try:
        for pending_index, (dataset_index, item) in enumerate(pending_items, start=1):
            result = await run_question(
                client,
                item,
                dataset_index,
                import_mode=args.import_mode,
                embed_import_chunks=args.embed_import_chunks,
            )
            all_results.append(result)
            _save(output_path, args, all_results)
            timing = result.get("timing") or {}
            import_summary = result.get("import") or {}
            completed = len(all_results)
            print(
                f"  ... {completed}/{len(selected_items)} done "
                f"in {timing.get('totalSeconds', '?')}s "
                f"(import {timing.get('importSeconds', '?')}s, "
                f"answer {timing.get('answerSeconds', '?')}s, "
                f"pairs {import_summary.get('turnPairsImported', '?')}, "
                f"pending run {pending_index}/{len(pending_items)})"
            )
    finally:
        await client.close()

    _save(output_path, args, all_results)

    elapsed = time.time() - start
    print(f"\n{'=' * 60}")
    print(f"LongMemEval Results - {args.dataset} / {args.config}")
    print(f"{'=' * 60}")
    print(f"Total questions: {len(all_results)}")
    print(f"Time: {elapsed:.0f}s")
    summary = _summarize_cost_and_usage(all_results)
    if summary["totalCostUsd"] is not None:
        print(f"Estimated model cost: ${summary['totalCostUsd']:.4f}")
    print(f"Results saved to: {output_path}")
    print(f"\nNext step: python score_results.py {output_path}")


def _save(path: Path, args: argparse.Namespace, results: list[dict]) -> None:
    by_type: dict[str, int] = {}
    for result in results:
        question_type = result.get("question_type", "unknown")
        by_type[question_type] = by_type.get(question_type, 0) + 1

    output = {
        "benchmark": "longmemeval",
        "variant": args.dataset,
        "config": args.config,
        "timestamp": datetime.now().isoformat(),
        "selection": {
            "offset": getattr(args, "offset", 0),
            "limit": getattr(args, "limit", None),
            "sample": getattr(args, "sample", "sequential"),
            "resume": getattr(args, "resume", False),
        },
        "results": results,
        "summary": {
            "total": len(results),
            "by_type": by_type,
            **_summarize_cost_and_usage(results),
        },
    }
    path.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")


def _summarize_cost_and_usage(results: list[dict]) -> dict[str, object]:
    prompt_tokens = 0
    completion_tokens = 0
    total_tokens = 0
    total_cost = 0.0
    saw_usage = False
    saw_cost = False

    for result in results:
        usage = result.get("usage")
        if isinstance(usage, dict):
            prompt_tokens += _coerce_token_count(usage.get("promptTokens")) or 0
            completion_tokens += _coerce_token_count(usage.get("completionTokens")) or 0
            total_tokens += _coerce_token_count(usage.get("totalTokens")) or 0
            saw_usage = True
        cost = result.get("cost")
        if isinstance(cost, dict):
            cost_value = cost.get("totalUsd")
            if isinstance(cost_value, int | float):
                total_cost += float(cost_value)
                saw_cost = True

    return {
        "promptTokens": prompt_tokens if saw_usage else None,
        "completionTokens": completion_tokens if saw_usage else None,
        "totalTokens": total_tokens if saw_usage else None,
        "totalCostUsd": round(total_cost, 8) if saw_cost else None,
    }


if __name__ == "__main__":
    asyncio.run(main())
