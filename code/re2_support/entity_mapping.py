"""Entity mapping utilities for converting metric ranks to service ranks.

RCAEval metric names follow ``<service>_<metric_type>`` for the RE1-OB data,
for example ``adservice_cpu`` and ``frontend-external_latency``.  The mapper
splits on the final underscore so entity names may contain hyphens or other
underscores.
"""

from dataclasses import asdict, dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple


@dataclass(frozen=True)
class EntityMapping:
    """Mapping from one metric candidate to its owning service entity."""

    metric: str
    service: str
    metric_type: str
    mapped: bool

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def normalize_service_name(service: str) -> str:
    """Normalize a service identifier for exact evaluation matching."""
    return service.strip().lower()


def map_metric_to_service(
    metric_name: str,
    aliases: Optional[Dict[str, str]] = None,
) -> EntityMapping:
    """Map ``<service>_<metric_type>`` to a service entity.

    Metrics without a separator remain visible as unmapped entities instead
    of being silently discarded.  Optional aliases can normalize names that
    differ between telemetry and topology metadata.
    """
    metric_name = str(metric_name).strip()
    if "_" in metric_name:
        service, metric_type = metric_name.rsplit("_", 1)
        mapped = bool(service and metric_type)
    else:
        service, metric_type, mapped = metric_name, "unknown", False

    alias_map = {
        normalize_service_name(source): target
        for source, target in (aliases or {}).items()
    }
    service = alias_map.get(normalize_service_name(service), service)

    return EntityMapping(
        metric=metric_name,
        service=service,
        metric_type=metric_type,
        mapped=mapped,
    )


def build_entity_mappings(
    metric_results: Iterable[Tuple[str, float]],
    aliases: Optional[Dict[str, str]] = None,
) -> List[Dict[str, Any]]:
    """Return serializable mappings for all ranked metric candidates."""
    return [
        map_metric_to_service(metric, aliases=aliases).to_dict()
        for metric, _ in metric_results
    ]


def metric_ranks_to_service_ranks(
    metric_results: Iterable[Tuple[str, float]],
    aliases: Optional[Dict[str, str]] = None,
) -> List[Tuple[str, float]]:
    """Aggregate metric candidates into unique service candidates.

    A service score is the maximum score among its metrics.  Sorting services
    by that score is equivalent to taking the first occurrence of each service
    from an already sorted metric ranking, which matches BARO's original
    service de-duplication behavior.
    """
    best_scores: Dict[str, Tuple[str, float]] = {}

    for metric, score in metric_results:
        mapping = map_metric_to_service(metric, aliases=aliases)
        key = normalize_service_name(mapping.service)
        numeric_score = float(score)
        current = best_scores.get(key)
        if current is None or numeric_score > current[1]:
            best_scores[key] = (mapping.service, numeric_score)

    return sorted(best_scores.values(), key=lambda item: item[1], reverse=True)

