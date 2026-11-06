from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


LEVELS = ("host", "pod", "service", "metric")


def entity_id(kind: str, key: str) -> str:
    return f"{kind}::{key}"


@dataclass(frozen=True)
class Entity:
    kind: str
    key: str

    @property
    def id(self) -> str:
        return entity_id(self.kind, self.key)

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, **asdict(self)}


@dataclass(frozen=True)
class Edge:
    source: str
    target: str
    weight: float
    relation: str
    provenance: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
