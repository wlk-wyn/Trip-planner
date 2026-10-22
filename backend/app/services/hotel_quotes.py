"""User-supplied dated room references; no supplier availability is implied."""
from datetime import date,datetime,timezone
from pydantic import BaseModel,Field,model_validator
from ..models.schemas import HotelNightQuote


class HotelQuoteInput(BaseModel):
    night_date: str = Field(pattern=r'^\d{4}-\d{2}-\d{2}$')
    hotel_item_id: str = Field(min_length=1,max_length=200)
    price_per_room: float | None = Field(default=None,ge=0,le=1000000,allow_inf_nan=False,strict=True)
    room_type: str = Field(default='',max_length=80)
    source_label: str = Field(default='',max_length=120)
    taxes_included: bool | None = Field(default=None,strict=True)

    @model_validator(mode='after')
    def conditions(self):
        date.fromisoformat(self.night_date)
        self.room_type=self.room_type.strip()
        if self.price_per_room is not None and not self.room_type:raise ValueError('请填写报价对应的房型')
        return self


def apply_hotel_quotes(plan,quotes):
    result=plan.model_copy(deep=True)
    first,last=date.fromisoformat(plan.start_date),date.fromisoformat(plan.end_date)
    seen=set()
    for quote in quotes:
        night=date.fromisoformat(quote.night_date)
        if not first<=night<last:raise ValueError('报价日期必须是出发日至返程日前一晚')
        if night in seen:raise ValueError('同一晚不能提交两份报价')
        seen.add(night)
        day=next((d for d in result.days if d.date==quote.night_date),None)
        if not day or not day.hotel or day.hotel.item_id!=quote.hotel_item_id:raise ValueError('当晚酒店已改变，请重新打开费用页')
        hotel=day.hotel
        hotel.nightly_quote=None if quote.price_per_room is None else HotelNightQuote(
            night_date=quote.night_date,hotel_place_id=hotel.place_id,price_per_room=quote.price_per_room,
            room_type=quote.room_type,source_label=quote.source_label.strip(),taxes_included=quote.taxes_included,
            recorded_at=datetime.now(timezone.utc).isoformat())
    result.budget=None
    return result


def applicable_quote(hotel,night):
    quote=hotel.nightly_quote
    return quote if quote and quote.night_date==night and quote.hotel_place_id==hotel.place_id else None


def retain_hotel_quotes(plan,previous=None):
    """Model-generated plans cannot create or alter user-supplied quotations."""
    for day in plan.days:
        if day.hotel:
            old=next((d.hotel for d in previous.days if d.date==day.date and d.hotel and d.hotel.place_id==day.hotel.place_id),None) if previous else None
            day.hotel.nightly_quote=old.nightly_quote.model_copy(deep=True) if old and old.nightly_quote else None
