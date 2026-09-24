"""Render motorunun okuduğu görsel tanımı."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable


@dataclass
class Visual:
    kind: str                 # image | video | card
    path: Path | None = None
    width: int = 0
    height: int = 0
    source: str = ""
    credit: str = ""
    is_ai: bool = False
    duration: float = 0.0
    params: dict = field(default_factory=dict)
    frame_fn: Callable | None = None

    @property
    def aspect(self) -> float:
        return self.width / max(1, self.height)
