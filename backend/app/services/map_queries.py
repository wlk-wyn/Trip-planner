"""Public map adapters backed by the same paced REST requests as planning."""
import json
from datetime import date, datetime, timezone
from ..models.schemas import Location, POIInfo, WeatherInfo, RouteInfo
from .amap_rest import search_pois, place_detail, geocode_address, get_route, AMapServiceError


def clean_query(value):
    result=value.strip()
    if not result:raise AMapServiceError('invalid_query','请填写具体地点或城市')
    return result


def as_poi(poi):
    if not poi.get('poi_id') or not isinstance(poi.get('name'),str) or not poi['name']:
        raise AMapServiceError('invalid_response','地点服务没有返回完整身份信息')
    return POIInfo(id=poi['poi_id'],name=poi['name'],type=poi.get('category',''),address=poi.get('address',''),
                   location=Location(longitude=poi['coordinates'][0],latitude=poi['coordinates'][1]),
                   tel=poi.get('tel'),photos=poi.get('photos',[]),observed_at=poi.get('observed_at'))


async def find_places(city,keyword,citylimit=True):
    return [as_poi(poi) for poi in await search_pois(clean_query(city),clean_query(keyword),citylimit=citylimit)]


async def find_detail(poi_id):
    result=await place_detail(clean_query(poi_id))
    if result is None:raise AMapServiceError('no_place','未找到该地点，可能已变更或下线')
    return as_poi(result).model_dump()


def forecast_models(rows,start=None,end=None):
    result=[]
    if not isinstance(rows,list):return result
    for row in rows:
        if not isinstance(row,dict):continue
        try:day=date.fromisoformat(row.get('date','')).isoformat()
        except (ValueError,TypeError):continue
        if (start and day<start) or (end and day>end):continue
        text=lambda key:row.get(key) if isinstance(row.get(key),str) else ''
        result.append(WeatherInfo(date=day,day_weather=text('dayweather'),night_weather=text('nightweather'),
                                 day_temp=row.get('daytemp'),night_temp=row.get('nighttemp'),
                                 wind_direction=text('daywind'),wind_power=text('daypower'),
                                 source='amap' if row.get('source')=='amap' else None,
                                 observed_at=row.get('observed_at'),report_time=row.get('report_time'),adcode=row.get('adcode')))
    return result


def grounded_weather(evidence,start,end):
    try:rows=json.loads(evidence.get('weather_info') or '[]')
    except (ValueError,TypeError):return []
    return forecast_models(rows,start,end)


async def address_route(request):
    origin=await geocode_address(request.origin_address,request.origin_city or None)
    destination=await geocode_address(request.destination_address,request.destination_city or None)
    def city(item):
        return item['city'] or (item['province'] if item['province'] in {'北京市','上海市','天津市','重庆市'} else '')
    start_city,end_city=city(origin),city(destination)
    if request.route_type=='transit' and (not start_city or not end_city):
        raise AMapServiceError('route_city_unknown','无法确定公交起终点所在城市，请使用完整的具体地址')
    same=(origin['longitude'],origin['latitude'])==(destination['longitude'],destination['latitude'])
    route=await get_route(Location(longitude=origin['longitude'],latitude=origin['latitude']),
                          Location(longitude=destination['longitude'],latitude=destination['latitude']),start_city,request.route_type,destination_city=end_city)
    return RouteInfo(distance=route['distance_meters'],duration=route['duration_seconds'],
                     route_type=route.get('mode',request.route_type),requested_route_type=request.route_type,
                     description=('两端解析为同一坐标；0仅表示坐标间距离，不代表场所内行走耗时' if same else route.get('note') or '查询时的路线参考，不预测出行当天班次与交通'),
                     source='coordinate_identity' if same else 'amap',queried_at=datetime.now(timezone.utc).isoformat(),
                     origin=origin,destination=destination,legs=route.get('legs',[]),polyline=route.get('polyline',''))
