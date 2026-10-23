"""Select travel candidates using provider identity, not popularity inventions.

Parent links never establish admission. Only a nearby returned parent can
subsume an unrequested child; missing parents and explicit choices remain.
"""
import math
import re
from .place_identity import normalized, parent_id

FACILITY = re.compile(r'(?:停车场|停车库|售票处|售票厅|检票口|售票窗口|游客服务中心|游客中心|管理处|管理办公室|公共厕所|卫生间|出入口|入口|出口)(?:[（(][^）)]*[）)])?$')


def explicit_choice(poi, request=None, keyword=''):
    name = poi.get('name') or ''
    aliases = {normalized(name)}
    # A requested pavilion such as 养心殿 must survive a broad parent query.
    tail = re.split(r'[-—－]', name)[-1]
    if len(tail) >= 3:
        aliases.add(normalized(tail))
    text = normalized(keyword)
    if request:
        text += normalized('\n'.join([request.free_text_input or '', request.reference_content or '',
                                     *(s.text or '' for s in request.reference_sources)]))
        if request.reference_mode == 'strict':
            return True
        if any(c.priority == 'required' and ((c.place and c.place.poi_id == poi.get('poi_id'))
               or normalized(c.place.name if c.place else c.name) in aliases) for c in request.reference_candidates):
            return True
    return any(len(alias) >= 3 and alias in text for alias in aliases)


def nearby_parent(child, parent):
    try:
        x, y = child['coordinates'], parent['coordinates']
        if len(x) != 2 or len(y) != 2 or any(isinstance(v, bool) or not isinstance(v, (float, int)) or not math.isfinite(v) for v in [*x, *y]):
            return False
        if any(not -180 <= p[0] <= 180 or not -90 <= p[1] <= 90 for p in (x, y)):
            return False
        lat = math.radians((x[1] + y[1]) / 2)
        return math.hypot((x[0]-y[0])*111.32*math.cos(lat), (x[1]-y[1])*110.57) <= 5
    except (KeyError, TypeError, ValueError):
        return False


def select_candidates(pois, request=None, keyword=''):
    by_id = {p.get('poi_id'): p for p in pois if p.get('poi_id')}
    kept, notes = [], []
    for poi in pois:
        if explicit_choice(poi, request, keyword):
            kept.append(poi)
            continue
        name = poi.get('name') or ''
        category = poi.get('category') or ''
        if FACILITY.search(name) or any(word in category for word in ('停车场', '公共厕所')):
            notes.append(f'{name}为配套设施候选，未作为默认游览景点。')
            continue
        identity = parent_id(poi.get('parent_poi_id'), poi.get('poi_id'))
        parent = by_id.get(identity)
        seen, cursor = {poi.get('poi_id')}, parent
        cycle = False
        while cursor:
            if cursor.get('poi_id') in seen:
                cycle = True
                break
            seen.add(cursor.get('poi_id'))
            cursor = by_id.get(parent_id(cursor.get('parent_poi_id'), cursor.get('poi_id')))
        parent_is_facility = parent and (FACILITY.search(parent.get('name') or '') or any(w in (parent.get('category') or '') for w in ('停车场', '公共厕所')))
        if parent and not cycle and not parent_is_facility and nearby_parent(poi, parent):
            notes.append(f'{name}登记属于{parent["name"]}，本批默认保留主体景点；不代表已核实入内条件。')
            continue
        kept.append(poi)
    return kept, notes


def overlap_issues(plan, request=None):
    from .fact_evidence import fact_state
    places = [(day, p) for day in plan.days for p in day.attractions]
    indexed = {p.poi_id: (day, p) for day, p in places if p.poi_id}
    issues = []
    for day, child in places:
        state = fact_state(child, 'parent')
        if state['source'] != 'amap' or state['status'] != 'recent_reference':
            continue
        entry = indexed.get(child.parent_poi_id)
        if not entry:
            continue
        parent_day, parent = entry
        a = {'coordinates': [child.location.longitude, child.location.latitude]}
        b = {'coordinates': [parent.location.longitude, parent.location.latitude]}
        if not nearby_parent(a, b):
            continue
        protected = child.locked or child.has_time_constraint or explicit_choice({'name': child.name, 'poi_id': child.poi_id}, request)
        issues.append(dict(code='nested_attraction', severity='info' if protected else 'warning', day_index=day.day_index,
                           message=f'{child.name}登记属于{parent.name}，两者分别安排在第{day.day_index+1}天和第{parent_day.day_index+1}天；请合并游览范围或确认需要单独再访，避免重复计算游览和门票。'))
    return issues


def validate_initial_candidates(plan, request):
    # Containment alone cannot forbid a separate visit (e.g. a large scenic
    # area across two days). Read checks flag scope; they never delete visits.
    for day in plan.days:
        for place in day.attractions:
            if FACILITY.search(place.name) and not explicit_choice({'name': place.name, 'poi_id': place.poi_id}, request):
                raise ValueError(f'{place.name}是配套设施候选，请选择真实游览主体；不能仅凭地图名称安排为独立景点')
