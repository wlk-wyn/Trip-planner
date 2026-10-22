"""Bounded local duration proposals using already queried route edges.

Replay includes opening waits, meal bounds and appointments. The caller must
rebuild/validate the accepted proposal with actual queried edges before adoption;
changing places or order still requires a new route check.
"""
import time
import re

from .schedule_validator import build_schedule, stops_for_day

MAX_REPLAYS = 1024
MAX_SECONDS = 1.0


def explicit_duration_instructions(text):
    """Conservative protection detection, not a parser assigning visit lengths."""
    return bool(re.search(r'\d+(?:\.\d+)?\s*(?:个\s*)?(?:分钟|小时|min(?:ute)?s?|h(?:ours?|rs?)?)(?=$|[^a-zA-Z])|'
                          r'[一二三四五六七八九十百半两]+个?(?:小时|分钟|刻钟)|半天|一整天|不少于|至少.*停留', text, re.I))


def duration_proposal(day, request, allowances, margin=10, *, accept=None):
    original = day.model_copy(deep=True)
    stops = stops_for_day(original)
    baseline = build_schedule(original, stops, original.schedule.segments, request)
    if (baseline.minimum_minutes != day.schedule.minimum_minutes
            or [i.model_dump() for i in baseline.items] != [i.model_dump() for i in day.schedule.items]):
        return None
    target = baseline.available_minutes-margin
    candidate = day.model_copy(deep=True)
    cuts = dict(allowances)
    for place in candidate.attractions:
        place.visit_duration -= cuts.get(place.item_id, 0)
    deadline, replays = time.monotonic()+MAX_SECONDS, 0

    def fits():
        nonlocal replays
        replays += 1
        if replays > MAX_REPLAYS or time.monotonic() > deadline:
            raise TimeoutError('duration proposal search budget exhausted')
        schedule = build_schedule(candidate, stops_for_day(candidate), day.schedule.segments, request)
        return (schedule.status == 'feasible' and not schedule.conflicts
                and (accept(candidate, schedule) if accept else schedule.minimum_minutes <= target))

    try:
        # Even the maximal allowed reduction may simply turn into more waiting.
        if not fits():
            return None
        places = [p for p in candidate.attractions if p.item_id in cuts]
        # Restore entire visits first: cuts before a long wait are often useless.
        for place in places:
            cut = cuts[place.item_id]
            place.visit_duration += cut
            if fits():
                cuts[place.item_id] = 0
            else:
                place.visit_duration -= cut
        # Share the remaining restoration rather than unnecessarily cutting a
        # single visit. Every retained minute must still meet the end margin.
        blocked = set()
        while any(cut and identity not in blocked for identity,cut in cuts.items()):
            for place in places:
                identity = place.item_id
                if not cuts[identity] or identity in blocked:
                    continue
                place.visit_duration += 1
                if fits():
                    cuts[identity] -= 1
                else:
                    place.visit_duration -= 1
                    blocked.add(identity)
    except TimeoutError:
        return None
    candidate.schedule = None
    return candidate
