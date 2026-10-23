"""数据模型定义"""

from typing import Annotated, List, Literal, Optional, Union
from pydantic import BaseModel, Field, field_validator, model_validator
from datetime import date
import math
from uuid import NAMESPACE_URL, uuid5


# ============ 请求模型 ============

class ReferenceSource(BaseModel):
    id: str = Field(..., max_length=80)
    type: Literal["text", "url", "screenshot"] = "text"
    title: str = Field(default="攻略", max_length=120)
    url: str = Field(default="", max_length=2000)
    text: str = Field(..., min_length=1, max_length=15000)


class ReferencePlace(BaseModel):
    poi_id: str = ""
    name: str
    address: str = ""
    longitude: float
    latitude: float


class ReferenceCandidate(BaseModel):
    id: str
    name: str
    source_ids: List[str] = Field(default_factory=list)
    priority: Literal["required", "optional", "ignored"] = "optional"
    status: Literal["matched", "ambiguous", "unmatched"] = "unmatched"
    place: Optional[ReferencePlace] = None
    options: List[ReferencePlace] = Field(default_factory=list, max_length=5)


class ReferenceAnalysisRequest(BaseModel):
    city: str = Field(..., min_length=1, max_length=80)
    sources: List[ReferenceSource] = Field(..., min_length=1, max_length=12)

TIME_PATTERN = r"^([01][0-9]|2[0-3]):[0-5][0-9]$"


class TripCostContext(BaseModel):
    budget_mode: Literal['active', 'deferred'] = 'active'
    party_size: Optional[int] = Field(default=None, strict=True, ge=1, le=50, description="同行人数；门票交通按成人参考口径，优惠另核实")
    room_count: Optional[int] = Field(default=None, strict=True, ge=1, le=50, description="每晚房间数")
    destination_budget: Optional[float] = Field(default=None, ge=0, le=10000000, allow_inf_nan=False, description="全体在目的地内的人民币参考预算，不含往返大交通")


class TripRequest(TripCostContext):
    """旅行规划请求"""
    city: str = Field(..., description="目的地城市", example="北京")
    start_date: str = Field(..., description="开始日期 YYYY-MM-DD", example="2025-06-01")
    end_date: str = Field(..., description="结束日期 YYYY-MM-DD", example="2025-06-03")
    travel_days: int = Field(..., description="旅行天数", ge=1, le=30, example=3)
    transportation: str = Field(..., description="交通方式", example="公共交通")
    accommodation: str = Field(..., description="住宿偏好", example="经济型酒店")
    daily_start_time: str = Field(default="09:00", pattern=TIME_PATTERN)
    daily_end_time: str = Field(default="20:00", pattern=TIME_PATTERN)
    buffer_minutes: int = Field(default=15, ge=0, le=60)
    preferences: List[str] = Field(default=[], description="旅行偏好标签", example=["历史文化", "美食"])
    free_text_input: Optional[str] = Field(default="", description="额外要求", example="希望多安排一些博物馆")
    reference_type: Optional[str] = Field(default="none", description="参考来源类型: none/url/screenshot")
    reference_content: Optional[str] = Field(default="", description="参考内容(小红书攻略文本)")
    reference_mode: Optional[str] = Field(default="flexible", description="参考模式: strict(严格)/hybrid(小红书为主AI补充)/flexible(AI自主)")
    reference_sources: List[ReferenceSource] = Field(default_factory=list, max_length=12)
    reference_candidates: List[ReferenceCandidate] = Field(default_factory=list, max_length=40)

    class Config:
        json_schema_extra = {
            "example": {
                "city": "北京",
                "start_date": "2025-06-01",
                "end_date": "2025-06-03",
                "travel_days": 3,
                "transportation": "公共交通",
                "accommodation": "经济型酒店",
                "preferences": ["历史文化", "美食"],
                "free_text_input": "希望多安排一些博物馆",
                "reference_type": "none",
                "reference_content": ""
            }
        }


class POISearchRequest(BaseModel):
    """POI搜索请求"""
    keywords: str = Field(..., description="搜索关键词", example="故宫")
    city: str = Field(..., description="城市", example="北京")
    citylimit: bool = Field(default=True, description="是否限制在城市范围内")


