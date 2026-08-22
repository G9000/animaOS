from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from anima_server.models.runtime import RuntimeImageAsset

if TYPE_CHECKING:
    from anima_server.services.corefs.asset_authority import CoreFsByteSource

type ImageInput = Path | CoreFsByteSource
ImageTextExtractor = Callable[[ImageInput, RuntimeImageAsset], str | None]
ImageCaptioner = Callable[[ImageInput, RuntimeImageAsset], str | None]
