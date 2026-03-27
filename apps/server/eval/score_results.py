"""
Score evaluation results using LLM-as-judge.

This follows LongMemEval's methodology: use an LLM to judge whether the
AI's response contains the correct answer. The judge prompt varies by
question type.

Uses Ollama by default (local, no API key needed). Can also use OpenRouter.

Usage:
  python score_results.py results/locomo_full.json
  python score_results.py results/longmemeval_oracle_full.json --model qwen3:32b
"""

import argparse
import json
import sys
import time
from pathlib import Path

import httpx

# --- Judge prompts (adapted from LongMemEval's evaluate_qa.py) ---

JUDGE_PROMPTS = {
    "default": (
        "I will give you a question, a correct answer, and a response from a model. "
        "Answer YES if the response contains the correct answer. Otherwise, answer NO. "
        "If the response is equivalent to the correct answer or contains all the "
        "intermediate steps to get the correct answer, also answer YES. "
        "If the response only contains a subset of the required information, answer NO.\n\n"
        "Question: {question}\n\n"
        "Correct Answer: {expected}\n\n"
        "Model Response: {response}\n\n"
        "Is the model response correct? Answer YES or NO only."
    ),
    "temporal_reasoning": (
        "I will give you a question, a correct answer, and a response from a model. "
        "Answer YES if the response contains the correct answer. Otherwise, answer NO. "
        "Do not penalize off-by-one errors for days/weeks/months. "
        "If the response is equivalent or contains the steps to get the answer, answer YES.\n\n"
        "Question: {question}\n\n"
        "Correct Answer: {expected}\n\n"
        "Model Response: {response}\n\n"
        "Is the model response correct? Answer YES or NO only."
    ),
    "knowledge_update": (
        "I will give you a question, a correct answer, and a response from a model. "
        "Answer YES if the response contains the correct answer. Otherwise, answer NO. "
        "If the response contains some previous information along with the updated answer, "
        "consider it correct as long as the updated answer matches.\n\n"
        "Question: {question}\n\n"
        "Correct Answer: {expected}\n\n"
        "Model Response: {response}\n\n"
        "Is the model response correct? Answer YES or NO only."
    ),
    "abstention": (
        "I will give you an unanswerable question, an explanation, and a response. "
        "Answer YES if the model correctly identifies the question as unanswerable "
        "(says info is incomplete, unknown, etc). Answer NO if it tries to answer.\n\n"
        "Question: {question}\n\n"
        "Explanation: {expected}\n\n"
        "Model Response: {response}\n\n"
        "Does the model correctly identify the question as unanswerable? "
        "Answer YES or NO only."
    ),
}


def get_prompt_key(result: dict) -> str:
    """Select the right judge prompt based on question type/category."""
    # LongMemEval question types
    q_type = result.get("question_type", "")
    if "temporal" in q_type:
        return "temporal_reasoning"
    if "knowledge-update" in q_type:
        return "knowledge_update"
    if result.get("is_abstention"):
        return "abstention"

    # LoCoMo categories
    cat_name = result.get("category_name", "")
    if cat_name == "temporal_reasoning":
        return "temporal_reasoning"

    return "default"