class RouteRequest(BaseModel):
    """路线规划请求"""
    model_config = {'str_strip_whitespace':True}
    origin_address: str = Field(..., min_length=1, max_length=300, description="起点地址", example="北京市朝阳区阜通东大街6号")
    destination_address: str = Field(..., min_length=1, max_length=300, description="终点地址", example="北京市海淀区上地十街10号")
    origin_city: Optional[str] = Field(default=None, max_length=80, description="起点城市")
    destination_city: Optional[str] = Field(default=None, max_length=80, description="终点城市")
    route_type: Literal['walking','driving','transit'] = Field(default="walking", description="路线类型: walking/driving/transit")


# ============ 响应模型 ============

class Location(BaseModel):
    """地理位置"""
    longitude: float = Field(..., description="经度")
    latitude: float = Field(..., description="纬度")


class FactEvidence(BaseModel):
    source: Literal['amap', 'model', 'user', 'official']
    recorded_at: str
    poi_id: str = ''
    value_hash: str = Field(pattern=r'^[a-f0-9]{64}$')
    source_url: Optional[str] = None
    scope: Optional[str] = None
    applies_on: Optional[str] = None
    document_hash: Optional[str] = Field(default=None, pattern=r'^[a-f0-9]{64}$')


class CostedPlace(BaseModel):
    poi_id: Optional[str] = ""
    parent_poi_id: Optional[str] = Field(default=None, max_length=80)
    parent_evidence: Optional[FactEvidence] = None
    item_id: str = ""
    place_id: str = ""
    locked: bool = False

    @model_validator(mode="after")
    def identify_place(self):
        identity = self.poi_id
        if identity:
            self.place_id = "amap:" + identity
        else:
            loc = getattr(self, "location", None)
            key = f"{getattr(self, 'restaurant', None) or getattr(self, 'name', '')}|{getattr(self, 'address', '')}|{loc}"
            self.place_id = "legacy:" + uuid5(NAMESPACE_URL, key).hex
        return self

    cost_status: Literal["unknown", "estimated", "confirmed"] = "unknown"
    cost_source: str = ""
    # Opening hours text as published on Amap; not an official guarantee.
    open_hours: Optional[str] = None
    hours_evidence: Optional[FactEvidence] = None
    price_evidence: Optional[FactEvidence] = None

    @model_validator(mode="before")
    @classmethod
    def migrate_cost(cls, raw):
        if not isinstance(raw, dict):
            return raw
        raw = dict(raw)
        key = "ticket_price" if "ticket_price" in cls.model_fields else "estimated_cost"
        amount = raw.get(key)
        if "cost_status" not in raw:
            raw["cost_status"] = "estimated" if amount is not None and float(amount) > 0 else "unknown"
        if raw.get("cost_status") == "unknown":
            raw[key] = None
        elif amount is None:
            raw["cost_status"] = "unknown"
        return raw


class EntryWindow(BaseModel):
    """User supplied same-day entry window; end is the latest permitted entry."""
    start: str = Field(pattern=TIME_PATTERN)
    end: str = Field(pattern=TIME_PATTERN)

    @model_validator(mode='after')
    def ordered(self):
        if self.end <= self.start:
            raise ValueError('最晚入场时间必须晚于最早入场时间')
        return self


class Attraction(CostedPlace):
    """景点信息"""
    name: str = Field(..., description="景点名称")
    address: str = Field(..., description="地址")
    location: Location = Field(..., description="经纬度坐标")
    visit_duration: int = Field(..., ge=1, le=1440, description="建议游览时间(分钟)")
    description: str = Field(..., description="景点描述")
    category: Optional[str] = Field(default="景点", description="景点类别")
    rating: Optional[float] = Field(default=None, description="评分")
    photos: Optional[List[str]] = Field(default_factory=list, description="景点图片URL列表")
    poi_id: Optional[str] = Field(default="", description="POI ID")
    image_url: Optional[str] = Field(default=None, description="图片URL")
    ticket_price: Optional[float] = Field(default=None, ge=0, description="门票价格，未知为null")
    fixed_start_time: Optional[str] = Field(default=None, pattern=TIME_PATTERN)
    entry_window: Optional[EntryWindow] = None

    @property
    def has_time_constraint(self):
        return bool(self.fixed_start_time or self.entry_window)

    @model_validator(mode='after')
    def one_time_constraint(self):
        if self.fixed_start_time and self.entry_window:
            raise ValueError('固定时间与入场时段不能同时设置')
        return self


