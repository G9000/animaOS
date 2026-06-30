from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class ImageRetentionUpdate(BaseModel):
    retentionState: Literal["transient", "retained", "durable"]
