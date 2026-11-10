"""缓存层：优先 Redis，连不上时自动降级为进程内 TTL 字典。

降级不会报错，只会在健康检查里体现 ``degraded=True``，保证本地无 Redis 也能跑。
"""
from __future__ import annotations

import json
import threading
import time
from typing import Any

from app.core.config import settings
from app.core.logging import get_logger

log = get_logger(__name__)


class _MemoryStore:
    """极简进程内缓存：dict + 过期时间戳，线程安全。"""

    def __init__(self) -> None:
        self._data: dict[str, tuple[float | None, str]] = {}
        self._lock = threading.Lock()

    def set(self, key: str, value: str, ttl: int | None) -> None:
        expire = (time.time() + ttl) if ttl else None
        with self._lock:
            self._data[key] = (expire, value)

    def get(self, key: str) -> str | None:
        with self._lock:
            item = self._data.get(key)
            if not item:
                return None
            expire, value = item
            if expire and expire < time.time():
                self._data.pop(key, None)
                return None
            return value

    def delete(self, key: str) -> None:
        with self._lock:
            self._data.pop(key, None)

    def keys(self, prefix: str = "") -> list[str]:
        return [k for k in self._data if k.startswith(prefix)]


class CacheService:
    """统一缓存门面：JSON 读写 + 前缀清理。"""

    def __init__(self) -> None:
        self._mem = _MemoryStore()
        self._redis = None
        self.backend = "memory"
        if settings.redis_enabled:
            try:
                import redis  # 延迟导入，避免未安装时启动失败

                client = redis.Redis.from_url(
                    settings.redis_url, decode_responses=True,
                    socket_connect_timeout=2, socket_timeout=2,
                )
                client.ping()
                self._redis = client
                self.backend = "redis"
            except Exception as exc:  # noqa: BLE001 - 任何异常都降级
                log.warning("Redis 不可用，降级为内存缓存：%s", exc)

    # ---------- 基础 ----------
    def set_json(self, key: str, value: Any, ttl: int | None = None) -> None:
        payload = json.dumps(value, ensure_ascii=False, default=str)
        if self._redis is not None:
            try:
                if ttl:
                    self._redis.setex(key, ttl, payload)
                else:
                    self._redis.set(key, payload)
                return
            except Exception as exc:  # noqa: BLE001
                log.warning("Redis 写入失败，回落内存：%s", exc)
        self._mem.set(key, payload, ttl)

    def get_json(self, key: str) -> Any | None:
        payload = None
        if self._redis is not None:
            try:
                payload = self._redis.get(key)
            except Exception:  # noqa: BLE001
                payload = None
        if payload is None:
            payload = self._mem.get(key)
        if payload is None:
            return None
        try:
            return json.loads(payload)
        except json.JSONDecodeError:
            return payload

    def delete(self, key: str) -> None:
        if self._redis is not None:
            try:
                self._redis.delete(key)
            except Exception:  # noqa: BLE001
                pass
        self._mem.delete(key)

    def keys(self, prefix: str) -> list[str]:
        if self._redis is not None:
            try:
                return list(self._redis.scan_iter(match=f"{prefix}*"))
            except Exception:  # noqa: BLE001
                pass
        return self._mem.keys(prefix)

    def health(self) -> dict[str, Any]:
        return {"backend": self.backend, "degraded": self.backend != "redis"}


cache = CacheService()
