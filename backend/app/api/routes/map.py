"""地图查询：真实REST结果、明确空结果和可重试的供应商错误。"""
from fastapi import APIRouter, HTTPException, Query, Depends
from ...models.schemas import POISearchResponse, RouteRequest, RouteResponse, WeatherResponse
from ...services.amap_rest import get_forecast, AMapServiceError
from ...services.map_queries import find_places, address_route, forecast_models
from ...services.request_gate import protect_request
from ..map_errors import map_http_error

router = APIRouter(prefix="/map", tags=["地图服务"], dependencies=[Depends(protect_request)])


@router.get('/poi', response_model=POISearchResponse, summary='搜索POI')
async def search_poi(keywords: str = Query(...,min_length=1,max_length=120),
                     city: str = Query(...,min_length=1,max_length=80), citylimit: bool = True):
    try:
        places = await find_places(city,keywords,citylimit)
        return POISearchResponse(success=True,message='已查询地点' if places else '未找到匹配地点',data=places)
    except AMapServiceError as error:raise map_http_error(error) from None


@router.get('/weather', response_model=WeatherResponse, summary='查询当前短期天气预报')
async def get_weather(city: str = Query(...,min_length=1,max_length=80)):
    try:
        forecasts = forecast_models(await get_forecast(city))
        return WeatherResponse(success=True,message='查询时的短期预报，非出行当天保证' if forecasts else '当前没有可用的短期预报',data=forecasts)
    except AMapServiceError as error:raise map_http_error(error) from None


@router.post('/route', response_model=RouteResponse, summary='查询明确地址之间的路线')
async def plan_route(request: RouteRequest):
    try:
        return RouteResponse(success=True,message='已查询路线参考',data=await address_route(request))
    except AMapServiceError as error:raise map_http_error(error) from None


@router.get('/health', summary='检查本地地图配置，不探测供应商')
async def health_check():
    from ...config import get_settings
    if not get_settings().amap_api_key:raise HTTPException(503,'地图服务尚未配置')
    return {'status':'configured','service':'map-service','upstreams':'not_probed'}
