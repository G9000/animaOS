"""Download LoCoMo and LongMemEval datasets."""

import json
from pathlib import Path

import httpx

DATA_DIR = Path(__file__).parent / "data"

LOCOMO_URL = (
    "https://raw.githubusercontent.com/Backboard-io/Backboard-Locomo-Benchmark"
    "/main/locomo_dataset.json"
)

LONGMEMEVAL_BASE = (
    "https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned/resolve/main"
)
LONGMEMEVAL_FILES = [
    "longmemeval_oracle.json",   # oracle retrieval (evidence sessions only)
    "longmemeval_s_cleaned.json",  # ~115k tokens, ~40 sessions
]


def download_file(url: str, dest: Path) -> None:
    if dest.exists():
        print(f"  Already exists: {dest.name}")
        return
    print(f"  Downloading {dest.name} ...")
    r = httpx.get(url, timeout=60, follow_redirects=True)
    r.raise_for_status()
    dest.write_bytes(r.content)
    print(f"  Saved ({len(r.content) / 1024:.0f} KB)")


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    print("=== LoCoMo ===")
    download_file(LOCOMO_URL, DATA_DIR / "locomo_dataset.json")

    # Verify format
    data = json.loads((DATA_DIR / "locomo_dataset.json").read_text(encoding="utf-8"))
    total_qa = sum(len(item["qa"]) for item in data)
    print(f"  {len(data)} conversations, {total_qa} questions")

    print("\n=== LongMemEval ===")
    for fname in LONGMEMEVAL_FILES:
        download_file(f"{LONGMEMEVAL_BASE}/{fname}", DATA_DIR / fname)

    # Verify oracle format
    oracle_path = DATA_DIR / "longmemeval_oracle.json"
    if oracle_path.exists():
        oracle = json.loads(oracle_path.read_text(encoding="utf-8"))
        print(f"  Oracle: {len(oracle)} questions")

    print("\nDone. Data saved to:", DATA_DIR)


if __name__ == "__main__":
    main()
