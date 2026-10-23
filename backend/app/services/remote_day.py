"""Last-resort NEW-draft replacement of an optional, infeasible remote visit.

Keep visits, meals, durations and day windows. Search facts supply identities;
geography only screens candidates. Adopt only a complete, route-checked day.
"""
import asyncio
import re
import math
import time

from ..models.schemas import Attraction,Location
from .amap_rest import search_pois
from .initial_planning import has_day_instructions,_reference_text,_km
from .reference_service import normalized
from .meal_windows import protected_meal_ids
from .meal_timing import WINDOWS,explicit_meal_times,prepare_meal_times,repair_meal_timing,meal_timing_issues
from .place_facts import apply_provider_facts
from .opening_hours import hours_issues,excludes_date
from .schedule_integrity import check_integrity
from .evening_planning import evening_coverage


def _protected(place,day,request,text):
    aliases={normalized(place.name),normalized(re.sub(r'[（(].*?[)）]','',place.name))}
    # Protect common abbreviated requests such as 长城 / 八达岭, not only the
    # exact provider name. Unclear user intent is not permission to replace it.
    for word in ('长城','故宫','天坛','兵马俑','西湖','颐和园','圆明园'):
        if word in place.name:aliases.add(word)
    short=re.sub(r'(风景名胜区|风景区|景区|博物馆|公园|长城)$','',place.name)
    if len(short)>=2:aliases.add(normalized(short))
    return (place.locked or place.has_time_constraint or place.cost_status=='confirmed'
        or place.item_id in day.planned_start_times
        or any(alias and len(alias)>=2 and alias in text for alias in aliases)
        or any(c.priority=='required' and ((c.place and c.place.poi_id and c.place.poi_id==place.poi_id)
               or normalized(c.place.name if c.place else c.name) in aliases) for c in request.reference_candidates))


def _keyword(request):
    text=' '.join(request.preferences)+' '+(request.free_text_input or '')
    if any(w in text for w in ('历史','文化','人文','博物')):return '博物馆'
    if any(w in text for w in ('自然','风光','公园')):return '公园'
    if any(w in text for w in ('亲子','科学','科技')):return '科技馆'
    return None


def _theme_matches(poi,keyword):
    name=poi.get('name') or '';category=poi.get('category') or ''
    if keyword=='博物馆':
        # A generic museum type also includes chocolate/illusion experiences.
        # Require a conservative history/culture signal rather than proximity.
        if any(w in name for w in ('巧克力','失恋','冰淇淋','自拍','拍照','网红','奇幻','错觉','蜡像','泰迪')):return False
        return ('博物' in name+category and any(w in name for w in (
            '历史','文物','考古','遗址','古迹','民俗','故居','古代','文化','首都博物馆','国家博物馆',
            '钱币','铁道','邮政','工艺','胡同','古建筑','民间','丝绸','陶瓷','青铜','文字','博物院')))
    if keyword=='公园':return '公园' in name+category
    if keyword=='科技馆':return any(w in name+category for w in ('科技馆','科学馆','自然博物'))
    return False


