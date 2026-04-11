"""Shared keyed panel cache for lightweight Render-safe endpoints."""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional


class PanelCacheManager:
    """Single-flight snapshot cache for panel endpoints."""

    def __init__(self, store):
        self._store = store
        self._refresh_tasks: Dict[str, asyncio.Task] = {}
        self._task_lock = asyncio.Lock()

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _normalize_entry(raw: Optional[dict]) -> Optional[dict]:
        if not isinstance(raw, dict):
            return None
        if "payload" in raw:
            payload = raw.get("payload")
            return {
                "payload": payload if isinstance(payload, dict) else {},
                "as_of": raw.get("as_of"),
                "saved_at": float(raw.get("_saved_at") or 0.0),
            }
        return {
            "payload": raw,
            "as_of": raw.get("as_of"),
            "saved_at": float(raw.get("_saved_at") or 0.0),
        }

    async def peek(self, key: str) -> Optional[dict]:
        raw = await asyncio.to_thread(self._store.load_snapshot, key)
        return self._normalize_entry(raw)

    async def delete(self, key: str):
        await asyncio.to_thread(self._store.delete_snapshot, key)

    async def save_payload(self, key: str, payload: dict, as_of: Optional[str] = None) -> dict:
        entry = {
            "payload": payload,
            "as_of": as_of or self._now_iso(),
            "_saved_at": time.time(),
        }
        await asyncio.to_thread(self._store.save_snapshot, entry, key)
        return self._normalize_entry(entry) or {
            "payload": payload,
            "as_of": as_of,
            "saved_at": time.time(),
        }

    @staticmethod
    def _age(entry: Optional[dict]) -> float:
        if not entry:
            return float("inf")
        saved_at = float(entry.get("saved_at") or 0.0)
        if saved_at <= 0:
            return float("inf")
        return max(0.0, time.time() - saved_at)

    @staticmethod
    def _response(entry: Optional[dict], stale: bool, refreshing: bool, error: Optional[str] = None) -> dict:
        payload = dict((entry or {}).get("payload") or {})
        payload["as_of"] = (entry or {}).get("as_of")
        payload["stale"] = bool(stale)
        payload["refreshing"] = bool(refreshing)
        if error:
            payload["error"] = error
        return payload

    async def _ensure_task(
        self,
        key: str,
        builder: Callable[[], dict],
        existing: Optional[dict],
        is_empty: Optional[Callable[[dict], bool]],
    ) -> asyncio.Task:
        async with self._task_lock:
            task = self._refresh_tasks.get(key)
            if task and not task.done():
                return task

            async def runner():
                try:
                    payload = await asyncio.to_thread(builder)
                    if payload is None:
                        raise RuntimeError(f"panel builder returned no payload for {key}")
                    if existing and is_empty and is_empty(payload):
                        return existing
                    return await self.save_payload(key, payload)
                finally:
                    async with self._task_lock:
                        current = self._refresh_tasks.get(key)
                        if current is task:
                            self._refresh_tasks.pop(key, None)

            task = asyncio.create_task(runner())
            self._refresh_tasks[key] = task
            return task

    async def get_or_refresh(
        self,
        key: str,
        ttl: float,
        stale_ttl: float,
        builder: Callable[[], dict],
        *,
        fallback_payload: Optional[Callable[[], dict] | dict] = None,
        is_empty: Optional[Callable[[dict], bool]] = None,
        wait_on_miss: bool = True,
    ) -> dict:
        entry = await self.peek(key)
        age = self._age(entry)

        if entry and age <= ttl:
            return self._response(entry, stale=False, refreshing=False)

        if entry and age <= stale_ttl:
            task = await self._ensure_task(key, builder, entry, is_empty)
            return self._response(entry, stale=True, refreshing=bool(task))

        try:
            task = await self._ensure_task(key, builder, entry, is_empty)
            if not wait_on_miss and not entry:
                if callable(fallback_payload):
                    payload = fallback_payload()
                else:
                    payload = dict(fallback_payload or {})
                return {
                    **payload,
                    "as_of": None,
                    "stale": True,
                    "refreshing": True,
                }
            fresh_entry = await task
            fresh_age = self._age(fresh_entry)
            return self._response(
                fresh_entry,
                stale=bool(fresh_entry and fresh_age > ttl),
                refreshing=False,
            )
        except Exception as exc:
            if entry:
                return self._response(entry, stale=True, refreshing=False, error=str(exc))
            if callable(fallback_payload):
                payload = fallback_payload()
            else:
                payload = dict(fallback_payload or {})
            return {
                **payload,
                "as_of": None,
                "stale": True,
                "refreshing": False,
                "error": str(exc),
            }
