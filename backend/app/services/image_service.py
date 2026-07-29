"""图片搜索服务 - Pexels优先, 智能降级查询"""

import os
import requests
from typing import List, Optional


class ImageService:
    """图片搜索 - Pexels API, 短查询+英文降级"""

    def __init__(self):
        self.pexels_key = os.getenv("PEXELS_API_KEY", "")
        self.base_url = "https://api.pexels.com/v1"

    def search_photos(self, query: str, per_page: int = 3) -> List[dict]:
        # 策略1: 原始短查询
        photos = self._search_pexels(query, per_page)
        if photos:
            return photos

        # 策略2: 取前8个字符再搜
        if len(query) > 8:
            short = query[:8]
            photos = self._search_pexels(short, per_page)
            if photos:
                return photos

        # 策略3: 尝试英文通用词
        if "餐" in query or "食" in query or "馆" in query or "厅" in query:
            photos = self._search_pexels("food restaurant", per_page)
        else:
            photos = self._search_pexels("travel landmark", per_page)

        return photos if photos else []

    def _search_pexels(self, query: str, per_page: int) -> List[dict]:
        if not self.pexels_key:
            return []
        try:
            resp = requests.get(
                f"{self.base_url}/search",
                params={"query": query, "per_page": per_page},
                headers={"Authorization": self.pexels_key},
                timeout=8
            )
            if resp.status_code != 200:
                return []
            photos = []
            for p in resp.json().get("photos", []):
                photos.append({
                    "id": str(p.get("id")),
                    "url": p.get("src", {}).get("large"),
                    "thumb": p.get("src", {}).get("medium"),
                    "description": p.get("alt") or "",
                })
            return photos
        except Exception:
            return []

    def get_photo_url(self, query: str) -> Optional[str]:
        photos = self.search_photos(query, per_page=3)
        if not photos:
            return None
        for photo in photos:
            if photo.get("description"):
                return photo.get("url")
        return photos[0].get("url") if photos else None


_image_service: Optional[ImageService] = None


def get_image_service() -> ImageService:
    global _image_service
    if _image_service is None:
        _image_service = ImageService()
    return _image_service
