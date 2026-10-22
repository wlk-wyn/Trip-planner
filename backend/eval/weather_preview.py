"""Real forecast display on an isolated, retimed public snapshot.

Only a display fixture: routes are deliberately cleared, never revalidated or
represented as a newly generated trip. The user's saved DB is never opened.
"""
import json
from datetime import date, timedelta
from uuid import uuid4
from app.models.schemas import TripPlan, TripRequest, WeatherInfo
from app.services.trip_repository import TripRepository
from . import frontend_preview as base

app=base.app
receipt=json.loads((base.ROOT/'eval/results/public-map-20261018-024318.json').read_text(encoding='utf-8'))
forecasts=next(row['response']['data'] for row in receipt['rows'] if row['case']=='public-weather' and row['http_status']==200)
if base.row['case_request']['city']!='北京':raise ValueError('Use the public Beijing case for Beijing forecast evidence')


class WeatherRepository(TripRepository):
    def list(self,owner):
        existing=super().list(owner)
        if not existing:
            plan=TripPlan(**base.row['plan_snapshot'])
            start=date.fromisoformat(forecasts[0]['date'])
            plan.start_date=start.isoformat();plan.end_date=(start+timedelta(days=len(plan.days)-1)).isoformat()
            for i,day in enumerate(plan.days):
                day.date=(start+timedelta(days=i)).isoformat();day.schedule=None
            plan.weather_info=[WeatherInfo(**item) for item in forecasts]
            plan.budget=None
            plan.overall_suggestions='天气展示验收：使用实际查询的北京短期预报。公开行程快照仅调整演示日期，路线已清除，不能作为重新生成或可执行行程。'
            plan.adjustment_notes=[]
            request=TripRequest(**base.row['case_request']).model_copy(update={'start_date':plan.start_date,'end_date':plan.end_date})
            self.create(owner,request.model_dump(),plan.model_dump(),[])
        return super().list(owner)


base.repo=WeatherRepository(base.ROOT/'var'/f'weather-preview-{uuid4().hex}.sqlite3')
