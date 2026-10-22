"""Reviewed pedestrian gate references, distinct from vendor navigation hints.

Only exact identified venues use these rules. A gate is looked up by ID and
checked against name, parent and area; no inferred coordinate or centre fallback
can impersonate it. Other places retain explicitly labelled coordinate routing.
"""
import asyncio
from datetime import datetime, timezone, date

from ..models.schemas import RouteEndpoint, Location
from .amap_rest import place_detail
from .visitor_access import matching_policy, CHINA

POLICY_URL = 'https://www.dpm.org.cn/Visit.html'
REVIEWED_ON = '2026-10-18'
VENUE = 'B000A8UIN8'
GATES = {
    'entrance': dict(poi_id='B000A84GDN', name='故宫博物院-午门', label='午门', bounds=(116.3965,39.9130,116.3980,39.9142)),
    'exit': dict(poi_id='B000A9PISW', name='故宫博物院-神武门', label='神武门', bounds=(116.3960,39.9218,116.3976,39.9229)),
}


class GateUnavailable(RuntimeError):
    pass


def has_gate_policy(stop, city=None):
    policy = matching_policy(city or '北京', stop['place']) if stop['kind'] == 'attraction' else None
    return bool(policy and policy['key'] == 'palace')


def coordinate_endpoint(stop):
    place = stop['place']
    if not place.location:
        return None
    return RouteEndpoint(name=getattr(place,'restaurant',None) or place.name,
                         poi_id=place.poi_id or '',location=place.location.model_copy(deep=True))


def _in_bounds(location, bounds):
    left,bottom,right,top=bounds
    return left<=location.longitude<=right and bottom<=location.latitude<=top


def _recent(stamp, days=7):
    try:
        moment=datetime.fromisoformat(stamp.replace('Z','+00:00'))
        age=(datetime.now(timezone.utc)-moment).total_seconds()
        return moment.tzinfo is not None and -300<=age<days*86400
    except (AttributeError,TypeError,ValueError):
        return False


def matches_gate(endpoint, role):
    spec=GATES[role]
    return bool(endpoint and endpoint.basis=='reviewed_gate' and endpoint.role==role
                and endpoint.poi_id==spec['poi_id'] and endpoint.name==spec['name']
                and endpoint.venue_poi_id==VENUE and endpoint.policy_source_url==POLICY_URL
                and endpoint.policy_reviewed_on==REVIEWED_ON and _in_bounds(endpoint.location,spec['bounds']))


async def endpoint_for(stop,city,role,lookup=None):
    if not has_gate_policy(stop,city):
        return coordinate_endpoint(stop)
    spec=GATES[role]
    try:
        poi=await asyncio.wait_for((lookup or place_detail)(spec['poi_id']),timeout=4)
        if (not isinstance(poi,dict) or poi.get('poi_id')!=spec['poi_id'] or poi.get('name')!=spec['name']
                or poi.get('parent_poi_id')!=VENUE or not _recent(poi.get('observed_at'),1)):
            raise ValueError()
        lon,lat=poi['coordinates']
        location=Location(longitude=lon,latitude=lat)
        if not _in_bounds(location,spec['bounds']):
            raise ValueError()
        return RouteEndpoint(name=poi['name'],poi_id=poi['poi_id'],location=location,basis='reviewed_gate',role=role,
                             venue_poi_id=VENUE,observed_at=poi['observed_at'],policy_source_url=POLICY_URL,policy_reviewed_on=REVIEWED_ON)
    except asyncio.CancelledError:
        raise
    except Exception:
        raise GateUnavailable(f'暂时无法确认{spec["label"]}门址，请重新校验；未用景点中心坐标替代') from None


def geometry_errors(stops,segments,city=None):
    errors=[]
    for first,second,segment in zip(stops,stops[1:],segments):
        for stop,endpoint,role in ((first,segment.from_endpoint,'exit'),(second,segment.to_endpoint,'entrance')):
            if endpoint is None:
                continue  # Old coordinate routes need a separate gate-scope warning.
            if endpoint.basis=='reviewed_gate':
                valid=has_gate_policy(stop,city) and matches_gate(endpoint,role)
            else:
                valid=endpoint.role=='location' and stop['place'].location==endpoint.location and (stop['place'].poi_id or '')==endpoint.poi_id
            if not valid:
                errors.append('交通端点与当前地点或进出口方向不一致，请重新校验')
    return list(dict.fromkeys(errors))


def gate_issues(plan):
    issues=[]
    # Local import keeps the routing adapter independent of schedule construction.
    from .schedule_validator import stops_for_day
    policy_age=(datetime.now(CHINA).date()-date.fromisoformat(REVIEWED_ON)).days
    for day in plan.days:
        if not day.schedule:
            continue
        missing=False;stale=False
        stops=stops_for_day(day.model_copy(deep=True))
        for first,second,segment in zip(stops,stops[1:],day.schedule.segments):
            for stop,endpoint,role in ((first,segment.from_endpoint,'exit'),(second,segment.to_endpoint,'entrance')):
                if has_gate_policy(stop,plan.city):
                    if not matches_gate(endpoint,role):missing=True
                    elif not _recent(endpoint.observed_at) or not 0<=policy_age<=30:stale=True
        if missing or stale:
            issues.append(dict(code='venue_gate_unconfirmed',severity='warning',day_index=day.day_index,
                message=f'第{day.day_index+1}天故宫的'+('门址或参观方向参考已较旧' if stale and not missing else '交通尚缺少对应入口/出口的门址证据')+'，请重新校验交通；当前默认午门进、神武门出，临时开放需另查官方公告。'))
    return issues
