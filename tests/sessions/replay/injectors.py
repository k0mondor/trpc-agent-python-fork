"""Out-of-band SQLite and Redis corruption helpers for end-to-end tests."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any


@dataclass(frozen=True)
class RawStorageTarget:
    app_name: str
    user_id: str
    session_id: str


def inject_sqlite_event_author(
    db_url: str,
    target: RawStorageTarget,
    *,
    event_id: str,
    value: str = "raw-sqlite-corruption",
) -> bool:
    """Mutate one persisted event without going through SessionService."""

    from sqlalchemy import create_engine, text

    engine = create_engine(db_url)
    try:
        with engine.begin() as connection:
            result = connection.execute(
                text(
                    "UPDATE events SET author = :value "
                    "WHERE app_name = :app_name AND user_id = :user_id "
                    "AND session_id = :session_id AND id = :event_id"
                ),
                {
                    "value": value,
                    "app_name": target.app_name,
                    "user_id": target.user_id,
                    "session_id": target.session_id,
                    "event_id": event_id,
                },
            )
            return bool(result.rowcount)
    finally:
        engine.dispose()


def inject_sqlite_session_state(
    db_url: str,
    target: RawStorageTarget,
    *,
    key: str,
    value: Any,
) -> bool:
    """Mutate the persisted session-state JSON using SDK-compatible encoding."""

    from sqlalchemy import create_engine, text

    engine = create_engine(db_url)
    try:
        with engine.begin() as connection:
            row = connection.execute(
                text(
                    "SELECT state FROM sessions WHERE app_name = :app_name "
                    "AND user_id = :user_id AND id = :session_id"
                ),
                {
                    "app_name": target.app_name,
                    "user_id": target.user_id,
                    "session_id": target.session_id,
                },
            ).fetchone()
            if row is None:
                return False
            state = json.loads(row[0]) if isinstance(row[0], str) else dict(row[0] or {})
            state[key] = value
            result = connection.execute(
                text(
                    "UPDATE sessions SET state = :state WHERE app_name = :app_name "
                    "AND user_id = :user_id AND id = :session_id"
                ),
                {
                    "state": json.dumps(state, ensure_ascii=False),
                    "app_name": target.app_name,
                    "user_id": target.user_id,
                    "session_id": target.session_id,
                },
            )
            return bool(result.rowcount)
    finally:
        engine.dispose()


def inject_redis_event_author(
    redis_url: str,
    target: RawStorageTarget,
    *,
    event_index: int = 0,
    value: str = "raw-redis-corruption",
) -> bool:
    """Mutate one event in the serialized Redis session value."""

    import redis

    client = redis.from_url(redis_url, decode_responses=True)
    try:
        key = f"session:{target.app_name}:{target.user_id}:{target.session_id}"
        raw = client.get(key)
        if not raw:
            return False
        payload = json.loads(raw)
        events = payload.get("events") or []
        if event_index >= len(events):
            return False
        events[event_index]["author"] = value
        client.set(key, json.dumps(payload, ensure_ascii=False))
        return True
    finally:
        client.close()


def inject_redis_session_state(
    redis_url: str,
    target: RawStorageTarget,
    *,
    key: str,
    value: Any,
) -> bool:
    """Mutate session-scoped state in the serialized Redis session value."""

    import redis

    client = redis.from_url(redis_url, decode_responses=True)
    try:
        redis_key = f"session:{target.app_name}:{target.user_id}:{target.session_id}"
        raw = client.get(redis_key)
        if not raw:
            return False
        payload = json.loads(raw)
        payload.setdefault("state", {})[key] = value
        client.set(redis_key, json.dumps(payload, ensure_ascii=False))
        return True
    finally:
        client.close()
