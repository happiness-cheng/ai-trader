"""复用现有 RAG 存储的记忆确认、隔离、过期与冲突策略。"""

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import Protocol, cast


class MemoryDenied(PermissionError):
    pass


class MemoryValidationError(ValueError):
    pass


class MemoryConflictError(RuntimeError):
    pass


class MemoryStoreError(RuntimeError):
    pass


@dataclass(frozen=True)
class ConfirmedMemoryEvent:
    event_id: str
    user_id: str
    source_run: str
    confirmed_at: datetime
    outcome: str
    content: str
    conflict_key: str
    confirmed: bool
    kind: str = "confirmed_outcome"
    origin: str = "business_confirmation"


class MemoryStore(Protocol):
    def index_confirmed_memory(
        self, record_id: str, content: str, metadata: dict[str, object],
    ) -> None: ...

    def get_memory_records(
        self, *, ids: list[str] | None = None, where: dict[str, object] | None = None,
    ) -> list[dict[str, object]]: ...

    def search(
        self, query: str, top_k: int = 3, collections: list[str] | None = None,
        *, where: dict[str, object] | None = None, raise_on_error: bool = False,
    ) -> list[dict[str, object]]: ...


class ControlledMemory:
    def __init__(
        self, store: MemoryStore, *, user_id: str, permissions: frozenset[str],
        clock: Callable[[], datetime] = lambda: datetime.now(UTC), ttl_seconds: int = 2592000,
    ) -> None:
        if not isinstance(user_id, str) or not user_id.strip() or len(user_id) > 128:
            raise MemoryValidationError("server-owned memory identity is required")
        if type(ttl_seconds) is not int or ttl_seconds <= 0:
            raise MemoryValidationError("memory TTL must be positive seconds")
        self._store = store
        self._user_id = user_id
        self._permissions = permissions
        self._clock = clock
        self._ttl_seconds = ttl_seconds

    def record_confirmed(self, event: ConfirmedMemoryEvent) -> dict[str, object]:
        self._require("memory:write")
        if event.user_id != self._user_id:
            raise MemoryDenied("confirmation belongs to a different user")
        now = self._now()
        confirmed_at = self._aware(event.confirmed_at)
        if (event.confirmed is not True or event.origin != "business_confirmation"
            or not self._allowed(event.kind, event.outcome)):
            raise MemoryValidationError("a confirmed business outcome is required")
        if confirmed_at > now:
            raise MemoryValidationError("confirmation cannot be in the future")
        for value in (event.event_id, event.source_run, event.conflict_key):
            if not isinstance(value, str) or not value.strip() or len(value) > 256:
                raise MemoryValidationError("confirmation provenance is required")
        if not isinstance(event.content, str) or not event.content.strip() or len(event.content) > 4096:
            raise MemoryValidationError("confirmed content must be bounded and nonempty")
        payload = json.dumps({
            "event_id": event.event_id, "user_id": self._user_id, "source_run": event.source_run,
            "confirmed_at": confirmed_at.isoformat(), "outcome": event.outcome,
            "content": event.content, "kind": event.kind, "conflict_key": event.conflict_key,
            "origin": event.origin,
        }, ensure_ascii=False, sort_keys=True)
        fingerprint = sha256(payload.encode()).hexdigest()
        record_id = "controlled_" + sha256(json.dumps(
            [self._user_id, event.event_id], ensure_ascii=False).encode()).hexdigest()
        try:
            existing = self._store.get_memory_records(ids=[record_id])
            if existing:
                metadata = cast(dict[str, object], existing[0].get("metadata", {}))
                if metadata.get("user_id") != self._user_id or metadata.get("fingerprint") != fingerprint:
                    raise MemoryConflictError("same confirmation ID has different content")
                return {"record_id": record_id, "status": "duplicate"}
            metadata = {
                "controlled": True, "verified": True, "user_id": self._user_id,
                "event_id": event.event_id, "source_run": event.source_run,
                "created_at": now.timestamp(), "confirmed_at": confirmed_at.timestamp(),
                "confirmed_at_iso": confirmed_at.isoformat(),
                "expires_at": confirmed_at.timestamp() + self._ttl_seconds,
                "ttl_seconds": self._ttl_seconds, "kind": event.kind,
                "conflict_key": event.conflict_key, "outcome": event.outcome,
                "origin": "business_confirmation", "fingerprint": fingerprint,
            }
            self._store.index_confirmed_memory(record_id, event.content, metadata)
        except MemoryConflictError:
            raise
        except Exception as exc:
            raise MemoryStoreError("memory write failed") from exc
        return {"record_id": record_id, "status": "written"}

    def search(self, query: str, top_k: int = 3) -> list[dict[str, object]]:
        self._require("memory:read")
        if not isinstance(query, str) or not query.strip() or len(query) > 500:
            raise MemoryValidationError("memory query must be 1..500 characters")
        if type(top_k) is not int or not 1 <= top_k <= 10:
            raise MemoryValidationError("memory top_k must be 1..10")
        now = self._now().timestamp()
        where: dict[str, object] = {"$and": [
            {"controlled": True}, {"user_id": self._user_id}, {"verified": True},
            {"expires_at": {"$gt": now}}, {"confirmed_at": {"$lte": now}},
            {"origin": "business_confirmation"},
        ]}
        try:
            candidates = self._store.search(query, top_k=top_k, collections=["lessons"],
                                             where=where, raise_on_error=True)
            output: list[dict[str, object]] = []
            visited: set[tuple[str, str]] = set()
            for candidate in candidates:
                if not self._valid(candidate, now):
                    continue
                metadata = cast(dict[str, object], candidate["metadata"])
                group = (str(metadata["kind"]), str(metadata["conflict_key"]))
                if group in visited:
                    continue
                visited.add(group)
                # 不能只在语义 top-k 内选最新：旧命中可能掩盖低相似度的新确认值。
                matching = self._store.get_memory_records(where={"$and": [
                    where, {"kind": group[0]}, {"conflict_key": group[1]},
                ]})
                rows = [row for row in matching if self._valid(row, now)]
                if not rows:
                    continue
                latest = max(float(cast(float, cast(dict[str, object], row["metadata"])["confirmed_at"]))
                             for row in rows)
                current = [row for row in rows if cast(dict[str, object], row["metadata"])["confirmed_at"] == latest]
                values = {(str(cast(dict[str, object], row["metadata"])["outcome"]),
                           str(row["document"])) for row in current}
                sources = [{"record_id": row["id"], "document": row["document"], **{
                    key: cast(dict[str, object], row["metadata"])[key]
                    for key in ("event_id", "source_run", "confirmed_at_iso", "outcome", "kind")
                }} for row in sorted(rows, key=lambda row: str(row["id"]))]
                status = "conflict" if len(values) > 1 else (
                    "historical" if group[0] in {"historical_advice", "preference"} else "current_confirmed")
                selected = min(current, key=lambda row: str(row["id"]))
                output.append({
                    "collection": "lessons", "id": selected["id"],
                    "document": "Conflicting confirmed records; inspect sources." if status == "conflict" else selected["document"],
                    "metadata": selected["metadata"], "status": status, "sources": sources,
                    "authority": "historical_context_only", "can_override_risk": False,
                })
            return output[:top_k]
        except Exception as exc:
            raise MemoryStoreError("memory retrieval failed") from exc

    def _require(self, permission: str) -> None:
        if permission not in self._permissions:
            raise MemoryDenied("memory permission denied")

    def _now(self) -> datetime:
        return self._aware(self._clock())

    @staticmethod
    def _aware(value: datetime) -> datetime:
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise MemoryValidationError("confirmation and clock require timezone-aware times")
        return value.astimezone(UTC)

    @staticmethod
    def _allowed(kind: str, outcome: str) -> bool:
        allowed = {
            "confirmed_outcome": {"filled", "paper_filled", "reconciled_filled", "cancelled", "rejected"},
            "historical_advice": {"reviewed"}, "lesson": {"reviewed"},
            "preference": {"user_confirmed", "reviewed"},
        }
        return isinstance(kind, str) and isinstance(outcome, str) and kind in allowed and outcome in allowed[kind]

    @property
    def user_id(self) -> str:
        """只读服务端绑定身份；不接受工具输入覆盖。"""
        return self._user_id

    def _valid(self, row: dict[str, object], now: float) -> bool:
        metadata = row.get("metadata")
        if not isinstance(metadata, dict):
            return False
        return (
            str(row.get("id", "")).startswith("controlled_")
            and isinstance(row.get("document"), str) and bool(str(row["document"]).strip())
            and metadata.get("controlled") is True and metadata.get("verified") is True
            and metadata.get("user_id") == self._user_id
            and metadata.get("origin") == "business_confirmation"
            and type(metadata.get("expires_at")) in {int, float}
            and metadata["expires_at"] > now
            and type(metadata.get("confirmed_at")) in {int, float}
            and metadata["confirmed_at"] <= now
            and self._allowed(str(metadata.get("kind")), str(metadata.get("outcome")))
            and all(isinstance(metadata.get(key), str) and bool(metadata[key]) for key in (
                "event_id", "source_run", "conflict_key", "confirmed_at_iso", "fingerprint",
            ))
        )