async def repair_remote_day(plan,request,validator,align_meals,progress=None):
    keyword=_keyword(request)
    if not keyword or has_day_instructions(request) or explicit_meal_times(request):return plan,[]
    text=normalized(_reference_text(request))
    if re.search(r'全部|所有景点|一个不[删少]|(?:至少|正好|必须).*?(?:个景点|处景点)',text):return plan,[]
    result=plan.model_copy(deep=True);notes=[];attempts=0;deadline=time.monotonic()+30

    async def check(partial,req,progress=None):
        await asyncio.wait_for(validator(partial,req,progress=progress),timeout=max(.001,deadline-time.monotonic()))

    for index,day in enumerate(result.days):
        if (not day.hotel or not day.hotel.location or not day.schedule or check_integrity(day,request)
            or day.schedule.status!='conflict' or not day.schedule.conflicts
            or any('超出可用时间' not in reason for reason in day.schedule.conflicts)
            or any(s.status!='verified' or s.duration_minutes is None for s in day.schedule.segments)
            or day.hotel.locked or any(p.locked or p.has_time_constraint for p in day.attractions)):
            continue
        protected=protected_meal_ids(result,request)
        if any(m.item_id in protected for m in day.meals if m.type in {'lunch','dinner'}):continue
        if any(m.type in WINDOWS and day.planned_start_times.get(m.item_id) not in (None,WINDOWS[m.type][0]) for m in day.meals):continue
        remote=[p for p in day.attractions if p.location and _km(p,day.hotel)>=20
                and not _protected(p,day,request,text)]
        if not remote:continue
        total=sum(s.duration_minutes for s in day.schedule.segments)
        if total<180 or total<day.schedule.available_minutes*.4:continue
        old=max(remote,key=lambda p:_km(p,day.hotel))
        if attempts>=2 or time.monotonic()>=deadline:break
        if progress:await progress('查询更近的可选景点，保留原远郊项目为备选')
        try:
            pois=await asyncio.wait_for(search_pois(plan.city,keyword,20),timeout=max(.001,deadline-time.monotonic()))
        except Exception:continue
        used=[p for d in result.days for p in d.attractions]
        choices=[]
        for poi in pois:
            name=poi.get('name') or '';coords=poi.get('coordinates');category=poi.get('category') or ''
            if (not poi.get('poi_id') or not poi.get('address') or not isinstance(coords,(list,tuple)) or len(coords)!=2
                or any(not isinstance(v,(int,float)) or not math.isfinite(v) for v in coords)
                or not (73<=coords[0]<=136 and 18<=coords[1]<=54)
                or not _theme_matches(poi,keyword)
                or excludes_date(poi,day.date)
                or re.search(r'暂停(?:营业|开放)|暂不开放|永久关闭|停业',poi.get('open_hours') or '')
                or any(w in name for w in ('售票','停车','入口','出口','服务中心','管理处','办公室','公司','培训'))
                or any(p.poi_id==poi['poi_id'] or normalized(p.name) in normalized(name) or normalized(name) in normalized(p.name) for p in used)):
                continue
            try:
                replacement=Attraction(name=name,poi_id=poi['poi_id'],address=poi['address'],
                    location=Location(longitude=coords[0],latitude=coords[1]),visit_duration=old.visit_duration,
                    category=category,item_id=old.item_id,
                    description='为避免远郊往返导致全天超时，查询的同偏好近城备选；停留为建议，开放与预约需另确认。')
                apply_provider_facts(replacement,poi)
            except (ValueError,TypeError):continue
            distance=_km(replacement,day.hotel)
            if distance<=12:choices.append((distance,replacement))
        for _,replacement in sorted(choices,key=lambda c:c[0])[:2]:
            if attempts>=2 or time.monotonic()>=deadline:return result,notes
            attempts+=1
            candidate=day.model_copy(deep=True)
            candidate.attractions=[replacement if p.item_id==old.item_id else p for p in candidate.attractions]
            candidate.visit_order=[];candidate.schedule=None
            partial=result.model_copy(update={'days':[candidate]})
            try:
                await asyncio.wait_for(align_meals(partial,repair=True,protected_ids=protected),timeout=max(.001,deadline-time.monotonic()))
                live_ids={p.item_id for d in partial.days for p in [*d.attractions,*d.meals,*([d.hotel] if d.hotel else [])]}
                partial.days[0].planned_start_times={key:value for key,value in partial.days[0].planned_start_times.items() if key in live_ids}
                partial=prepare_meal_times(partial,request)
                await check(partial,request,progress)
                partial,timing_notes=await repair_meal_timing(partial,request,check,progress)
            except Exception:continue
            checked=partial.days[0]
            if (not checked.schedule or checked.schedule.status!='feasible' or checked.schedule.conflicts
                or check_integrity(checked,request) or meal_timing_issues(partial,request)
                or not {'breakfast','lunch','dinner'}<={m.type for m in checked.meals}
                or len(checked.meals)!=len(day.meals)
                or any(i['code'] in {'closed_day','opening_hours_conflict'} for i in hours_issues(checked))):continue
            full=result.model_copy(deep=True);full.days[index]=checked
            from .attraction_candidates import overlap_issues
            if any(i['severity']=='warning' for i in overlap_issues(full,request)):continue
            if evening_coverage(result,request) is True and evening_coverage(full,request) is not True:continue
            checked.description=' → '.join(p.name for p in checked.attractions)
            result=full;result.budget=None
            notes.append(f'第{day.day_index+1}天远郊往返无法在原窗口内安排，将自动选择的「{old.name}」改为更近的「{replacement.name}」；原项目保留为备选。景点数量、建议停留时长、三餐及出行窗口未缩减，餐厅随新路线重新匹配并校验。')
            notes.extend(timing_notes)
            result.adjustment_notes=list(dict.fromkeys([*result.adjustment_notes,*notes]))[-100:]
            break
    return result,notes
