"""One explicit cost basis for checks, evaluation and the result UI.

Listed prices remain references, not a booking quotation. Unknown amounts are
never zero; assumed party/room counts prevent a budget conclusion.
"""
import math
import re
from decimal import Decimal, ROUND_HALF_UP

from ..models.schemas import TripCostContext
from .schedule_integrity import check_integrity
from .fact_evidence import fact_state


def stated_budget(request):
    if getattr(request,'budget_mode','active')=='deferred':
        return None
    if request.destination_budget is not None:
        return request.destination_budget
    from .budget_intake import partial_budget
    text=request.free_text_input or ''
    matches = re.finditer(r'预算(?:\s|约|为|是|不超过|人民币|￥|¥){0,12}([0-9]+(?:,[0-9]{3})*(?:\.[0-9]{1,2})?)\s*(万)?\s*(?:元|块)?(?![\d,.])',text)
    match=next((m for m in matches if not partial_budget(re.split(r'[，,。；;\n]',text[:m.start()])[-1] + m[0])),None)
    if not match:
        return None
    amount = Decimal(match[1].replace(',', '')) * (10000 if match[2] else 1)
    return float(amount) if 0 <= amount <= 10000000 else None


def _money(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1000000000:
        return None
    return Decimal(str(value)).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)


def build_cost_report(plan, request=None, context=None):
    context = context if context is not None else TripCostContext.model_validate(request.model_dump() if request else {})
    people, rooms = context.party_size or 1, context.room_count or 1
    lines = []
    assumptions = []
    if context.party_size is None:
        assumptions.append('人数未填写，暂按1人试算')
    from .lodging_nights import lodging_dates, hotel_for_night
    overnight_dates = lodging_dates(plan)
    nights = len(overnight_dates) if overnight_dates is not None else None
    if nights is None:
        assumptions.append('日期范围无效或超过30天，住宿晚数待核实')
    if nights and context.room_count is None:
        assumptions.append('房间数未填写，暂按每晚1间试算')

    def add(day, kind, name, value, quantity, unit, status='unknown', source=''):
        price = _money(value) if status in {'estimated', 'confirmed'} else None
        amount = price * quantity if price is not None else None
        lines.append(dict(date=day, kind=kind, name=name, unit_price=float(price) if price is not None else None,
                          quantity=quantity, unit=unit, amount=float(amount) if amount is not None else None,
                          status=status if price is not None else 'unknown', source=source))

    def add_place(day, kind, place, value, quantity, unit):
        if kind=='hotels':
            from .hotel_quotes import applicable_quote
            quote=applicable_quote(place,day)
            if quote:
                add(day,kind,place.name,quote.price_per_room,quantity,unit,'confirmed','用户提供的指定日期房型报价，未独立核实')
                lines[-1].update(hotel_item_id=place.item_id,quote=quote.model_dump(),
                    evidence={'source':'user','status':'user_provided','recorded_at':quote.recorded_at,'scope':quote.room_type,'applies_on':day})
                return
        state = fact_state(place, 'price', visit_date=day)
        if place.price_evidence and place.price_evidence.source == 'official' and (
                state['source'] != 'official' or state['status'] in {'scope_mismatch', 'unknown_age'}):
            value = None
        add(day, kind, getattr(place, 'restaurant', None) or place.name, value, quantity, unit, place.cost_status, place.cost_source)
        lines[-1]['evidence'] = state
        if kind=='hotels':lines[-1]['hotel_item_id']=place.item_id

    for day in plan.days:
        for place in day.attractions:
            from .ticket_bookings import active_booking
            booking = active_booking(plan, day, place)
            remaining_people = max(0, people - booking.participants) if booking else people
            if remaining_people:
                add_place(day.date, 'attractions', place, place.ticket_price, remaining_people, '人次')
                if booking: lines[-1]['source'] += '；未被预订覆盖的人数参考费'
        for meal in day.meals:
            if (meal.restaurant or meal.name).strip():
                add_place(day.date, 'meals', meal, meal.estimated_cost, people, '人次')
        schedule = day.schedule
        if not schedule or check_integrity(day, request):
            add(day.date, 'transport', '当天交通待核实', None, people, '人')
        else:
            for segment in schedule.segments:
                value, status, source = None, 'unknown', ''
                if segment.status == 'verified' and segment.mode == 'walking':
                    value, status, source = 0, 'confirmed', '步行，无乘车费用'
                elif segment.status == 'verified' and segment.mode == 'transit':
                    value, status, source = segment.fare, 'estimated', '高德方案成人票价，查询时参考'
                # Driving taxi_cost is not fuel/parking/tolls; never substitute it.
                add(day.date, 'transport', f'{segment.from_name} → {segment.to_name}', value, people, '人', status, source)
    for overnight in overnight_dates or []:
        hotel = hotel_for_night(plan,overnight)
        if hotel:
            add_place(overnight, 'hotels', hotel, hotel.estimated_cost, rooms, '间夜')
        else:
            add(overnight, 'hotels', '当晚住宿待选择', None, rooms, '间夜')
    if nights is None:
        add(plan.start_date, 'hotels', '住宿日期待核实', None, rooms, '间夜')

    for booking in plan.ticket_bookings:
        net = Decimal(str(booking.paid_total)) - Decimal(str(booking.refunded_total))
        add(booking.day_date, 'attractions', f'{booking.place_name} · {booking.ticket_name}', float(net), 1, '笔', 'confirmed',
            '用户录入实付总额，未独立核实' if booking.status == 'recorded' else '用户记录已取消；保留实付减已退金额，不代表平台已办理退款')
        lines[-1].update(booking_id=booking.id, booking_status=booking.status,
            booking_participants=booking.participants, paid_total=booking.paid_total, refunded_total=booking.refunded_total,
            evidence={'source':'user','status':'user_provided','recorded_at':booking.recorded_at,'applies_on':booking.day_date})

    totals = {kind: float(sum((Decimal(str(line['amount'])) for line in lines if line['kind'] == kind and line['amount'] is not None), Decimal(0)))
              for kind in ('attractions', 'meals', 'hotels', 'transport')}
    subtotal = float(sum((Decimal(str(value)) for value in totals.values()), Decimal(0)))
    unknown = sum(line['amount'] is None for line in lines)
    pending_quote_taxes=sum(bool(line.get('quote')) and line['quote']['taxes_included'] is not True for line in lines)
    deferred = context.budget_mode=='deferred'
    target = None if deferred else context.destination_budget
    free_text_target = stated_budget(request) if request and target is None and not deferred else None
    status = 'deferred' if deferred else 'not_set'
    if target is not None:
        status = 'needs_context' if assumptions else 'over_reference' if subtotal > target else 'incomplete' if unknown or pending_quote_taxes else 'within_listed_reference'
    elif free_text_target is not None:
        status = 'needs_scope'
    # A remaining allowance is conditional arithmetic, never a supplier price.
    unknown_hotels=[line for line in lines if line['kind']=='hotels' and line['amount'] is None]
    other_unknown=sum(line['kind']!='hotels' and line['amount'] is None for line in lines)
    units=sum(line['quantity'] for line in unknown_hotels)
    remaining=Decimal(str(target))-Decimal(str(subtotal)) if target is not None and not assumptions else None
    lodging_allowance=None
    if remaining is not None and units and not other_unknown and not pending_quote_taxes:
        lodging_allowance={'remaining_reference_budget':float(max(Decimal(0),remaining)),
            'unpriced_room_nights':units,
            'average_ceiling_per_room_night':float((max(Decimal(0),remaining)/units).quantize(Decimal('.01'),rounding='ROUND_DOWN')),
            'already_over_reference':remaining<0,
            'scope':'仅在其他已列参考费用不变、且没有其他未知费用时的住宿预留，不是酒店报价或可订保证'}
    return dict(context=dict(party_size=people, room_count=rooms, lodging_nights=nights), assumptions=assumptions,
                totals=totals, known_total=subtotal, unknown_count=unknown,
                estimated_count=sum(line['status'] == 'estimated' for line in lines), lines=lines,
                budget=dict(limit=target if target is not None else free_text_target, status=status),
                lodging_allowance=lodging_allowance,
                unknown_hotel_nights=len(unknown_hotels),other_unknown_count=other_unknown,
                hotel_quote_taxes_pending=pending_quote_taxes,
                exclusions=['往返目的地的大交通', '购物、个人消费及额外项目', '行程日期外的住宿和额外用餐'],
                notes=['未被用户预订记录覆盖的门票和公共交通按成人参考价计算；儿童、学生等优惠需另核实。',
                       '门票实付按每笔总额计一次；未覆盖人数仍保留参考费。取消记录计实付减已退，尚未退回的钱仍计入小计；再次购票另计。',
                       '官网公示门票也只是当前政策参考，单独售票展览、讲解和园内交通等未列项目另计；不代表余票或预订成功。',
                       '住宿按出发日至返程日前一晚计算，最后一天列出的酒店不另算一晚。',
                       '用户提供的日期房型报价未独立核实；可住人数、早餐、取消政策和实际可订性仍需确认，未确认含税时不判断预算满足。',
                       '这是已列项目的参考小计，不是预订报价或完整旅行总价。'])
