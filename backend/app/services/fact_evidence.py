"""Observed reference data is distinct from official verification.

Freshness windows are product reminders, not provider accuracy guarantees.
Evidence binds both the POI and exact value, so edits cannot inherit its age.
"""
import hashlib
import json
from datetime import datetime, timezone
from ..models.schemas import FactEvidence

MAX_AGE_DAYS = {'hours': 7, 'price': 1, 'parent': 30}


def value_of(place, field):
    if field == 'parent':
        return getattr(place, 'parent_poi_id', None)
    if field == 'hours':
        return place.open_hours
    return getattr(place, 'ticket_price', getattr(place, 'estimated_cost', None)) if place.cost_status in {'confirmed', 'estimated'} else None


def fingerprint(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        value = float(value)
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()


def record_fact(place, field, source, recorded_at=None, *, source_url=None, scope=None, applies_on=None, document_hash=None):
    value = value_of(place, field)
    if value is None or value == '':
        setattr(place, field + '_evidence', None)
        return
    # A provider record without the original query time must stay undated.
    if source == 'amap' and (not recorded_at or not place.poi_id):
        setattr(place, field + '_evidence', None)
        return
    if source == 'official' and (field != 'price' or not all((recorded_at, source_url, scope, applies_on, document_hash, place.poi_id))):
        setattr(place, field + '_evidence', None)
        return
    setattr(place, field + '_evidence', FactEvidence(source=source,
        recorded_at=recorded_at or datetime.now(timezone.utc).isoformat(),
        poi_id=place.poi_id or '', value_hash=fingerprint(value), source_url=source_url, scope=scope, applies_on=applies_on, document_hash=document_hash))


def fact_state(place, field, now=None, visit_date=None):
    now = now or datetime.now(timezone.utc)
    if field=='price' and visit_date and getattr(place,'nightly_quote',None):
        from .hotel_quotes import applicable_quote
        quote=applicable_quote(place,visit_date)
        if quote:
            return {'source':'user','recorded_at':quote.recorded_at,'status':'user_provided','scope':quote.room_type,'applies_on':quote.night_date}
    value = value_of(place, field)
    base = {'source': None, 'recorded_at': None, 'status': 'missing'}
    if value is None or value == '':
        return base
    evidence = getattr(place, field + '_evidence', None)
    if not evidence or evidence.poi_id != (place.poi_id or '') or evidence.value_hash != fingerprint(value):
        return {**base, 'status': 'unknown_age'}
    metadata = ({'source_url': evidence.source_url, 'scope': evidence.scope, 'applies_on': evidence.applies_on}
                if evidence.source == 'official' else {})
    if evidence.source == 'official' and not all((evidence.source_url, evidence.scope, evidence.applies_on, evidence.document_hash)):
        return {**base, **metadata, 'source': 'official', 'status': 'unknown_age'}
    if evidence.source == 'official' and visit_date and evidence.applies_on != visit_date:
        return {**base, **metadata, 'source': 'official', 'status': 'scope_mismatch'}
    try:
        stamp = datetime.fromisoformat(evidence.recorded_at.replace('Z', '+00:00'))
        if not stamp.tzinfo or stamp > now:
            return {**base, **metadata, 'source': evidence.source, 'status': 'unknown_age'}
    except (ValueError, TypeError):
        return {**base, **metadata, 'source': evidence.source, 'status': 'unknown_age'}
    age = (now - stamp).total_seconds() / 86400
    status = ('model_estimate' if evidence.source == 'model' else 'user_provided' if evidence.source == 'user'
              else 'stale_reference' if age >= MAX_AGE_DAYS[field] else 'official_reference' if evidence.source == 'official' else 'recent_reference')
    return {**metadata, 'source': evidence.source, 'recorded_at': stamp.isoformat(), 'status': status}


def needs_refresh(place, field, now=None, visit_date=None):
    state = fact_state(place, field, now, visit_date)
    # User data and model estimates are not refreshed by a map reference lookup.
    if field == 'price' and place.cost_status == 'confirmed':
        return False
    return state['status'] in {'missing', 'unknown_age', 'stale_reference', 'scope_mismatch'} and state['source'] not in {'user', 'model'}


def fact_report(plan, now=None):
    rows = []
    for day in plan.days:
        for place in [*day.attractions, *day.meals, *([day.hotel] if day.hotel else [])]:
            rows.append({'date': day.date, 'item_id': place.item_id, 'name': getattr(place, 'restaurant', None) or place.name,
                         'hours': fact_state(place, 'hours', now), 'price': fact_state(place, 'price', now, day.date)})
    facts = [row[field] for row in rows for field in ('hours', 'price')]
    return {'items': rows, 'stale': sum(f['status'] == 'stale_reference' for f in facts),
            'unknown_age': sum(f['status'] == 'unknown_age' for f in facts),
            'scope_mismatch': sum(f['status'] == 'scope_mismatch' for f in facts),
            'missing': sum(f['status'] == 'missing' for f in facts)}


def reconcile_manual_facts(plan, previous):
    """Keep server evidence only for identical fields on the identical visit."""
    # There is no manual forecast editor: posted weather cannot create supplier evidence.
    plan.weather_info=[item.model_copy(deep=True) for item in previous.weather_info]
    old = {(day.date, p.item_id): p for day in previous.days for p in [*day.attractions, *day.meals, *([day.hotel] if day.hotel else [])]}
    for day in plan.days:
        for place in [*day.attractions, *day.meals, *([day.hotel] if day.hotel else [])]:
            before = old.get((day.date, place.item_id))
            if hasattr(place, 'nightly_quote'):
                # Only the dedicated dated-quote endpoint may write quotations.
                # A general itinerary edit must not import, alter or move one.
                same_quote_context = (before and type(before) is type(place)
                    and all(getattr(before, key, None) == getattr(place, key, None)
                            for key in ('poi_id', 'place_id', 'name', 'address', 'location',
                                        'estimated_cost', 'cost_status')))
                quote = before.nightly_quote if same_quote_context else None
                place.nightly_quote = quote.model_copy(deep=True) if quote else None
            if hasattr(place, 'parent_poi_id'):
                parents = [p for (_, item_id), p in old.items() if item_id == place.item_id]
                prior = before or (parents[0] if len(parents) == 1 else None)
                identical = (prior and type(prior) is type(place) and prior.poi_id == place.poi_id
                             and (getattr(prior,'restaurant',None) or prior.name) == (getattr(place,'restaurant',None) or place.name)
                             and prior.address == place.address and prior.location == place.location)
                place.parent_poi_id = prior.parent_poi_id if identical else None
                place.parent_evidence = prior.parent_evidence.model_copy(deep=True) if identical and prior.parent_evidence else None
            # A moved visit must retain the official price's original date scope,
            # rather than relabel an unchanged seasonal price as user-confirmed.
            if before is None:
                candidates = [p for (_, item_id), p in old.items() if item_id == place.item_id]
                if len(candidates) == 1 and candidates[0].price_evidence and candidates[0].price_evidence.source == 'official':
                    before = candidates[0]
            for field in ('hours', 'price'):
                same = before and type(before) is type(place) and before.poi_id == place.poi_id and before.place_id == place.place_id
                if same and value_of(before, field) == value_of(place, field) and (field != 'price' or before.cost_status == place.cost_status):
                    evidence = getattr(before, field + '_evidence')
                    setattr(place, field + '_evidence', evidence.model_copy(deep=True) if evidence else None)
                else:
                    record_fact(place, field, 'user')
