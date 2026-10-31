"""Protocol manifest: which protocols exist and which centers run them.

Center participation lives here, not in the search index (see ADR 0002).
"""

import json
from functools import lru_cache
from pathlib import Path
from typing import Literal, get_args

from pydantic import BaseModel

Center = Literal["UZA", "UZL", "CHU", "UCL", "HUDERF", "UZG"]
CENTERS: tuple[Center, ...] = get_args(Center)

MANIFEST_PATH = Path(__file__).parent / "data" / "protocol_manifest.json"


class Protocol(BaseModel):
    protocol_id: str
    short_title: str
    title: str
    filename: str
    version: str | None = None
    version_date: str | None = None
    doc_type: str = "protocol"
    centers: list[Center]

    @property
    def label(self) -> str:
        """Human-readable name with version, e.g. 'FaR-RMS v2.0c'."""
        return f"{self.short_title} v{self.version}" if self.version else self.short_title


@lru_cache
def load_manifest(path: Path = MANIFEST_PATH) -> dict[str, Protocol]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {entry["protocol_id"]: Protocol.model_validate(entry) for entry in raw}


def is_center(value: str) -> bool:
    return value in CENTERS


def protocols_for_center(center: Center) -> list[Protocol]:
    """The Center Scope: protocols this center participates in."""
    return [p for p in load_manifest().values() if center in p.centers]
