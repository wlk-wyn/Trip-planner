"""LLM结构化输出Schema - 用于with_structured_output

这些模型专门为LLM生成行程设计，字段命名简洁，
便于LLM准确生成符合结构的JSON。
"""

from typing import List, Optional, Literal
from pydantic import BaseModel, Field


class LLMCoordinates(BaseModel):
    """坐标"""
    longitude: float = Field(description="经度")
    latitude: float = Field(description="纬度")


class LLMAttraction(BaseModel):
    """LLM输出的景点信息"""
    name: str = Field(description="景点名称")
    address: str = Field(description="景点地址")
    coordinates: LLMCoordinates = Field(description="景点经纬度")
    duration_minutes: int = Field(description="建议游览时长(分钟)", ge=30, le=480)
    time: str = Field(description="活动时间段,如'09:30-11:30'", pattern=r'\d{2}:\d{2}-\d{2}:\d{2}')
    description: str = Field(description="景点简短描述")
    category: str = Field(default="景点", description="景点类别")
    ticket_price: int = Field(default=0, description="门票价格(元)")
    rating: Optional[float] = Field(default=None, description="评分")


class LLMMeal(BaseModel):
    """LLM输出的餐饮信息"""
    meal_type: Literal["breakfast", "lunch", "dinner"] = Field(description="餐饮类型")
    restaurant: str = Field(description="餐厅名称")
    address: str = Field(description="餐厅地址")
    coordinates: LLMCoordinates = Field(description="餐厅经纬度")
    recommended_dishes: List[str] = Field(default_factory=list, description="推荐菜名列表")
    description: str = Field(description="推荐理由")
    estimated_cost: int = Field(description="人均消费(元)", ge=0)
    image_url: Optional[str] = Field(default=None, description="餐厅图片URL(可选)")


class LLMHotel(BaseModel):
    """LLM输出的酒店信息"""
    name: str = Field(description="酒店名称")
    address: str = Field(description="酒店地址")
    coordinates: LLMCoordinates = Field(description="酒店经纬度")
    price_range: str = Field(description="价格范围,如'300-500元'")
    rating: str = Field(description="酒店评分")


class LLMDayPlan(BaseModel):
    """LLM输出的单日行程"""
    day_index: int = Field(description="第几天(从0开始)", ge=0)
    date: str = Field(description="日期 YYYY-MM-DD", pattern=r'\d{4}-\d{2}-\d{2}')
    description: str = Field(description="当日行程描述")
    attractions: List[LLMAttraction] = Field(min_length=2, description="景点列表(至少2个)")
    meals: List[LLMMeal] = Field(min_length=3, max_length=3, description="早中晚三餐")


class LLMTripPlan(BaseModel):
    """LLM输出的完整旅行计划"""
    city: str = Field(description="目的地城市")
    start_date: str = Field(description="开始日期 YYYY-MM-DD")
    end_date: str = Field(description="结束日期 YYYY-MM-DD")
    overall_suggestions: str = Field(description="总体旅行建议")
    days: List[LLMDayPlan] = Field(min_length=1, description="每日行程列表")
    hotel: Optional[LLMHotel] = Field(default=None, description="推荐酒店(可选)")
