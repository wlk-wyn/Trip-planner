"""POI相关API路由"""

from fastapi import APIRouter, HTTPException, Depends, Query, Path
from pydantic import BaseModel, Field
from typing import List, Optional
from ...services.map_queries import find_places, find_detail
from ...services.amap_rest import AMapServiceError
from ..map_errors import map_http_error
from ...services.amap_rest import find_poi_photo
from ...services.request_gate import protect_request

router = APIRouter(prefix="/poi", tags=["POI"], dependencies=[Depends(protect_request)])


class POIDetailResponse(BaseModel):
    """POI详情响应"""
    success: bool
    message: str
    data: Optional[dict] = None


@router.get(
    "/detail/{poi_id}",
    response_model=POIDetailResponse,
    summary="获取POI详情",
    description="根据POI ID获取详细信息,包括图片"
)
async def get_poi_detail(poi_id: str = Path(...,min_length=1,max_length=80)):
    """
    获取POI详情
    
    Args:
        poi_id: POI ID
        
    Returns:
        POI详情响应
    """
    try:
        return POIDetailResponse(success=True,message="已查询地点详情",data=await find_detail(poi_id))
    except AMapServiceError as error:raise map_http_error(error) from None


@router.get(
    "/search",
    summary="搜索POI",
    description="根据关键词搜索POI"
)
async def search_poi(keywords: str = Query(...,min_length=1,max_length=120), city: str = Query("北京",min_length=1,max_length=80)):
    """
    搜索POI

    Args:
        keywords: 搜索关键词
        city: 城市名称

    Returns:
        搜索结果
    """
    try:
        result = await find_places(city,keywords)
        return {"success":True,"message":"已查询地点" if result else "未找到匹配地点","data":[place.model_dump() for place in result]}
    except AMapServiceError as error:raise map_http_error(error) from None


@router.get(
    "/photo",
    summary="获取景点图片",
    description="仅返回同名高德地点的参考照片；缺失时保留空值"
)
async def get_attraction_photo(name: str = Query(...,min_length=1,max_length=160), city: str = Query('',max_length=80)):
    """
    获取景点图片

    Args:
        name: 景点名称
        city: 城市名(用于精准搜索)

    Returns:
        图片URL
    """
    try:
        photo_url = await find_poi_photo(name, city)

        return {
            "success": True,
            "message": "地图登记的地点参考图片" if photo_url else "当前没有可确认的地点图片",
            "data": {
                "name": name,
                "photo_url": photo_url
            }
        }

    except AMapServiceError as error:raise map_http_error(error) from None
    except Exception as e:
        print(f"❌ 获取景点图片失败: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"获取景点图片失败: {str(e)}"
        )

