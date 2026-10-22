"""One advisory review before routing; reserves are not measured travel times.

Only complete, ordinary automatic drafts qualify. Proposals retain all places,
daily counts, hotel/meal choices and user windows, with the existing 25% bound
on optional suggested visits. Nothing here marks a schedule feasible.
"""
from collections import Counter
import time
from itertools import permutations
from pydantic import BaseModel, ConfigDict, Field

from .initial_planning import has_day_instructions, _reference_text
from .duration_fit import explicit_duration_instructions
from .meal_timing import explicit_meal_times
from .meal_windows import protected_meal_ids
from .schedule_validator import stops_for_day, minutes
from .opening_hours import parse_place_hours


class ReviewVisit(BaseModel):
    model_config = ConfigDict(extra='forbid')
    poi_id: str = Field(min_length=1)
    visit_duration: int = Field(strict=True,ge=1,le=1440)


class ReviewDay(BaseModel):
    model_config = ConfigDict(extra='forbid')
    date: str
    attractions: list[ReviewVisit] = Field(min_length=1,max_length=6)


class DensityReview(BaseModel):
    model_config = ConfigDict(extra='forbid')
    days: list[ReviewDay] = Field(min_length=1,max_length=14)


def minimum_suggestion(place):
    floor=90 if any(word in place.name+(place.category or '') for word in ('博物','美术馆','科技馆','展览')) else 60
    return max(place.visit_duration-place.visit_duration//4,min(place.visit_duration,floor))


def optimistic_bounds(day,request,stops):
    """Internal lower bound only: no guessed traffic, no persisted timeline."""
    cursor=minutes(day.start_time or request.daily_start_time)
    buffer=day.buffer_minutes if day.buffer_minutes is not None else request.buffer_minutes
    conflicts=[]
    for index,stop in enumerate(stops):
        if index:cursor+=buffer
        target=stop['fixed'] or day.planned_start_times.get(stop['id'])
        if target:cursor=max(cursor,minutes(target))
        if stop['kind']!='hotel':
            rule=parse_place_hours(stop['place'],day.date)
            if rule.closed or rule.outside_weekday:
                conflicts.append(stop['place'].name or getattr(stop['place'],'restaurant',''))
            elif rule.understood and rule.windows:
                arrivals=[max(cursor,a) for a,b in rule.windows if max(cursor,a)+stop['duration']<=b
                          and (rule.last_admission is None or max(cursor,a)<rule.last_admission)]
                if arrivals:cursor=min(arrivals)
                else:conflicts.append(stop['place'].name or getattr(stop['place'],'restaurant',''))
        cursor+=stop['duration']
    return list(dict.fromkeys(conflicts)),cursor


def density_rows(plan,request):
    text=_reference_text(request)
    places=[p for day in plan.days for p in day.attractions]
    if (not 1<=len(plan.days)<=14 or any(not 1<=len(d.attractions)<=6 for d in plan.days)
            or has_day_instructions(request) or explicit_duration_instructions(text)
            or explicit_meal_times(request) or request.reference_candidates
            or any(p.locked or p.fixed_start_time or not p.poi_id for p in places)
            or len({p.poi_id for p in places})!=len(places)
            or len({p.item_id for p in places})!=len(places)
            or protected_meal_ids(plan,request)
            or any((d.hotel and d.hotel.locked) or d.schedule is not None or d.visit_order
                   or any(p.item_id in d.planned_start_times for p in d.attractions) for d in plan.days)):
        return []
    from .evening_planning import wants_evening
    if wants_evening(request) or any(word in text for word in ('全部','所有景点','一个不删','一个不少')):
        return []
    # Partial drafts do not justify assuming that three whole meals exist.
    if any(len(d.meals)!=3 or {m.type for m in d.meals}!={'breakfast','lunch','dinner'}
           or any(not m.route_matched for m in d.meals) for d in plan.days):
        return []
    rows=[]
    for day in plan.days:
        stops=stops_for_day(day.model_copy(deep=True))
        available=minutes(day.end_time or request.daily_end_time)-minutes(day.start_time or request.daily_start_time)
        if available<=0:return []
        reserve=max(30,round(available*.2))
        buffer=max(0,len(stops)-1)*(day.buffer_minutes if day.buffer_minutes is not None else request.buffer_minutes)
        suggested=sum(s['duration'] for s in stops)
        excess=suggested+buffer+reserve-available
        conflicts,earliest_end=optimistic_bounds(day,request,stops)
        if conflicts or earliest_end>minutes(day.end_time or request.daily_end_time) or excess>max(30,round(available*.05)):
            rows.append(dict(date=day.date,available_minutes=available,suggested_stop_minutes=suggested,
                buffer_minutes=buffer,planning_traffic_reserve_minutes=reserve,advisory_excess_minutes=excess,
                optimistic_hours_conflicts=conflicts,optimistic_end_overrun_minutes=max(0,earliest_end-minutes(day.end_time or request.daily_end_time))))
    return rows


def review_context(plan,request,rows):
    return dict(warning='交通预留是密度指导，不是已查询耗时，不能写成路线或可执行结论。',
        user_preferences=(request.free_text_input or '')[:1000],advisory_rows=rows,
        days=[dict(date=d.date,attractions=[dict(poi_id=p.poi_id,name=p.name,location=p.location.model_dump() if p.location else None,
            registered_windows_minutes=list(parse_place_hours(p,d.date).windows),
            registered_last_admission=parse_place_hours(p,d.date).last_admission,visit_duration=p.visit_duration,
            minimum_allowed=minimum_suggestion(p)) for p in d.attractions]) for d in plan.days])


def density_score(plan,request):
    rows=density_rows(plan,request)
    return (sum(len(r['optimistic_hours_conflicts']) for r in rows),
            sum(r['optimistic_end_overrun_minutes'] for r in rows),
            max((max(0,r['advisory_excess_minutes']) for r in rows),default=0),
            sum(max(0,r['advisory_excess_minutes']) for r in rows))


def order_before_review(plan,request):
    """Try a small number of calendar orders, retaining every visit duration.

    No route or model calls, no reassignment of meals/hotels. Calendar bounds
    rank unverified drafts only; every adopted order still requires real routes.
    """
    if not density_rows(plan,request):return plan
    result=plan.model_copy(deep=True);deadline=time.monotonic()+.15;checks=0
    for index,day in enumerate(result.days):
        baseline=density_score(result,request);best=None;best_score=baseline
        for order in permutations(day.attractions):
            if [p.item_id for p in order]==[p.item_id for p in day.attractions]:continue
            if checks>=64 or time.monotonic()>deadline:break
            checks+=1;candidate=result.model_copy(deep=True)
            candidate.days[index].attractions=[p.model_copy(deep=True) for p in order]
            score=density_score(candidate,request)
            if score<best_score:best,best_score=candidate,score
        if best is not None:
            result=best
            result.days[index].description=' → '.join(p.name for p in result.days[index].attractions)
        if checks>=64 or time.monotonic()>deadline:break
    return result


def apply_density_review(plan,request,payload):
    if not density_rows(plan,request):
        raise ValueError('仅复核未校验且没有用户锚点的完整自动初稿')
    review=DensityReview.model_validate(payload)
    if [d.date for d in review.days]!=[d.date for d in plan.days]:
        raise ValueError('初稿复核不得更改日期')
    original={p.poi_id:p for d in plan.days for p in d.attractions}
    if Counter(v.poi_id for d in review.days for v in d.attractions)!=Counter(original.keys()):
        raise ValueError('初稿复核必须保留全部地点，不得增删或重复')
    result=plan.model_copy(deep=True)
    for day,proposed in zip(result.days,review.days):
        if len(day.attractions)!=len(proposed.attractions):
            raise ValueError('初稿复核不得改写每天地点数量')
        visits=[]
        for visit in proposed.attractions:
            place=original[visit.poi_id]
            if not minimum_suggestion(place)<=visit.visit_duration<=place.visit_duration:
                raise ValueError('初稿复核的建议停留超出允许范围')
            visits.append(place.model_copy(update={'visit_duration':visit.visit_duration},deep=True))
        day.attractions=visits;day.schedule=None;day.visit_order=[]
        day.description=' → '.join(p.name for p in visits)
    def score(value):return density_score(value,request)
    if score(result)>=score(plan):
        raise ValueError('初稿复核没有改善预算密度')
    # The reviewer can over-shorten unrelated visits. Restore every minute we
    # can without losing any of its preflight improvements. This remains an
    # advisory computation; actual route validation still decides feasibility.
    target=score(result);deadline=time.monotonic()+.25;checks=0
    changed=[p for d in result.days for p in d.attractions if p.visit_duration<original[p.poi_id].visit_duration]
    def can_restore():
        nonlocal checks
        checks+=1
        if checks>256 or time.monotonic()>deadline:return None
        return all(after<=before for after,before in zip(score(result),target))
    for place in changed:
        previous=place.visit_duration;place.visit_duration=original[place.poi_id].visit_duration
        accepted=can_restore()
        if accepted is not True:place.visit_duration=previous
        if accepted is None:return result
    blocked=set()
    while any(p.visit_duration<original[p.poi_id].visit_duration and p.poi_id not in blocked for p in changed):
        for place in changed:
            if place.poi_id in blocked or place.visit_duration==original[place.poi_id].visit_duration:continue
            place.visit_duration+=1;accepted=can_restore()
            if accepted is not True:place.visit_duration-=1;blocked.add(place.poi_id)
            if accepted is None:return result
    return result
