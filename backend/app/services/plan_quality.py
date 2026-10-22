"""Deterministic quality coverage, separate from an LLM's success claim."""
from datetime import date, timedelta
from .reference_service import normalized
from .fact_evidence import fact_report


def evaluate_plan(plan, request):
    expected=[(date.fromisoformat(request.start_date)+timedelta(days=i)).isoformat() for i in range(request.travel_days)]
    date_valid=[d.date for d in plan.days]==expected and plan.start_date==request.start_date and plan.end_date==request.end_date and request.end_date==expected[-1]
    city_valid=normalized(plan.city).removesuffix('市')==normalized(request.city).removesuffix('市')
    places=[p for d in plan.days for p in [*d.attractions,*d.meals,*([d.hotel] if d.hotel else [])]]
    segments=[s for d in plan.days if d.schedule for s in d.schedule.segments]
    checked=sum(d.schedule is not None for d in plan.days)
    conflicts=[d.date for d in plan.days if d.schedule and d.schedule.conflicts]
    verified=sum(s.status=="verified" and s.duration_minutes is not None and s.duration_minutes >= 0 for s in segments)
    required=[c for c in request.reference_candidates if c.priority=="required"]
    def included(candidate):
        match=candidate.place
        if not match: return False
        return any((p.poi_id==match.poi_id if p.poi_id and match.poi_id else
                   ((getattr(p,"restaurant",None) or p.name)==match.name and p.location is not None
                    and abs(p.location.longitude-match.longitude)<0.0001 and abs(p.location.latitude-match.latitude)<0.0001)) for p in places)
    covered=sum(included(c) for c in required)
    from .plan_checks import run_checks
    issues=run_checks(plan,request)
    route_structure_valid=all(d.schedule and len(d.schedule.segments)==max(0,len(d.schedule.items)-1) for d in plan.days)
    hours_conflicts=sum(i["code"] in {"opening_hours_conflict", "closed_day", "opening_weekday_uncovered",
                                    "park_last_entry", "park_closing_time", "inner_venue_closed", "inner_venue_last_entry"} for i in issues)
    activity_days=sum(bool(d.attractions or any((m.restaurant or m.name).strip() for m in d.meals)) for d in plan.days)
    facts = fact_report(plan)
    return dict(rule_issues=issues,rule_errors=sum(i["severity"]=="error" for i in issues),dates_valid=date_valid,city_valid=city_valid,days_with_activities=activity_days,days_checked=checked,total_days=len(plan.days),
                verified_routes=verified,total_routes=len(segments),conflict_dates=conflicts,
                places_with_provider_id=sum(bool(p.poi_id) for p in places),total_places=len(places),
                required_covered=covered,required_total=len(required),
                schedule_passed=bool(plan.days) and date_valid and city_valid and activity_days==len(plan.days) and route_structure_valid and all(d.schedule.status=="feasible" for d in plan.days) and not conflicts and verified==len(segments) and covered==len(required) and not hours_conflicts and not any(i["severity"]=="error" for i in issues),
                opening_hours_conflicts=hours_conflicts,opening_hours_unconfirmed_days=sum(i["code"]=="opening_hours_unknown" for i in issues),
                stale_place_facts=facts['stale'],undated_place_facts=facts['unknown_age'],missing_place_facts=facts['missing'],
                nested_attraction_groups=sum(i["code"] == "nested_attraction" for i in issues),
                official_hours_verified=False,booking_availability_verified=False)