class Meal(CostedPlace):
    """餐饮信息"""
    route_matched: bool = Field(default=False, description="由系统按初稿路线自动匹配，用于新行程修复")
    type: Literal["breakfast", "lunch", "dinner", "snack"] = Field(..., description="餐饮类型: breakfast/lunch/dinner/snack")
    name: str = Field(..., description="餐饮/美食名称")
    restaurant: Optional[str] = Field(default=None, description="餐厅名称")
    category: Optional[str] = Field(default=None, description="地图地点类别，用于自动餐厅选择，非接待保证")
    address: Optional[str] = Field(default=None, description="餐厅地址")
    location: Optional[Location] = Field(default=None, description="餐厅经纬度坐标")
    description: Optional[str] = Field(default=None, description="描述/推荐理由")
    recommended_dish: Optional[str] = Field(default=None, description="推荐菜品")
    image_url: Optional[str] = Field(default=None, description="美食/餐厅图片URL")
    estimated_cost: Optional[float] = Field(default=None, ge=0, description="餐费，未知为null")


class HotelNightQuote(BaseModel):
    night_date: str = Field(pattern=r'^\d{4}-\d{2}-\d{2}$')
    hotel_place_id: str = Field(min_length=1,max_length=200)
    price_per_room: float = Field(ge=0,le=1000000,allow_inf_nan=False,strict=True)
    room_type: str = Field(min_length=1,max_length=80)
    source_label: str = Field(default='',max_length=120)
    taxes_included: Optional[bool] = Field(default=None,strict=True)
    recorded_at: str


class Hotel(CostedPlace):
    """酒店信息"""
    name: str = Field(..., description="酒店名称")
    address: str = Field(default="", description="酒店地址")
    location: Optional[Location] = Field(default=None, description="酒店位置")
    price_range: str = Field(default="", description="价格范围")
    rating: str = Field(default="", description="评分")
    distance: str = Field(default="", description="距离景点距离")
    type: str = Field(default="", description="酒店类型")
    estimated_cost: Optional[float] = Field(default=None, ge=0, description="每晚费用，未知为null")
    nightly_quote: Optional[HotelNightQuote] = None


class ScheduleItem(BaseModel):
    id: str
    kind: Literal["attraction", "meal", "hotel"]
    name: str
    duration_minutes: int
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    fixed: bool = False


class RouteLeg(BaseModel):
    """One step of a queried route: a walk, a subway/bus ride, a rail ride or a drive."""
    kind: Literal["walk", "subway", "bus", "rail", "drive"]
    line: str = ""
    direction: str = ""
    from_stop: str = ""
    to_stop: str = ""
    stops: Optional[int] = None
    entrance: str = ""
    exit: str = ""
    distance_meters: Optional[int] = None
    duration_minutes: Optional[int] = None
    alternatives: List[str] = Field(default_factory=list)


class RouteEndpoint(BaseModel):
    name: str
    poi_id: str = ''
    location: Location
    basis: Literal['place_coordinate', 'reviewed_gate'] = 'place_coordinate'
    role: Literal['location', 'entrance', 'exit'] = 'location'
    venue_poi_id: str = ''
    observed_at: Optional[str] = None
    policy_source_url: Optional[str] = None
    policy_reviewed_on: Optional[str] = None


class RouteSegment(BaseModel):
    from_id: str
    to_id: str
    from_name: str
    to_name: str
    mode: str
    status: Literal["verified", "unverified"]
    duration_minutes: Optional[int] = None
    distance_meters: Optional[int] = None
    checked_at: Optional[str] = None
    note: str = ""
    # Detail of the chosen Amap plan; fares are per adult at query time.
    fare: Optional[float] = None
    taxi_cost: Optional[float] = None
    legs: List[RouteLeg] = Field(default_factory=list)
    polyline: str = ""
    from_endpoint: Optional[RouteEndpoint] = None
    to_endpoint: Optional[RouteEndpoint] = None


class DaySchedule(BaseModel):
    status: Literal["feasible", "conflict", "unverified"]
    window_start: str
    window_end: str
    minimum_minutes: int
    available_minutes: int
    items: List[ScheduleItem] = Field(default_factory=list)
    segments: List[RouteSegment] = Field(default_factory=list)
    conflicts: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    solver_status: str = ""
    solver_note: str = ""
    route_queries: int = 0


