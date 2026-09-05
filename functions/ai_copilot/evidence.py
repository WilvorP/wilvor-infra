import re
from typing import Any


_SEGMENT_RE = re.compile(r"[^A-Za-z0-9_-]+")


def _segment(value: Any) -> str:
    text = _SEGMENT_RE.sub("-", str(value)).strip("-")
    return text or "unknown"


class EvidenceCatalog:
    def __init__(self) -> None:
        self._items: dict[str, dict[str, Any]] = {}

    def add(
        self,
        *,
        source_type: str,
        source_id: str,
        field: str,
        value: Any,
        observed_at: str | None = None,
        label: str | None = None,
    ) -> str | None:
        if value is None:
            return None
        evidence_id = (
            f"{_segment(source_type).lower()}."
            f"{_segment(source_id)}."
            f"{_segment(field).lower()}"
        )
        item = {
            "evidenceId": evidence_id,
            "sourceType": source_type,
            "sourceId": str(source_id),
            "field": field,
            "value": value,
        }
        if observed_at:
            item["observedAt"] = observed_at
        item["label"] = label or (
            f"{source_type} {source_id}: {field}"
        )
        self._items[evidence_id] = item
        return evidence_id

    def add_tree(
        self,
        *,
        source_type: str,
        source_id: str,
        value: Any,
        field: str = "",
        observed_at: str | None = None,
    ) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                child_field = (
                    f"{field}.{key}" if field else str(key)
                )
                self.add_tree(
                    source_type=source_type,
                    source_id=source_id,
                    value=child,
                    field=child_field,
                    observed_at=observed_at,
                )
            return
        if isinstance(value, list):
            for index, child in enumerate(value):
                child_field = (
                    f"{field}.{index}"
                    if field
                    else str(index)
                )
                self.add_tree(
                    source_type=source_type,
                    source_id=source_id,
                    value=child,
                    field=child_field,
                    observed_at=observed_at,
                )
            return
        if field:
            self.add(
                source_type=source_type,
                source_id=source_id,
                field=field,
                value=value,
                observed_at=observed_at,
            )

    def items(
        self,
        maximum: int = 500,
    ) -> list[dict[str, Any]]:
        return [
            self._items[key]
            for key in sorted(self._items)[:maximum]
        ]

    def validate_references(
        self,
        references: Any,
    ) -> list[dict[str, str]]:
        if not isinstance(references, list):
            return []
        result = []
        seen = set()
        for reference in references:
            if not isinstance(reference, dict):
                continue
            evidence_id = reference.get("evidenceId")
            if (
                not isinstance(evidence_id, str)
                or evidence_id in seen
                or evidence_id not in self._items
            ):
                continue
            seen.add(evidence_id)
            result.append(
                {
                    "evidenceId": evidence_id,
                    "label": self._items[evidence_id][
                        "label"
                    ],
                }
            )
        return result


def validate_evidence_references(
    references: Any,
    catalog_items: list[dict[str, Any]],
) -> list[dict[str, str]]:
    known = {
        item.get("evidenceId"): item
        for item in catalog_items
        if isinstance(item, dict)
        and isinstance(item.get("evidenceId"), str)
    }
    result = []
    seen = set()
    if not isinstance(references, list):
        return result
    for reference in references:
        if not isinstance(reference, dict):
            continue
        evidence_id = reference.get("evidenceId")
        if evidence_id in known and evidence_id not in seen:
            seen.add(evidence_id)
            result.append(
                {
                    "evidenceId": evidence_id,
                    "label": str(
                        known[evidence_id].get("label")
                        or evidence_id
                    ),
                }
            )
    return result
