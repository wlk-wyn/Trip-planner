"""简单内存 TTL 缓存服务

为搜索节点提供带过期时间的缓存，避免重复调用高德 API。
后续可平滑迁移到 Redis。
"""

import time
import threading
from typing import Any, Optional


class TTLCache:
    """线程安全的 TTL 内存缓存"""

    def __init__(self):
        self._store: dict[str, dict] = {}  # key -> {"data": Any, "expire": float}
        self._lock = threading.Lock()

    def get(self, key: str) -> Optional[Any]:
        """获取缓存值，过期则返回 None"""
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            if time.time() > entry["expire"]:
                del self._store[key]
                return None
            return entry["data"]

    def set(self, key: str, data: Any, ttl: int) -> None:
        """写入缓存，ttl 单位为秒"""
        with self._lock:
            self._store[key] = {
                "data": data,
                "expire": time.time() + ttl,
            }

    def clear(self) -> None:
        """清空所有缓存"""
        with self._lock:
            self._store.clear()

    def stats(self) -> dict:
        """返回缓存统计信息"""
        with self._lock:
            total = len(self._store)
            expired = sum(1 for v in self._store.values() if time.time() > v["expire"])
            return {"total": total, "active": total - expired, "expired": expired}


# 全局单例
cache = TTLCache()

# 缓存 TTL 常量（秒）
CACHE_TTL_WEATHER = 4 * 3600      # 天气: 4 小时
CACHE_TTL_ATTRACTION = 12 * 3600  # 景点: 12 小时
CACHE_TTL_HOTEL = 12 * 3600       # 酒店: 12 小时
