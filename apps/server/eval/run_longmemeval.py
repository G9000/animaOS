"""
Run AnimaOS against the LongMemEval benchmark (ICLR 2025).

LongMemEval has 500 questions across 5 abilities:
  - single-session-user: recall user-stated facts
  - single-session-assistant: recall assistant-stated facts
  - single-session-preference: personalization based on preferences
  - temporal-reasoning: when things happened
  - knowledge-update: tracking changed facts
  - multi-session: connecting facts across sessions
  - abstention: correctly saying "I don't know"

Usage:
  python run_longmemeval.py --base-url http://localhost:8000

  # Use oracle set (evidence sessions only — faster, isolates memory quality)
  python run_longmemeval.py --dataset oracle

  # Limit to first N questions
  python run_longmemeval.py --limit 50
"""

import argparse
import asyncio
import contextlib
import json
import time
from datetime import datetime
from pathlib import Path

import httpx

DATA_DIR = Path(__file__).parent / "data"
RESULTS_DIR = Path(__file__).parent / "results"


class AnimaClient:
    """Client that talks to AnimaOS via its HTTP API."""

    def __init__(self, base_url: str, timeout: float = 120.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._client = httpx.AsyncClient(timeout=timeout)
        self._unlock_token: str | None = None

    async def ensure_unlocked(self) -> None:
        if self._unlock_token:
            return
        r = await self._client.post(
            f"{self.base_url}/api/auth/login",
            json={"username": "eval", "password": "eval"},
        )
        if r.status_code == 200:
            data = r.json()
            self._unlock_token = data.get("unlock_token") or data.get("token")
        else:
            self._unlock_token = "dev"

    def _headers(self) -> dict[str, str]:
        h: dict[str, str] = {"Content-Type": "application/json"}
        if self._unlock_token and self._unlock_token != "dev":
            h["x-anima-unlock"] = self._unlock_token
        return h

    async def send_message(self, text: str) -> str:
        await self.ensure_unlocked()
        r = await self._client.post(
            f"{self.base_url}/api/chat",
            json={"message": text},
            headers=self._headers(),
        )
        r.raise_for_status()
        data = r.json()
        return data.get("response", data.get("message", ""))

    async def reset_memory(self) -> None:
        await self.ensure_unlocked()
        try:
            await self._client.post(
                f"{self.base_url}/api/eval/reset",
                headers=self._headers(),
            )
        except httpx.HTTPStatusError:
            print("  Warning: /api/eval/reset not available")

    async def trigger_consolidation(self) -> None:
        await self.ensure_unlocked()
        with contextlib.suppress(httpx.HTTPStatusError):
            await self._client.post(
                f"{self.base_url}/api/chat/consolidate",
                headers=self._headers(),
            )

    async def close(self) -> None:
        await self._client.aclose()


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


async def run_question(
    client: AnimaClient,
    item: dict,
    q_index: int,
) -> dict:
    """Ingest history sessions, then ask the evaluation question."""

    question = item["question"]
    expected = item["answer"]
    q_type = item.get("question_type", "unknown")
    q_id = item.get("question_id", q_index)
    sessions = item.get("haystack_sessions", [])
    dates = item.get("haystack_dates", [])

    is_abstention = str(q_id).endswith("_abs")

    # Reset for this question
    await client.reset_memory()

    # Ingest history sessions as user-assistant conversations
    for sess_idx, session in enumerate(sessions):
        date_str = dates[sess_idx] if sess_idx < len(dates) else ""
        for turn_idx, turn in enumerate(session):
            role = turn.get("role", "user")
            content = turn.get("content", "")

            if role == "user":
                msg = content
                if date_str and turn_idx == 0:
                    msg = f"[{date_str}] {content}"
                try:
                    await client.send_message(msg)
                except Exception:
                    continue

    # Consolidate
    await client.trigger_consolidation()
    await asyncio.sleep(1)

    # Ask the question
    try:
        response = await client.send_message(question)
    except Exception as e:
        response = f"ERROR: {e}"

    return {
        "question_id": q_id,
        "question_type": q_type,
        "question": question,
        "expected_answer": str(expected),
        "ai_response": response,
        "is_abstention": is_abstention,
        "num_sessions": len(sessions),
        "evaluated": False,
    }


async def main() -> None:
    parser = argparse.ArgumentParser(description="Run AnimaOS against LongMemEval")
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--dataset", choices=["oracle", "small"], default="oracle")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--config", default="full")
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    dataset = load_longmemeval(args.dataset)
    if args.limit:
        dataset = dataset[: args.limit]

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    output_path = (
        Path(args.output)
        if args.output
        else RESULTS_DIR / f"longmemeval_{args.dataset}_{args.config}.json"
    )

    client = AnimaClient(args.base_url)

    print(f"LongMemEval Benchmark — variant={args.dataset}, config={args.config}")
    print(f"Questions: {len(dataset)}")
    print(f"Server: {args.base_url}")

    all_results: list[dict] = []
    start = time.time()

    try:
        for i, item in enumerate(dataset):
            result = await run_question(client, item, i)
            all_results.append(result)

            if (i + 1) % 10 == 0:
                print(f"  ... {i+1}/{len(dataset)} questions done")
                # Save incrementally
                _save(output_path, args, all_results)
    finally:
        await client.close()

    _save(output_path, args, all_results)

    elapsed = time.time() - start
    print(f"\n{'='*60}")
    print(f"LongMemEval Results — {args.dataset} / {args.config}")
    print(f"{'='*60}")
    print(f"Total questions: {len(all_results)}")
    print(f"Time: {elapsed:.0f}s")
    print(f"Results saved to: {output_path}")
    print(f"\nNext step: python score_results.py {output_path}")


def _save(path: Path, args: argparse.Namespace, results: list[dict]) -> None:
    by_type: dict[str, int] = {}
    for r in results:
        t = r.get("question_type", "unknown")
        by_type[t] = by_type.get(t, 0) + 1

    output = {
        "benchmark": "longmemeval",
        "variant": args.dataset,
        "config": args.config,
        "timestamp": datetime.now().isoformat(),
        "results": results,
        "summary": {"total": len(results), "by_type": by_type},
    }
    path.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