class DayPlan(BaseModel):
    """单日行程"""
    date: str = Field(..., description="日期 YYYY-MM-DD")
    day_index: int = Field(..., description="第几天(从0开始)")
    description: str = Field(..., description="当日行程描述")
    transportation: str = Field(..., description="交通方式")
    accommodation: str = Field(..., description="住宿")
    start_time: Optional[str] = Field(default=None, pattern=TIME_PATTERN)
    end_time: Optional[str] = Field(default=None, pattern=TIME_PATTERN)
    buffer_minutes: Optional[int] = Field(default=None, ge=0, le=60)
    schedule: Optional[DaySchedule] = None
    visit_order: List[str] = Field(default_factory=list, max_length=80)
    planned_start_times: dict[str, Annotated[str, Field(pattern=TIME_PATTERN)]] = Field(default_factory=dict, max_length=80)
    hotel: Optional[Hotel] = Field(default=None, description="推荐酒店")
    attractions: List[Attraction] = Field(default=[], description="景点列表")
    meals: List[Meal] = Field(default=[], description="餐饮列表")

    @model_validator(mode="after")
    def identify_visits(self):
        seen = set()
        for kind, places in [("attraction", self.attractions), ("meal", self.meals), ("hotel", [self.hotel] if self.hotel else [])]:
            for index, place in enumerate(places):
                if not place.item_id:
                    place.item_id = uuid5(NAMESPACE_URL, f"{self.date}|{kind}|{place.place_id}|{index}").hex
                if place.item_id in seen:
                    raise ValueError("同一天的访问标识重复")
                seen.add(place.item_id)
        return self



class WeatherInfo(BaseModel):
    """天气信息"""
    date: str = Field(..., description="日期 YYYY-MM-DD")
    day_weather: str = Field(default="", description="白天天气")
    night_weather: str = Field(default="", description="夜间天气")
    day_temp: Optional[float] = Field(default=None, description="白天温度；未知为null")
    night_temp: Optional[float] = Field(default=None, description="夜间温度；未知为null")
    wind_direction: str = Field(default="", description="风向")
    wind_power: str = Field(default="", description="风力")
    source: Optional[Literal['amap','model','user']] = None
    observed_at: Optional[str] = None
    report_time: Optional[str] = None
    adcode: Optional[str] = None

    @field_validator('day_temp', 'night_temp', mode='before')
    @classmethod
    def parse_temperature(cls, v):
        """解析温度,移除°C等单位"""
        if isinstance(v, str):
            # 移除°C, ℃等单位符号
            v = v.replace('°C', '').replace('℃', '').replace('°', '').strip()
        try:
            number=float(v)
            return number if not isinstance(v,bool) and math.isfinite(number) and -100<=number<=100 else None
        except (TypeError,ValueError):
            return None


class Budget(BaseModel):
    """预算信息"""
    total_attractions: int = Field(default=0, description="景点门票总费用")
    total_hotels: int = Field(default=0, description="酒店总费用")
    total_meals: int = Field(default=0, description="餐饮总费用")
    total_transportation: int = Field(default=0, description="交通总费用")
    total: int = Field(default=0, description="总费用")


class TicketBooking(BaseModel):
    id: str
    day_date: str
    item_id: str
    place_fingerprint: str
    place_name: str
    ticket_name: str = Field(min_length=1, max_length=120)
    participants: int = Field(ge=1, le=100, strict=True)
    paid_total: float = Field(ge=0, le=1000000, allow_inf_nan=False, strict=True)
    refunded_total: float = Field(default=0, ge=0, le=1000000, allow_inf_nan=False, strict=True)
    status: Literal['recorded', 'cancelled'] = 'recorded'
    source_label: str = Field(default='', max_length=80)
    fixed_start_time: Optional[str] = Field(default=None, pattern=TIME_PATTERN)
    entry_window: Optional[EntryWindow] = None
    recorded_at: str

    @model_validator(mode='after')
    def refund_limit(self):
        if self.refunded_total > self.paid_total:
            raise ValueError('已退款金额不能超过实付总额')
        return self


class TripPlan(BaseModel):
    """旅行计划"""
    city: str = Field(..., description="目的地城市")
    start_date: str = Field(..., description="开始日期")
    end_date: str = Field(..., description="结束日期")
    days: List[DayPlan] = Field(..., description="每日行程")
    weather_info: List[WeatherInfo] = Field(default=[], description="天气信息")
    overall_suggestions: str = Field(..., description="总体建议")
    adjustment_notes: List[str] = Field(default_factory=list, max_length=100, description="服务端完成的规划调整说明，独立于模型原建议")
    budget: Optional[Budget] = Field(default=None, description="预算信息")
    ticket_bookings: List[TicketBooking] = Field(default_factory=list, max_length=100)


