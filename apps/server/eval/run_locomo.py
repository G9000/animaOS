"""Run AnimaOS against the LoCoMo benchmark.

LoCoMo has 10 conversations, each with roughly 19 sessions and 150-200
questions.

Categories:
  1 = single-hop factual recall
  2 = temporal reasoning
  3 = multi-hop reasoning
  4 = open-ended inference
  5 = adversarial / unanswerable

Usage:
  python run_locomo.py --base-url http://127.0.0.1:3031 --create-user --limit 1
  python run_locomo.py --config memory_only --categories 1,2,3
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

CATEGORY_NAMES = {
    1: "single_hop",
    2: "temporal_reasoning",
    3: "multi_hop",
    4: "open_ended",
    5: "adversarial",
}


def load_locomo() -> list[dict]:
    path = DATA_DIR / "locomo_dataset.json"
    if not path.exists():
        raise FileNotFoundError(
            f"LoCoMo dataset not found at {path}. Run download_data.py first."
        )
    return json.loads(path.read_text(encoding="utf-8"))


def extract_sessions(conversation: dict) -> list[tuple[str, list[dict]]]:
    """Extract ordered sessions from a LoCoMo conversation."""
    sessions = []
    i = 1
    while f"session_{i}" in conversation:
        dt = conversation.get(f"session_{i}_date_time", "")
        turns = conversation[f"session_{i}"]
        sessions.append((dt, turns))
        i += 1
    return sessions


def build_import_sessions(conversation: dict) -> list[dict[str, object]]:
    speaker_a = conversation.get("speaker_a", "User")
    speaker_b = conversation.get("speaker_b", "Assistant")
    sessions: list[dict[str, object]] = []

    for dt, turns in extract_sessions(conversation):
        import_turns: list[dict[str, str]] = []
        for turn in turns:
            speaker = turn.get("speaker")
            text = str(turn.get("text", "")).strip()
            if not text:
                continue
            if speaker == speaker_a:
                role = "user"
            elif speaker == speaker_b:
                role = "assistant"
            else:
                continue
            import_turns.append({"role": role, "content": text})
        if import_turns:
            sessions.append({"date": str(dt), "turns": import_turns})

    return sessions


async def run_conversation(
    client: HttpAnimaClient,
    conv_data: dict,
    categories: set[int],
    conv_index: int,
    *,
    import_mode: str = "raw_chunks",
    embed_import_chunks: bool = False,
) -> list[dict]:
    """Ingest one LoCoMo conversation and ask all selected questions."""

    conv = conv_data["conversation"]
    qa_list = conv_data["qa"]
    speaker_a = conv.get("speaker_a", "User")
    speaker_b = conv.get("speaker_b", "Assistant")

    sessions = extract_sessions(conv)
    print(f"\n  Conversation {conv_index}: {len(sessions)} sessions, {len(qa_list)} questions")
    print(f"  Speakers: {speaker_a} / {speaker_b}")

    print("  Resetting eval memory...")
    await client.reset_memory()

    print(f"  Importing {len(sessions)} original transcript sessions ({import_mode})...")
    import_result = await client.import_transcript_sessions(
        build_import_sessions(conv),
        extraction_mode=import_mode,
        embed_raw_chunks=embed_import_chunks,
    )
    print(
        "    ... imported "
        f"{import_result.get('memoryItemsImported', 0)} raw chunks / "
        f"{import_result.get('turnPairsImported', 0)} memory pairs from "
        f"{import_result.get('messagesImported', 0)} transcript messages"
    )
    await asyncio.sleep(0.2)

    results = []
    filtered_qa = [q for q in qa_list if q.get("category") in categories]
    print(f"  Asking {len(filtered_qa)} questions (categories {sorted(categories)})...")

    for qi, qa in enumerate(filtered_qa):
        question = qa["question"]
        expected = str(qa["answer"])
        category = qa.get("category", 0)
        evidence = qa.get("evidence", [])

        response_data: dict[str, object] = {}
        try:
            response_data = await client.send_message_data(question)
            response = str(response_data.get("response") or response_data.get("message") or "")
        except Exception as exc:
            response = f"ERROR: {exc}"

        results.append(
            {
                "conversation_id": conv_data.get("sample_id", conv_index),
                "question": question,
                "expected_answer": expected,
                "ai_response": response,
                "category": category,
                "category_name": CATEGORY_NAMES.get(category, "unknown"),
                "evidence": evidence,
                "model": response_data.get("model"),
                "provider": response_data.get("provider"),
                "tools_used": response_data.get("toolsUsed", []),
                "retrieval": response_data.get("retrieval"),
                "import_mode": import_mode,
                "embed_import_chunks": embed_import_chunks,
                "evaluated": False,
            }
        )

        if (qi + 1) % 20 == 0:
            print(f"    ... asked {qi + 1}/{len(filtered_qa)} questions")

    return results


async def main() -> None:
    parser = argparse.ArgumentParser(description="Run AnimaOS against LoCoMo benchmark")
    parser.add_argument("--base-url", default=DEFAULT_HTTP_BASE_URL, help="AnimaOS server URL")
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
        help="Endpoint used to isolate each benchmark case.",
    )
    parser.add_argument("--limit", type=int, default=None, help="Limit to first N conversations")
    parser.add_argument(
        "--categories",
        default="1,2,3",
        help="Comma-separated category IDs to evaluate (default: 1,2,3)",
    )
    parser.add_argument(
        "--config",
        choices=["full", "memory_only", "memory_reflection", "baseline"],
        default="full",
        help="Ablation config label for the output file.",
    )
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
    parser.add_argument("--output", default=None, help="Output file path")
    args = parser.parse_args()

    categories = {int(c) for c in args.categories.split(",")}
    dataset = load_locomo()
    if args.limit:
        dataset = dataset[: args.limit]

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    output_path = Path(args.output) if args.output else RESULTS_DIR / f"locomo_{args.config}.json"

    client = HttpAnimaClient(
        args.base_url,
        username=args.username,
        password=args.password,
        create_user=args.create_user,
        reset_endpoint=args.reset_endpoint,
    )

    print(f"LoCoMo Benchmark - config={args.config}, categories={sorted(categories)}")
    print(f"Conversations: {len(dataset)}")
    print(f"Server: {args.base_url}")
    print(f"Import mode: {args.import_mode}")
    print(f"Embed import chunks: {args.embed_import_chunks}")

    all_results: list[dict] = []
    start = time.time()

    try:
        for i, conv_data in enumerate(dataset):
            results = await run_conversation(
                client,
                conv_data,
                categories,
                i,
                import_mode=args.import_mode,
                embed_import_chunks=args.embed_import_chunks,
            )
            all_results.extend(results)
            _save(output_path, args, all_results, categories)
    finally:
        await client.close()

    elapsed = time.time() - start
    summary = _compute_summary(all_results)
    print(f"\n{'=' * 60}")
    print(f"LoCoMo Results - {args.config}")
    print(f"{'=' * 60}")
    print(f"Total questions: {summary['total']}")
    print(f"Time: {elapsed:.0f}s")
    print(f"\nResults saved to: {output_path}")
    print(f"\nNext step: python score_results.py {output_path}")


def _save(
    path: Path,
    args: argparse.Namespace,
    results: list[dict],
    categories: set[int],
) -> None:
    output = {
        "benchmark": "locomo",
        "config": args.config,
        "categories": sorted(categories),
        "timestamp": datetime.now().isoformat(),
        "results": results,
        "summary": _compute_summary(results),
    }
    path.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")


def _compute_summary(results: list[dict]) -> dict:
    """Compute summary stats from results before scoring."""
    by_category: dict[str, int] = {}
    for result in results:
        name = result.get("category_name", "unknown")
        by_category[name] = by_category.get(name, 0) + 1
    return {"total": len(results), "by_category": by_category}


if __name__ == "__main__":
    asyncio.run(main())
