"""NEW-trip proposal: dine near lodging after a long return from the suburbs.

Search locations screen candidates; only queried routes can prove feasibility.
Restaurant replacement and meal reordering are adopted together or not at all.
"""
import asyncio
import time

from ..models.schemas import Meal, Location
from .amap_rest import search_nearby_restaurants
from .meal_windows import _near, protected_meal_ids
from .meal_timing import explicit_meal_times, meal_timing_issues, repair_meal_timing, prepare_meal_times
from .opening_hours import parse_hours, hours_issues
from .place_facts import apply_provider_facts
from .restaurant_candidates import automatic_restaurant
from .schedule_integrity import check_integrity
from .evening_planning import evening_coverage


async def repair_return_dinner(plan, request, validator, progress=None):
    from .initial_planning import has_day_instructions, fit_suggested_durations
    if explicit_meal_times(request) or has_day_instructions(request):
        return plan, []
    result = plan.model_copy(deep=True)
    protected = protected_meal_ids(result, request)
    notes, contexts, deadline = [], 0, time.monotonic()+20

    async def check(partial, req, progress=None):
        remaining = deadline-time.monotonic()
        if remaining <= 0:
            raise asyncio.TimeoutError()
        await asyncio.wait_for(validator(partial, req, progress), timeout=remaining)

    for day_index, day in enumerate(result.days):
        if (not day.hotel or not day.hotel.location or not day.schedule or check_integrity(day, request)
                or day.hotel.locked or any(p.locked or p.has_time_constraint for p in day.attractions)):
            continue
        dinner = next((m for m in day.meals if m.type == 'dinner'), None)
        if (not dinner or not dinner.route_matched or dinner.item_id in protected
                or any(m.locked for m in day.meals)):
            continue
        # This targets a checked, long final return; missing routes have their
        # separate repair path. Do not replace an otherwise satisfactory meal.
        edge = next((s for s in day.schedule.segments if s.from_id == dinner.item_id
                     and s.to_id == day.hotel.item_id+'-end'), None)
        if (not edge or edge.status != 'verified' or edge.duration_minutes is None or edge.duration_minutes < 60
                or (day.schedule.status == 'feasible' and not meal_timing_issues(result.model_copy(update={'days':[day]}), request))):
            continue
        if contexts >= 2 or time.monotonic() >= deadline:
            break
        contexts += 1
        if progress:
            await progress('查找返城后住宿附近的晚餐')
        try:
            pois = await asyncio.wait_for(search_nearby_restaurants(day.hotel.location.longitude, day.hotel.location.latitude,
                '', radius=1500, category='050100'), timeout=max(.001, deadline-time.monotonic()))
        except Exception:
            continue
        old_hours = parse_hours(dinner.open_hours, day.date)
        used = {p.poi_id for d in result.days for p in [*d.attractions,*d.meals,*([d.hotel] if d.hotel else [])] if p.poi_id}
        choices = []
        for poi in pois:
            distance = _near(day.hotel.location, poi)
            hours = parse_hours(poi.get('open_hours'), day.date)
            if (distance is None or not poi.get('poi_id') or poi['poi_id'] in used
                    or not automatic_restaurant(poi, 'dinner', visit_parent_ids={a.poi_id for a in day.attractions if a.poi_id}) or hours.closed or hours.outside_weekday
                    or (old_hours.understood and not hours.understood)):
                continue
            choices.append((distance, poi))
        for _, poi in sorted(choices, key=lambda row:row[0])[:2]:
            if time.monotonic() >= deadline:
                return result, notes
            candidate = day.model_copy(deep=True)
            replacement = Meal(**{**dinner.model_dump(), 'place_id':'', 'name':'', 'restaurant':poi['name'],
                'poi_id':poi['poi_id'], 'address':poi.get('address',''),
                'location':Location(longitude=poi['coordinates'][0],latitude=poi['coordinates'][1]),
                'open_hours':None, 'hours_evidence':None, 'price_evidence':None, 'recommended_dish':'',
                'estimated_cost':None,'cost_status':'unknown','cost_source':'',
                'description':'返程后在住宿附近就餐的候选；交通按查询时结果校验，营业与接待仍需确认。'})
            apply_provider_facts(replacement, poi)
            candidate.meals = [replacement if m.item_id == dinner.item_id else m for m in candidate.meals]
            candidate.schedule = None
            partial = result.model_copy(deep=True)
            partial.days = [candidate]
            partial = prepare_meal_times(partial, request)
            try:
                await check(partial, request, progress)
                partial, timing_notes = await asyncio.wait_for(
                    repair_meal_timing(partial, request, check, progress, allow_duration_fit=True),
                    timeout=max(.001, deadline-time.monotonic()))
                duration_notes = []
                checked = partial.days[0]
                if (checked.schedule and checked.schedule.conflicts
                        and all('超出可用时间' in text for text in checked.schedule.conflicts)
                        and not meal_timing_issues(partial, request)):
                    partial, duration_notes = await asyncio.wait_for(
                        fit_suggested_durations(partial,request,check,progress),
                        timeout=max(.001, deadline-time.monotonic()))
            except Exception:
                continue
            checked = partial.days[0]
            if (not checked.schedule or checked.schedule.status != 'feasible' or checked.schedule.conflicts
                    or check_integrity(checked,request) or meal_timing_issues(partial,request)
                    or any(i['code'] in {'closed_day','opening_hours_conflict'} for i in hours_issues(checked))):
                continue
            full = result.model_copy(deep=True)
            full.days[day_index] = checked
            if evening_coverage(result,request) is True and evening_coverage(full,request) is not True:
                continue
            result = full
            item = next(i for i in checked.schedule.items if i.id == dinner.item_id)
            notes.append(f'第{day.day_index+1}天将自动匹配的晚餐「{dinner.restaurant or dinner.name}」改为返城后住宿附近的「{replacement.restaurant}」，{item.start_time}开始；保留全部景点和三餐，已重新校验往返交通，营业与接待仍需确认。')
            notes.extend(timing_notes)
            notes.extend(duration_notes)
            break
    return result, notes
