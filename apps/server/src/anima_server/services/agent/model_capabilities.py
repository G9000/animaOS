from __future__ import annotations

_VISION_MODEL_PATTERNS = (
    "gpt-4o",
    "gpt-4.1",
    "gpt-5",
    "vision",
    "llava",
    "qwen-vl",
    "qwen2.5-vl",
    "qwen2.5vl",
    "llama3.2-vision",
    "claude-3",
    "claude-sonnet-4",
    "claude-opus-4",
)


def supports_image_input(provider: str, model: str) -> bool:
    normalized_provider = provider.strip().lower()
    normalized_model = model.strip().lower()
    if not normalized_provider or not normalized_model:
        return False
    if normalized_provider == "scaffold":
        return False
    return any(pattern in normalized_model for pattern in _VISION_MODEL_PATTERNS)
