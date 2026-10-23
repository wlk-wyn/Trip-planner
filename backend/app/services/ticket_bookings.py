"""User-confirmed ticket ledger. No order creation, receipt or supplier lookup."""
import hashlib
import json
from datetime import date, datetime, timezone
from uuid import uuid4
from pydantic import BaseModel, Field, model_validator
from ..models.schemas import TicketBooking


class BookingConflict(ValueError):
    pass


class BookingInput(BaseModel):
    booking_id: str | None = Field(default=None, max_length=80)
    day_date: date
    item_id: str = Field(min_length=1, max_length=200)
    ticket_name: str = Field(min_length=1, max_length=120)
    participants: int = Field(ge=1, le=100, strict=True)
    paid_total: float = Field(ge=0, le=1000000, allow_inf_nan=False, strict=True)
    source_label: str = Field(default='', max_length=80)

    @model_validator(mode='after')
    def clean(self):
        self.ticket_name = self.ticket_name.strip()
        self.source_label = self.source_label.strip()
        if not self.ticket_name: raise ValueError('请填写票种或套餐名称')
        if round(self.paid_total, 2) != self.paid_total: raise ValueError('实付总额最多保留两位小数')
        return self


def fingerprint(place):
    value = {key: getattr(place, key, None) for key in ('place_id', 'name', 'address')}
    value['location'] = place.location.model_dump() if place.location else None
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def matches(booking, day, place):
    return booking.day_date == day.date and booking.item_id == place.item_id and booking.place_fingerprint == fingerprint(place)


def active_booking(plan, day, place):
    return next((b for b in plan.ticket_bookings if b.status == 'recorded' and matches(b, day, place)), None)


def preserve_bookings(plan, previous=None, *, protect=True):
    plan.ticket_bookings = [b.model_copy(deep=True) for b in previous.ticket_bookings] if previous else []
    if not protect: return
    for booking in plan.ticket_bookings:
        if booking.status == 'recorded' and not any(matches(booking, day, p) for day in plan.days for p in day.attractions):
            raise BookingConflict(f'「{booking.place_name}」有已录入的预订，不能删除、替换或跨日移动；请先处理预订记录')


def record_booking(plan, body):
    result = plan.model_copy(deep=True)
    day = next((d for d in result.days if d.date == body.day_date.isoformat()), None)
    place = next((p for p in day.attractions if p.item_id == body.item_id), None) if day else None
    if not place: raise BookingConflict('该景点已不在这个日期，请重新打开')
    old = active_booking(result, day, place)
    if body.booking_id and (not old or old.id != body.booking_id): raise BookingConflict('预订记录已变化，请重新打开')
    if old and not body.booking_id: raise BookingConflict('该景点已有有效记录，请编辑原记录，避免重复记账')
    if not old and len(result.ticket_bookings) >= 100: raise BookingConflict('当前行程的预订记录已达到上限')
    booking = TicketBooking(id=old.id if old else uuid4().hex, day_date=day.date, item_id=place.item_id,
        place_fingerprint=fingerprint(place), place_name=place.name, ticket_name=body.ticket_name,
        participants=body.participants, paid_total=body.paid_total, source_label=body.source_label,
        fixed_start_time=place.fixed_start_time, entry_window=place.entry_window,
        recorded_at=datetime.now(timezone.utc).isoformat())
    result.ticket_bookings = [b for b in result.ticket_bookings if not old or b.id != old.id] + [booking]
    result.budget = None
    return result


def cancel_booking(plan, booking_id, refunded_total, clear_time=False):
    result = plan.model_copy(deep=True)
    booking = next((b for b in result.ticket_bookings if b.id == booking_id), None)
    if not booking: raise BookingConflict('预订记录不存在')
    if refunded_total > booking.paid_total or round(refunded_total, 2) != refunded_total:
        raise BookingConflict('已退款金额不得超过实付总额，且最多保留两位小数')
    if clear_time:
        for day in result.days:
            for place in day.attractions:
                if matches(booking, day, place):
                    if (place.fixed_start_time, place.entry_window) != (booking.fixed_start_time, booking.entry_window):
                        raise BookingConflict('入场时间已另行修改，请取消勾选清除时间，之后手动核对')
                    place.fixed_start_time, place.entry_window = None, None
                    # This changes a constraint, so discard the old conclusion.
                    day.schedule = None
    booking.status, booking.refunded_total = 'cancelled', refunded_total
    booking.recorded_at = datetime.now(timezone.utc).isoformat()
    result.budget = None
    return result