class TripPlanResponse(BaseModel):
    """旅行计划响应"""
    success: bool = Field(..., description="是否成功")
    message: str = Field(default="", description="消息")
    data: Optional[TripPlan] = Field(default=None, description="旅行计划数据")


class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(..., max_length=4000)


class TripIntakeRequest(BaseModel):
    """新建行程时的自然语言对话。"""
    history: List[ChatTurn] = Field(..., min_length=1, max_length=12)


class TripIntakeData(TripCostContext):
    ready: bool
    reply: str
    city: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    travel_days: Optional[int] = None
    transportation: Optional[str] = None
    accommodation: Optional[str] = None
    preferences: List[str] = Field(default_factory=list)
    free_text_input: str = ""
    budget_amount: Optional[float] = None
    budget_basis: Optional[Literal['party', 'person']] = None
    budget_coverage: Optional[Literal['destination', 'whole_trip']] = None
    budget_clarification: bool = False


class TripIntakeResponse(BaseModel):
    success: bool
    message: str = ""
    data: Optional[TripIntakeData] = None


class TripChatRequest(BaseModel):
    plan: TripPlan
    message: str = Field(..., min_length=1, max_length=1500)
    history: List[ChatTurn] = Field(default_factory=list, max_length=20)
    original_request: Optional[TripRequest] = None


class TripChatData(BaseModel):
    reply: str
    plan: TripPlan
    changed: bool
    actions: List[str] = Field(default_factory=list)


class TripChatResponse(BaseModel):
    success: bool
    message: str = ""
    data: Optional[TripChatData] = None


class POIInfo(BaseModel):
    """POI信息"""
    id: str = Field(..., description="POI ID")
    name: str = Field(..., description="名称")
    type: str = Field(..., description="类型")
    address: str = Field(..., description="地址")
    location: Location = Field(..., description="经纬度坐标")
    tel: Optional[str] = Field(default=None, description="电话")
    photos: List[str] = Field(default_factory=list)
    source: Literal['amap'] = 'amap'
    observed_at: Optional[str] = None


class POISearchResponse(BaseModel):
    """POI搜索响应"""
    success: bool = Field(..., description="是否成功")
    message: str = Field(default="", description="消息")
    data: List[POIInfo] = Field(default=[], description="POI列表")


class RouteInfo(BaseModel):
    """路线信息"""
    distance: float = Field(..., description="距离(米)")
    duration: int = Field(..., description="时间(秒)")
    route_type: str = Field(..., description="路线类型")
    description: str = Field(..., description="路线描述")
    requested_route_type: Optional[str] = None
    source: str = 'amap'
    queried_at: Optional[str] = None
    origin: Optional[dict] = None
    destination: Optional[dict] = None
    legs: List[dict] = Field(default_factory=list)
    polyline: str = ''


class RouteResponse(BaseModel):
    """路线规划响应"""
    success: bool = Field(..., description="是否成功")
    message: str = Field(default="", description="消息")
    data: Optional[RouteInfo] = Field(default=None, description="路线信息")


class WeatherResponse(BaseModel):
    """天气查询响应"""
    success: bool = Field(..., description="是否成功")
    message: str = Field(default="", description="消息")
    data: List[WeatherInfo] = Field(default=[], description="天气信息")


# ============ 错误响应 ============

class ErrorResponse(BaseModel):
    """错误响应"""
    success: bool = Field(default=False, description="是否成功")
    message: str = Field(..., description="错误消息")
    error_code: Optional[str] = Field(default=None, description="错误代码")



class PlanOperation(BaseModel):
    model_config = {"extra": "forbid"}
    action: Literal["add", "replace", "remove", "reorder", "update_visit", "update_day", "move_visit"]
    date: str
    target_date: str = ""
    kind: Literal["attraction", "meal", "hotel"] = "attraction"
    item_id: str = ""
    poi_id: str = ""
    order: List[str] = Field(default_factory=list, max_length=40)
    meal_type: Literal["breakfast", "lunch", "dinner", "snack"] = "snack"
    visit_duration: Optional[int] = Field(default=None, ge=1, le=1440)
    start_time: Optional[str] = Field(default=None, pattern=TIME_PATTERN)
    end_time: Optional[str] = Field(default=None, pattern=TIME_PATTERN)
    buffer_minutes: Optional[int] = Field(default=None, ge=0, le=60)


class PlanPatch(BaseModel):
    model_config = {"extra": "forbid"}
    operations: List[PlanOperation] = Field(..., min_length=1, max_length=20)
