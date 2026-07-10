from __future__ import annotations

import json
import re
import urllib.request
from typing import Any

_OLLAMA_DEFAULT_BASE_URL = "http://127.0.0.1:11434"
_OLLAMA_SHOW_TIMEOUT_SECONDS = 1.5

_VISION_MODEL_PATTERNS = (
    "gpt-4o",
    "gpt-4.1",
    "gpt-5",
    "llava",
    "qwen-vl",
    "qwen3-vl",
    "qwen3vl",
    "qwen2.5-vl",
    "qwen2.5vl",
    "llama3.2-vision",
)
_VISION_MODEL_NAMES = {
    "qwen/qwen3.6-35b-a3b-fp8",
    "moonshotai/kimi-k2.6",
}
_VISION_TOKEN_RE = re.compile(r"(^|[/:._-])vision($|[/:._-])")
_CLAUDE_GENERATION_RE = re.compile(
    r"(?:^|[/:._-])claude(?:[/:._-][a-z]+)*[/:._-](\d+)(?:$|[/:._-])"
)


def supports_image_input(provider: str, model: str, *, base_url: str = "") -> bool:
    normalized_provider = provider.strip().lower()
    normalized_model = model.strip().lower()
    if not normalized_provider or not normalized_model:
        return False
    if normalized_provider == "scaffold":
        return False
    if _model_name_matches_vision_pattern(normalized_model):
        return True
    if normalized_provider == "ollama":
        return _ollama_model_reports_vision(model.strip(), base_url=base_url)
    return False


def _model_name_matches_vision_pattern(model: str) -> bool:
    return (
        model in _VISION_MODEL_NAMES
        or any(pattern in model for pattern in _VISION_MODEL_PATTERNS)
        or _claude_model_supports_vision(model)
        or bool(_VISION_TOKEN_RE.search(model))
    )


def _claude_model_supports_vision(model: str) -> bool:
    """Accept Claude 3+ IDs regardless of whether generation follows the family."""
    match = _CLAUDE_GENERATION_RE.search(model)
    return match is not None and int(match.group(1)) >= 3


def _ollama_model_reports_vision(model: str, *, base_url: str) -> bool:
    if not model:
        return False

    request = urllib.request.Request(
        _ollama_show_url(base_url),
        data=json.dumps({"name": model}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(
            request,
            timeout=_OLLAMA_SHOW_TIMEOUT_SECONDS,
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, TimeoutError, UnicodeError, ValueError):
        return False

    return _payload_has_vision_capability(payload)


def _ollama_show_url(base_url: str) -> str:
    normalized_base_url = base_url.strip().rstrip("/") or _OLLAMA_DEFAULT_BASE_URL
    if normalized_base_url.endswith("/v1"):
        normalized_base_url = normalized_base_url[: -len("/v1")]
    return f"{normalized_base_url}/api/show"


def _payload_has_vision_capability(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False

    capabilities = payload.get("capabilities")
    if isinstance(capabilities, list):
        return any(
            isinstance(capability, str) and capability.strip().lower() == "vision"
            for capability in capabilities
        )

    model_info = payload.get("model_info")
    if isinstance(model_info, dict):
        return any(_VISION_TOKEN_RE.search(str(key).lower()) for key in model_info)

    return False
