from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from anima_server.models.runtime import RuntimeImageAsset

ImageTextExtractor = Callable[[Path, RuntimeImageAsset], str | None]
ImageCaptioner = Callable[[Path, RuntimeImageAsset], str | None]
