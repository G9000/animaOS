from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from anima_server.models.runtime import RuntimeImageAsset


@dataclass(frozen=True, slots=True)
class StoredImageAsset:
    asset: RuntimeImageAsset
    path: Path
    created: bool
