"""
Run AnimaOS against the LoCoMo benchmark.

LoCoMo has 10 conversations, each with ~19 sessions and ~150-200 questions.
Categories:
  1 = single-hop (factual recall)
  2 = temporal reasoning (when did X happen?)
  3 = multi-hop (requires connecting multiple facts)
  4 = open-ended (subjective/inferential)
  5 = adversarial (trick questions, things NOT said)

Usage:
  # Against a running AnimaOS server
  python run_locomo.py --base-url http://localhost:8000

  # With ablation config
  python run_locomo.py --config memory_only

  # Limit to first N conversations (for quick testing)
  python run_locomo.py --limit 2

  # Skip category 4 (open-ended) and 5 (adversarial) like most papers do
  python run_locomo.py --categories 1,2,3
"""

import argparse
import asyncio
import contextlib
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

DATA_DIR = Path(__file__).parent / "data"
RESULTS_DIR = Path(__file__).parent / "results"

CATEGORY_NAMES = {
    1: "single_hop",
    2: "temporal_reasoning",
    3: "multi_hop",
    4: "open_ended",
    5: "adversarial",
}


class AnimaClient:
    """Client that talks to AnimaOS via its HTTP API."""

    def __init__(self, base_url: str, timeout: float = 120.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._client = httpx.AsyncClient(timeout=timeout)
        self._unlock_token: str | None = None

    async def ensure_unlocked(self) -> None:
        """Login / unlock the session. Adjust credentials as needed."""
        if self._unlock_token:
            return
        # Try to login — adjust endpoint and creds to match your setup
        r = await self._client.post(
            f"{self.base_url}/api/auth/login",
            json={"username": "eval", "password": "eval"},
        )
        if r.status_code == 200:
            data = r.json()
            self._unlock_token = data.get("unlock_token") or data.get("token")
        else:
            # Fallback: no auth required (dev mode)
            self._unlock_token = "dev"

    def _headers(self) -> dict[str, str]:
        h: dict[str, str] = {"Content-Type": "application/json"}
        if self._unlock_token and self._unlock_token != "dev":
            h["x-anima-unlock"] = self._unlock_token
        return h

    async def send_message(self, text: str, thread_id: str | None = None) -> str:
        """Send a message and return the assistant's response text."""
        await self.ensure_unlocked()
        payload: dict[str, Any] = {"message": text}
        if thread_id:
            payload["thread_id"] = thread_id
        r = await self._client.post(
            f"{self.base_url}/api/chat",
            json=payload,
            headers=self._headers(),
        )
        r.raise_for_status()
        data = r.json()
        return data.get("response", data.get("message", ""))

    async def reset_memory(self) -> None:
        """Reset the agent's memory for a clean eval run."""
        await self.ensure_unlocked()
        # This endpoint may not exist yet — implement as needed
        try:
            await self._client.post(
                f"{self.base_url}/api/eval/reset",
                headers=self._headers(),
            )
        except httpx.HTTPStatusError:
            print("  Warning: /api/eval/reset not available. Memory not reset.")

    async def trigger_consolidation(self) -> None:
        """Trigger memory consolidation (extract facts from conversation)."""
        await self.ensure_unlocked()
        with contextlib.suppress(httpx.HTTPStatusError):
            await self._client.post(
                f"{self.base_url}/api/chat/consolidate",
                headers=self._headers(),
            )

    async def close(self) -> None:
        await self._client.aclose()


def load_locomo() -> list[dict]:
    path = DATA_DIR / "locomo_dataset.json"
    if not path.exists():
        raise FileNotFoundError(
            f"LoCoMo dataset not found at {path}. Run download_data.py first."
        )
    return json.loads(path.read_text(encoding="utf-8"))


def extract_sessions(conversation: dict) -> list[tuple[str, list[dict]]]:
    """Extract ordered sessions from a LoCoMo conversation.

    Returns list of (datetime_str, [{"speaker": ..., "text": ...}, ...]).
    """
    sessions = []
    i = 1
    while f"session_{i}" in conversation:
        dt = conversation.get(f"session_{i}_date_time", "")
        turns = conversation[f"session_{i}"]
        sessions.append((dt, turns))
        i += 1
    return sessions


async def run_conversation(
    client: AnimaClient,
    conv_data: dict,
    categories: set[int],
    conv_index: int,
) -> list[dict]:
    """Ingest one LoCoMo conversation and ask all questions."""

    conv = conv_data["conversation"]
    qa_list = conv_data["qa"]
    speaker_a = conv.get("speaker_a", "User")
    speaker_b = conv.get("speaker_b", "Assistant")

    sessions = extract_sessions(conv)
    print(f"\n  Conversation {conv_index}: {len(sessions)} sessions, {len(qa_list)} questions")
    print(f"  Speakers: {speaker_a} / {speaker_b}")

    # Reset memory for this conversation
    await client.reset_memory()

    # --- Phase 1: Ingest all conversation sessions ---
    print(f"  Ingesting {len(sessions)} sessions...")
    for sess_idx, (dt, turns) in enumerate(sessions):
        # We feed user turns as messages, treating speaker_a as the user
        for turn in turns:
            speaker = turn["speaker"]
            text = turn["text"]

            if speaker == speaker_a:
                # This is the "user" speaking — send as a user message
                # Prefix with date context so the AI has temporal grounding
                msg = text
                if (dt and sess_idx == 0) or (sess_idx > 0 and turns.index(turn) == 0):
                    msg = f"[{dt}] {text}"
                try:
                    await client.send_message(msg)
                except Exception as e:
                    print(f"    Error on session {sess_idx+1}: {e}")
                    continue

        if (sess_idx + 1) % 5 == 0:
            print(f"    ... ingested {sess_idx+1}/{len(sessions)} sessions")

    # --- Phase 2: Trigger consolidation ---
    print("  Triggering consolidation...")
    await client.trigger_consolidation()
    # Give consolidation a moment to process
    await asyncio.sleep(2)

    # --- Phase 3: Ask evaluation questions ---
    results = []
    filtered_qa = [q for q in qa_list if q.get("category") in categories]
    print(f"  Asking {len(filtered_qa)} questions (categories {sorted(categories)})...")

    for qi, qa in enumerate(filtered_qa):
        question = qa["question"]
        expected = str(qa["answer"])
        category = qa.get("category", 0)
        evidence = qa.get("evidence", [])

        try:
            response = await client.send_message(question)
        except Exception as e:
            response = f"ERROR: {e}"

        results.append({
            "conversation_id": conv_data.get("sample_id", conv_index),
            "question": question,
            "expected_answer": expected,
            "ai_response": response,
            "category": category,
            "category_name": CATEGORY_NAMES.get(category, "unknown"),
            "evidence": evidence,
            "evaluated": False,  # Will be scored by score_results.py
        })

        if (qi + 1) % 20 == 0:
            print(f"    ... asked {qi+1}/{len(filtered_qa)} questions")

    return results


async def main() -> None:
    parser = argparse.ArgumentParser(description="Run AnimaOS against LoCoMo benchmark")
    parser.add_argument("--base-url", default="http://localhost:8000", help="AnimaOS server URL")
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
        help="Ablation config",
    )
    parser.add_argument("--output", default=None, help="Output file path")
    args = parser.parse_args()

    categories = {int(c) for c in args.categories.split(",")}
    dataset = load_locomo()
    if args.limit:
        dataset = dataset[: args.limit]

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    output_path = Path(args.output) if args.output else RESULTS_DIR / f"locomo_{args.config}.json"

    client = AnimaClient(args.base_url)

    print(f"LoCoMo Benchmark — config={args.config}, categories={sorted(categories)}")
    print(f"Conversations: {len(dataset)}")
    print(f"Server: {args.base_url}")

    all_results: list[dict] = []
    start = time.time()

    try:
        for i, conv_data in enumerate(dataset):
            results = await run_conversation(client, conv_data, categories, i)
            all_results.extend(results)

            # Save incrementally
            output = {
                "benchmark": "locomo",
                "config": args.config,
                "categories": sorted(categories),
                "timestamp": datetime.now().isoformat(),
                "results": all_results,
                "summary": _compute_summary(all_results),
            }
            output_path.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
    finally:
        await client.close()

    elapsed = time.time() - start
    summary = _compute_summary(all_results)
    print(f"\n{'='*60}")
    print(f"LoCoMo Results — {args.config}")
    print(f"{'='*60}")
    print(f"Total questions: {summary['total']}")
    print(f"Time: {elapsed:.0f}s")
    print(f"\nResults saved to: {output_path}")
    print(f"\nNext step: python score_results.py {output_path}")


def _compute_summary(results: list[dict]) -> dict:
    """Compute summary stats from results (before scoring)."""
    by_category: dict[str, int] = {}
    for r in results:
        name = r.get("category_name", "unknown")
        by_category[name] = by_category.get(name, 0) + 1
    return {
        "total": len(results),
        "by_category": by_category,
    }


if __name__ == "__main__":
    asyncio.run(main())
