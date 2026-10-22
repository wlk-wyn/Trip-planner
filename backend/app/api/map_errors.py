from fastapi import HTTPException
from ..services.amap_rest import AMapServiceError


def map_http_error(error: AMapServiceError):
    if error.code in {'invalid_query','unknown_region','ambiguous_region','unknown_address','ambiguous_address','coarse_address','route_city_unknown'}:
        return HTTPException(422,str(error))
    if error.code in {'no_place','no_route','no_transit'}:
        return HTTPException(404,str(error))
    if error.retryable or error.code in {'configuration','local_throttle','10001','10002','10003','10005','10009','10012','10041','10044','40000'}:
        return HTTPException(503,str(error),headers={'Retry-After':'3'} if error.retryable else None)
    return HTTPException(502,'地图服务暂时无法返回有效结果，请稍后重试')
