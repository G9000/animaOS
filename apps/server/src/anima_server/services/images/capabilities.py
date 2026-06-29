from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ImageProcessingCapabilities:
    vision_caption: bool = False
    image_text_extraction: bool = False
