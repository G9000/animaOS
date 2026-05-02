"""Run the recommended AnimaOS agent evaluation profiles."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from eval_client import (
    DEFAULT_EVAL_PASSWORD,
    DEFAULT_EVAL_USERNAME,
    DEFAULT_HTTP_BASE_URL,
)

EvalProfile = Literal["pr", "nightly", "release", "ablation"]

EVAL_DIR = Path(__file__).parent
RESULTS_DIR = EVAL_DIR / "results"
DEFAULT_JUDGE_MODEL = "qwen3:8b"
DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_ABLATION_CONFIGS = ("baseline", "memory_only", "memory_reflection", "full")
DEFAULT_PR_AGENT_PROVIDER = "ollama"
DEFAULT_PR_AGENT_MODEL = "vaultbox/qwen3.5-uncensored:35b"
DEFAULT_PR_AGENT_BASE_URL = "http://127.0.0.1:11434"


@dataclass(frozen=True, slots=True)
class EvalCommand:
    name: str
    argv: tuple[str, ...]
    requires_server: bool
    description: str

    def display(self) -> str:
        return subprocess.list2cmdline(list(self.argv))


def build_eval_plan(
    *,
    profile: EvalProfile | str,
    output_dir: Path = RESULTS_DIR,
    base_url: str = DEFAULT_HTTP_BASE_URL,
    username: str = DEFAULT_EVAL_USERNAME,
    password: str = DEFAULT_EVAL_PASSWORD,
    create_user: bool = False,
    reset_endpoint: str = "/api/eval/reset",
    score: bool = False,
    judge_model: str = DEFAULT_JUDGE_MODEL,
    ollama_url: str = DEFAULT_OLLAMA_URL,
    ablation_configs: tuple[str, ...] = DEFAULT_ABLATION_CONFIGS,
    agent_provider: str = DEFAULT_PR_AGENT_PROVIDER,
    agent_model: str = DEFAULT_PR_AGENT_MODEL,
    agent_base_url: str = DEFAULT_PR_AGENT_BASE_URL,
    agent_api_key: str = "",
    disable_background_memory: bool = True,
    python_executable: str | None = None,
) -> list[EvalCommand]:
    """Build the command plan for an eval profile without executing it."""

    python = python_executable or sys.executable
    output_dir = Path(output_dir)

    if profile == "pr":
        return [
            _conversation_smoke_command(
                python=python,
                output_path=output_dir / "conversation_eval_pr.json",
                agent_provider=agent_provider,
                agent_model=agent_model,
                agent_base_url=agent_base_url,
                agent_api_key=agent_api_key,
                disable_background_memory=disable_background_memory,
            )
        ]

    if profile == "nightly":
        return [
            _longmemeval_command(
                name="longmemeval-nightly",
                python=python,
                output_path=output_dir / "longmemeval_oracle_full_nightly.json",
                base_url=base_url,
                username=username,
                password=password,
                create_user=create_user,
                reset_endpoint=reset_endpoint,
                config="full",
                limit=50,
                description="Primary nightly long-term memory regression suite.",
            )
        ]

    if profile == "release":
        longmem_output = output_dir / "longmemeval_oracle_full_release.json"
        locomo_output = output_dir / "locomo_full_release.json"
        plan = [
            _longmemeval_command(
                name="longmemeval-release",
                python=python,
                output_path=longmem_output,
                base_url=base_url,
                username=username,
                password=password,
                create_user=create_user,
                reset_endpoint=reset_endpoint,
                config="full",
                limit=None,
                description="Full primary long-term memory benchmark.",
            )
        ]
        if score:
            plan.append(
                _score_command(
                    name="score-longmemeval-release",
                    python=python,
                    results_path=longmem_output,
                    judge_model=judge_model,
                    ollama_url=ollama_url,
                )
            )
        plan.append(
            _locomo_command(
                name="locomo-release",
                python=python,
                output_path=locomo_output,
                base_url=base_url,
                username=username,
                password=password,
                create_user=create_user,
                reset_endpoint=reset_endpoint,
                config="full",
                limit=None,
                categories="1,2,3,5",
                description="Heavy release stress test for temporal, multi-hop, and abstention memory.",
            )
        )
        if score:
            plan.append(
                _score_command(
                    name="score-locomo-release",
                    python=python,
                    results_path=locomo_output,
                    judge_model=judge_model,
                    ollama_url=ollama_url,
                )
            )
        return plan

    if profile == "ablation":
        return [
            _longmemeval_command(
                name=f"longmemeval-ablation-{config}",
                python=python,
                output_path=output_dir / f"longmemeval_oracle_{config}_ablation.json",
                base_url=base_url,
                username=username,
                password=password,
                create_user=create_user,
                reset_endpoint=reset_endpoint,
                config=config,
                limit=50,
                description=f"LongMemEval ablation slice for {config}.",
            )
            for config in ablation_configs
        ]

    raise ValueError(f"Unknown eval profile: {profile}")


def run_eval_plan(
    plan: list[EvalCommand],
    *,
    continue_on_failure: bool = False,
) -> int:
    """Run an eval command plan and return the highest failing exit code."""

    worst_exit_code = 0
    for command in plan:
        print(f"\n== {command.name} ==")
        print(command.description)
        print(command.display())
        result = subprocess.run(command.argv, check=False)
        if result.returncode != 0:
            worst_exit_code = result.returncode
            if not continue_on_failure:
                return result.returncode
    return worst_exit_code


def _conversation_smoke_command(
    *,
    python: str,
    output_path: Path,
    agent_provider: str,
    agent_model: str,
    agent_base_url: str,
    agent_api_key: str,
    disable_background_memory: bool,
) -> EvalCommand:
    argv = [
        python,
        str(EVAL_DIR / "run_conversation_eval.py"),
        "--mode",
        "in-process",
        "--output",
        str(output_path),
        "--agent-provider",
        agent_provider,
        "--agent-model",
        agent_model,
        "--agent-base-url",
        agent_base_url,
    ]
    if agent_api_key:
        argv.extend(("--agent-api-key", agent_api_key))
    if disable_background_memory:
        argv.append("--disable-background-memory")

    return EvalCommand(
        name="conversation-smoke",
        argv=tuple(argv),
        requires_server=False,
        description="Fast in-process behavioral smoke eval for PR gating.",
    )


def _longmemeval_command(
    *,
    name: str,
    python: str,
    output_path: Path,
    base_url: str,
    username: str,
    password: str,
    create_user: bool,
    reset_endpoint: str,
    config: str,
    limit: int | None,
    description: str,
) -> EvalCommand:
    argv = [
        python,
        str(EVAL_DIR / "run_longmemeval.py"),
        "--base-url",
        base_url,
        "--username",
        username,
        "--password",
        password,
        "--dataset",
        "oracle",
        "--config",
        config,
        "--reset-endpoint",
        reset_endpoint,
        "--output",
        str(output_path),
    ]
    if limit is not None:
        argv.extend(("--limit", str(limit)))
    if create_user:
        argv.append("--create-user")
    return EvalCommand(
        name=name,
        argv=tuple(argv),
        requires_server=True,
        description=description,
    )


def _locomo_command(
    *,
    name: str,
    python: str,
    output_path: Path,
    base_url: str,
    username: str,
    password: str,
    create_user: bool,
    reset_endpoint: str,
    config: str,
    limit: int | None,
    categories: str,
    description: str,
) -> EvalCommand:
    argv = [
        python,
        str(EVAL_DIR / "run_locomo.py"),
        "--base-url",
        base_url,
        "--username",
        username,
        "--password",
        password,
        "--categories",
        categories,
        "--config",
        config,
        "--reset-endpoint",
        reset_endpoint,
        "--output",
        str(output_path),
    ]
    if limit is not None:
        argv.extend(("--limit", str(limit)))
    if create_user:
        argv.append("--create-user")
    return EvalCommand(
        name=name,
        argv=tuple(argv),
        requires_server=True,
        description=description,
    )


def _score_command(
    *,
    name: str,
    python: str,
    results_path: Path,
    judge_model: str,
    ollama_url: str,
) -> EvalCommand:
    return EvalCommand(
        name=name,
        argv=(
            python,
            str(EVAL_DIR / "score_results.py"),
            str(results_path),
            "--model",
            judge_model,
            "--ollama-url",
            ollama_url,
        ),
        requires_server=False,
        description="LLM-as-judge scoring for benchmark answers.",
    )


def _plan_as_json(plan: list[EvalCommand]) -> str:
    return json.dumps(
        [
            {
                "name": command.name,
                "description": command.description,
                "requires_server": command.requires_server,
                "argv": list(command.argv),
                "command": command.display(),
            }
            for command in plan
        ],
        indent=2,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run AnimaOS eval profiles")
    parser.add_argument(
        "--profile",
        choices=("pr", "nightly", "release", "ablation"),
        default="pr",
        help="Eval profile to run. Defaults to the fast PR smoke profile.",
    )
    parser.add_argument("--base-url", default=DEFAULT_HTTP_BASE_URL)
    parser.add_argument("--username", default=DEFAULT_EVAL_USERNAME)
    parser.add_argument("--password", default=DEFAULT_EVAL_PASSWORD)
    parser.add_argument(
        "--create-user",
        action="store_true",
        help="Create the disposable eval user if login fails.",
    )
    parser.add_argument("--reset-endpoint", default="/api/eval/reset")
    parser.add_argument(
        "--score",
        action="store_true",
        help="Add score_results.py commands after benchmark runs.",
    )
    parser.add_argument("--judge-model", default=DEFAULT_JUDGE_MODEL)
    parser.add_argument("--ollama-url", default=DEFAULT_OLLAMA_URL)
    parser.add_argument("--output-dir", type=Path, default=RESULTS_DIR)
    parser.add_argument(
        "--agent-provider",
        default=DEFAULT_PR_AGENT_PROVIDER,
        help="Provider forced for the in-process PR smoke eval.",
    )
    parser.add_argument(
        "--agent-model",
        default=DEFAULT_PR_AGENT_MODEL,
        help="Model forced for the in-process PR smoke eval.",
    )
    parser.add_argument(
        "--agent-base-url",
        default=DEFAULT_PR_AGENT_BASE_URL,
        help="Base URL forced for local/OpenAI-compatible in-process PR smoke evals.",
    )
    parser.add_argument(
        "--agent-api-key",
        default="",
        help="Optional API key for the in-process PR smoke eval provider.",
    )
    parser.add_argument(
        "--enable-background-memory",
        action="store_true",
        help="Allow background memory/sleep tasks during in-process PR smoke evals.",
    )
    parser.add_argument(
        "--ablation-configs",
        default=",".join(DEFAULT_ABLATION_CONFIGS),
        help="Comma-separated config labels for the ablation profile.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the command plan as JSON without running it.",
    )
    parser.add_argument(
        "--continue-on-failure",
        action="store_true",
        help="Run remaining commands even if one eval command fails.",
    )
    args = parser.parse_args()

    plan = build_eval_plan(
        profile=args.profile,
        output_dir=args.output_dir,
        base_url=args.base_url,
        username=args.username,
        password=args.password,
        create_user=args.create_user,
        reset_endpoint=args.reset_endpoint,
        score=args.score,
        judge_model=args.judge_model,
        ollama_url=args.ollama_url,
        ablation_configs=tuple(
            item.strip() for item in args.ablation_configs.split(",") if item.strip()
        ),
        agent_provider=args.agent_provider,
        agent_model=args.agent_model,
        agent_base_url=args.agent_base_url,
        agent_api_key=args.agent_api_key,
        disable_background_memory=not args.enable_background_memory,
    )

    if args.dry_run:
        print(_plan_as_json(plan))
        return

    args.output_dir.mkdir(parents=True, exist_ok=True)
    raise SystemExit(
        run_eval_plan(plan, continue_on_failure=args.continue_on_failure)
    )


if __name__ == "__main__":
    main()