def judge_with_ollama(
    question: str,
    expected: str,
    response: str,
    prompt_key: str,
    model: str,
    base_url: str,
) -> dict:
    """Call Ollama to judge a single response."""
    template = JUDGE_PROMPTS[prompt_key]
    prompt = template.format(question=question, expected=expected, response=response)

    r = httpx.post(
        f"{base_url}/api/generate",
        json={
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.0, "num_predict": 10},
        },
        timeout=60,
    )
    r.raise_for_status()
    answer = r.json().get("response", "").strip().upper()

    is_correct = answer.startswith("YES")
    return {
        "is_correct": is_correct,
        "judge_raw": answer,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Score eval results with LLM judge")
    parser.add_argument("results_file", help="Path to results JSON file")
    parser.add_argument("--model", default="qwen3:8b", help="Ollama model for judging")
    parser.add_argument("--ollama-url", default="http://localhost:11434", help="Ollama API URL")
    parser.add_argument("--force", action="store_true", help="Re-score already-scored results")
    args = parser.parse_args()

    path = Path(args.results_file)
    if not path.exists():
        print(f"Error: {path} not found")
        sys.exit(1)

    data = json.loads(path.read_text(encoding="utf-8"))
    results = data["results"]

    # Check Ollama is running
    try:
        httpx.get(f"{args.ollama_url}/api/tags", timeout=5)
    except httpx.ConnectError:
        print(f"Error: Ollama not reachable at {args.ollama_url}")
        print("Start Ollama and ensure the model is pulled: ollama pull", args.model)
        sys.exit(1)

    to_score = [
        (i, r) for i, r in enumerate(results)
        if args.force or not r.get("evaluated")
    ]

    print(f"Scoring {len(to_score)} / {len(results)} results with {args.model}")
    start = time.time()

    correct = 0
    total = 0
    for idx, (i, r) in enumerate(to_score):
        prompt_key = get_prompt_key(r)
        response_text = r.get("ai_response", "")
        if response_text.startswith("ERROR:"):
            r["evaluated"] = True
            r["is_correct"] = False
            r["judge_raw"] = "SKIPPED (error response)"
            continue

        try:
            verdict = judge_with_ollama(
                question=r["question"],
                expected=r["expected_answer"],
                response=response_text,
                prompt_key=prompt_key,
                model=args.model,
                base_url=args.ollama_url,
            )
            r["evaluated"] = True
            r["is_correct"] = verdict["is_correct"]
            r["judge_raw"] = verdict["judge_raw"]
            if verdict["is_correct"]:
                correct += 1
            total += 1
        except Exception as e:
            print(f"  Error scoring question {i}: {e}")
            r["evaluated"] = True
            r["is_correct"] = False
            r["judge_raw"] = f"ERROR: {e}"
            total += 1

        if (idx + 1) % 25 == 0:
            print(f"  ... {idx+1}/{len(to_score)} scored ({correct}/{total} correct so far)")

        # Save periodically
        if (idx + 1) % 50 == 0:
            _save(path, data)

    _save(path, data)

    elapsed = time.time() - start
    scored = [r for r in results if r.get("evaluated")]
    correct_all = sum(1 for r in scored if r.get("is_correct"))

    print(f"\n{'='*60}")
    print("SCORING COMPLETE")
    print(f"{'='*60}")
    print(f"Total: {len(scored)}")
    print(f"Correct: {correct_all}")
    print(f"Accuracy: {correct_all/len(scored)*100:.1f}%" if scored else "N/A")
    print(f"Time: {elapsed:.0f}s")

    # Per-category breakdown
    categories: dict[str, list[bool]] = {}
    for r in scored:
        key = r.get("category_name") or r.get("question_type", "unknown")
        if key not in categories:
            categories[key] = []
        categories[key].append(bool(r.get("is_correct")))

    if categories:
        print("\nPer-category:")
        for cat, vals in sorted(categories.items()):
            acc = sum(vals) / len(vals) * 100 if vals else 0
            print(f"  {cat:30s}  {sum(vals):3d}/{len(vals):3d}  ({acc:.1f}%)")

    print(f"\nResults updated: {path}")


def _save(path: Path, data: dict) -> None:
    # Recompute summary with scores
    results = data["results"]
    scored = [r for r in results if r.get("evaluated")]
    correct = sum(1 for r in scored if r.get("is_correct"))

    data["summary"]["scored"] = len(scored)
    data["summary"]["correct"] = correct
    data["summary"]["accuracy"] = correct / len(scored) if scored else 0

    # Per-category accuracy
    by_cat: dict[str, dict] = {}
    for r in scored:
        key = r.get("category_name") or r.get("question_type", "unknown")
        if key not in by_cat:
            by_cat[key] = {"total": 0, "correct": 0}
        by_cat[key]["total"] += 1
        if r.get("is_correct"):
            by_cat[key]["correct"] += 1

    for v in by_cat.values():
        v["accuracy"] = v["correct"] / v["total"] if v["total"] else 0

    data["summary"]["by_category_scored"] = by_cat

    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
