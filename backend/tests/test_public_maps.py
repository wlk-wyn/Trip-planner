import json
import tempfile
import unittest
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4
import httpx
from fastapi import FastAPI

from app.api.routes import map as maps, poi, saved_trips
from app.services import amap_rest as rest
from app.services.map_queries import grounded_weather
from app.services.fact_evidence import reconcile_manual_facts
from app.services.trip_prep import build_prep
from app.services.trip_repository import TripRepository
from app.models.schemas import WeatherInfo, TripPlan
from app.agents.langgraph_planner import LangGraphTripPlanner
from test_travel_agent import PLAN, REQUEST


class PublicMapTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.patches=[patch.object(rest,name,OrderedDict()) for name in ('_poi_cache','_region_cache','_geocode_cache')]
        for item in self.patches:item.start()
        self.calls=[]
        self.raw_pois={'status':'1','pois':[{'id':'p1','name':'示例博物馆','type':'科教文化','address':'示例路1号','location':'116.4,39.9','tel':'010-00000000','photos':[{'url':'https://example.com/photo.jpg'}]}]}
        self.cast={'date':'2026-10-18','dayweather':'小雨','nightweather':'阴','daytemp':'0','nighttemp':None,'daywind':'北','daypower':'1-3'}
        self.report=(datetime.now(timezone.utc)-timedelta(minutes=30)).isoformat()
        async def transport(request):
            self.calls.append((request.url.path,dict(request.url.params)))
            path=request.url.path
            if path.endswith('place/text') or path.endswith('place/detail'):return httpx.Response(200,json=self.raw_pois)
            if path.endswith('config/district'):return httpx.Response(200,json={'status':'1','districts':[{'adcode':'110000','citycode':'010','name':'北京市','level':'province'}]})
            if path.endswith('weather/weatherInfo'):return httpx.Response(200,json={'status':'1','forecasts':[{'city':'北京市','adcode':'110000','reporttime':self.report,'casts':[self.cast]}]})
            if path.endswith('geocode/geo'):
                first=request.url.params['address']=='起点'
                return httpx.Response(200,json={'status':'1','geocodes':[{'location':'116.4,39.9' if first else '116.5,39.9','level':'兴趣点','formatted_address':'北京市示例地点','city':'北京市','province':'北京市','adcode':'110101'}]})
            if '/direction/' in path:return httpx.Response(200,json={'status':'1','route':{'paths':[{'distance':'1234','duration':'121','steps':[]}],'transits':[{'distance':'2345','duration':'321','segments':[]}]}})
            raise AssertionError(path)
        self.http=httpx.AsyncClient(transport=httpx.MockTransport(transport))
        self.settings=patch.object(rest,'get_settings',return_value=SimpleNamespace(amap_api_key='synthetic'))
        self.settings.start()
        self.client_patch=patch.object(rest,'_shared_client',return_value=self.http);self.client_patch.start()
        self.poi_pace=patch.object(rest,'_pace_poi_request',new=AsyncMock());self.paced_poi=self.poi_pace.start()
        self.route_pace=patch.object(rest,'_pace_route_request',new=AsyncMock());self.paced_route=self.route_pace.start()
        self.meta_pace=patch.object(rest,'_pace_metadata_request',new=AsyncMock());self.paced_meta=self.meta_pace.start()
        app=FastAPI()
        for router in (maps.router,poi.router,saved_trips.router):app.include_router(router,prefix='/api')
        self.temp=tempfile.TemporaryDirectory();self.repo=TripRepository(Path(self.temp.name)/'trips.sqlite3')
        app.dependency_overrides[saved_trips.repository]=lambda:self.repo
        self.api=httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test',headers={'X-Trip-Owner':str(uuid4())})

    async def asyncTearDown(self):
        await self.api.aclose();await self.http.aclose()
        for item in [self.meta_pace,self.route_pace,self.poi_pace,self.client_patch,self.settings,*reversed(self.patches)]:item.stop()
        self.temp.cleanup()

    async def test_both_search_routes_return_real_identity_coordinates_and_observation(self):
        for path in ('/api/map/poi','/api/poi/search'):
            response=await self.api.get(path,params={'city':'北京','keywords':'博物馆'})
            self.assertEqual(response.status_code,200)
            place=response.json()['data'][0]
            self.assertEqual((place['id'],place['name'],place['location']['longitude']),('p1','示例博物馆',116.4))
            self.assertEqual(place['source'],'amap');self.assertTrue(place['observed_at'])
        self.assertEqual(self.paced_poi.await_count,1,'the second endpoint reuses the same cache')

    async def test_city_limit_false_has_its_own_cache_and_is_sent_to_provider(self):
        await self.api.get('/api/map/poi',params={'city':'北京','keywords':'馆'})
        await self.api.get('/api/map/poi',params={'city':'北京','keywords':'馆','citylimit':'false'})
        self.assertEqual([params['citylimit'] for _,params in self.calls],['true','false'])

    async def test_detail_returns_typed_fields_photos_and_missing_is_404(self):
        response=await self.api.get('/api/poi/detail/p1')
        self.assertEqual(response.status_code,200);self.assertEqual(response.json()['data']['photos'],['https://example.com/photo.jpg'])
        self.raw_pois={'status':'1','pois':[]}
        missing=await self.api.get('/api/poi/detail/absent')
        self.assertEqual(missing.status_code,404)

    async def test_no_matches_is_explicit_but_malformed_coordinates_are_an_error(self):
        self.raw_pois={'status':'1','pois':[]}
        empty=await self.api.get('/api/map/poi',params={'city':'北京','keywords':'无匹配'})
        self.assertEqual(empty.status_code,200);self.assertEqual(empty.json()['data'],[])
        self.assertIn('未找到',empty.json()['message'])
        for location in ('nan,39','200,39','116,100'):
            self.raw_pois={'status':'1','pois':[{'id':'bad','name':'无效','location':location}]}
            invalid=await self.api.get('/api/map/poi',params={'city':'北京','keywords':location})
            self.assertEqual(invalid.status_code,502)

    async def test_weather_resolves_adcode_and_keeps_zero_unknown_and_report_evidence(self):
        response=await self.api.get('/api/map/weather',params={'city':'北京'})
        self.assertEqual(response.status_code,200)
        forecast=response.json()['data'][0]
        self.assertEqual((forecast['day_temp'],forecast['night_temp'],forecast['adcode']),(0,None,'110000'))
        self.assertEqual(forecast['report_time'],self.report);self.assertTrue(forecast['observed_at'])
        self.assertEqual(self.calls[-1][1]['city'],'110000');self.assertEqual(self.paced_meta.await_count,2)

    async def test_direct_adcode_avoids_district_lookup_and_empty_forecast_is_explicit(self):
        response=await self.api.get('/api/map/weather',params={'city':'110000'})
        self.assertEqual(response.status_code,200);self.assertEqual(len(self.calls),1)
        with patch.object(rest,'_get_json',AsyncMock(return_value={'forecasts':[]})):
            response=await self.api.get('/api/map/weather',params={'city':'110000'})
        self.assertEqual(response.json()['data'],[]);self.assertIn('没有',response.json()['message'])

    async def test_unknown_or_ambiguous_region_does_not_query_weather(self):
        for districts in ([],[{'name':'朝阳区','adcode':'110105','level':'district'},{'name':'朝阳市','adcode':'211300','level':'city'}]):
            with patch.object(rest,'_get_json',AsyncMock(return_value={'districts':districts})) as query:
                response=await self.api.get('/api/map/weather',params={'city':'朝阳'})
                self.assertEqual(response.status_code,422);self.assertEqual(query.await_count,1)

    async def test_stale_missing_or_future_forecast_publication_is_not_reliable(self):
        for report in (None,(datetime.now(timezone.utc)-timedelta(days=2)).isoformat(),(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat()):
            self.report=report
            response=await self.api.get('/api/map/weather',params={'city':'110000'})
            self.assertEqual(response.status_code,502)

    async def test_forecast_mismatched_region_or_bad_calendar_is_rejected(self):
        for changes in ({'adcode':'310000'},{'casts':[{'date':'2026-02-30'}]}):
            data={'forecasts':[{'adcode':'110000','reporttime':self.report,'casts':[self.cast],**changes}]}
            with patch.object(rest,'_get_json',AsyncMock(return_value=data)):
                response=await self.api.get('/api/map/weather',params={'city':'110000'})
                self.assertEqual(response.status_code,502)

    async def test_address_route_preserves_provider_seconds_and_query_provenance(self):
        response=await self.api.post('/api/map/route',json={'origin_address':'起点','destination_address':'终点','origin_city':'北京','route_type':'walking'})
        self.assertEqual(response.status_code,200)
        route=response.json()['data']
        self.assertEqual((route['duration'],route['distance'],route['route_type']),(121,1234,'walking'))
        self.assertTrue(route['queried_at']);self.assertEqual(route['origin']['level'],'兴趣点')
        self.assertEqual(self.paced_meta.await_count,2);self.assertEqual(self.paced_route.await_count,1)

    async def test_ambiguous_or_coarse_address_never_becomes_a_route_endpoint(self):
        for geocodes in ([],[{'level':'市','location':'116.4,39.9'}],[{},{}]):
            with patch.object(rest,'_get_json',AsyncMock(return_value={'geocodes':geocodes})) as query:
                response=await self.api.post('/api/map/route',json={'origin_address':'起点','destination_address':'终点'})
                self.assertEqual(response.status_code,422);self.assertEqual(query.await_count,1)

    async def test_cross_city_transit_sends_both_resolved_cities(self):
        result=await rest.get_route(SimpleNamespace(longitude=116.4,latitude=39.9),SimpleNamespace(longitude=117.2,latitude=39.1),'北京','transit',destination_city='天津')
        self.assertEqual(result['duration_seconds'],321)
        self.assertEqual((self.calls[-1][1]['city'],self.calls[-1][1]['cityd']),('北京','天津'))

    async def test_requested_transit_fallback_is_labelled_as_actual_walking(self):
        result=await rest.get_route(SimpleNamespace(longitude=116.4,latitude=39.9),SimpleNamespace(longitude=116.401,latitude=39.9),'北京','transit')
        self.assertEqual(result['mode'],'walking');self.assertIn('步行',result['note'])

    async def test_provider_failure_is_not_an_empty_success_and_does_not_leak_details(self):
        for error,code in ((rest.AMapServiceError('10021','地图限流',True),503),(rest.AMapServiceError('invalid_response','secret=do-not-expose'),502),(rest.AMapServiceError('configuration','服务尚未配置'),503)):
            with patch.object(rest,'_get_json',AsyncMock(side_effect=error)):
                response=await self.api.get('/api/poi/search',params={'keywords':'错误场景','city':'北京'})
                self.assertEqual(response.status_code,code)
                self.assertNotIn('do-not-expose',response.text)
                if error.retryable:self.assertEqual(response.headers['Retry-After'],'3')

    async def test_request_constraints_reject_blank_address_and_unsupported_mode(self):
        for body in ({'origin_address':' ','destination_address':'终点'},{'origin_address':'起点','destination_address':'终点','route_type':'fly'}):
            response=await self.api.post('/api/map/route',json=body);self.assertEqual(response.status_code,422)
        self.assertEqual(self.calls,[])

    async def test_nested_place_cache_is_not_mutated_by_a_caller(self):
        first=await rest.search_pois('北京','博物馆');stamp=first[0]['observed_at']
        first[0]['coordinates'][0]=0;first[0]['photos'].clear()
        second=await rest.search_pois('北京','博物馆')
        self.assertEqual(second[0]['coordinates'][0],116.4);self.assertEqual(len(second[0]['photos']),1)
        self.assertEqual(second[0]['observed_at'],stamp)

    async def test_unknown_temperature_is_not_zero_and_explicit_zero_is_kept(self):
        for value in (None,'',[],True,'不详','NaN',float('inf'),'1000'):
            self.assertIsNone(WeatherInfo(date='2026-10-18',day_temp=value).day_temp)
        self.assertEqual(WeatherInfo(date='2026-10-18',day_temp='0℃').day_temp,0)
        self.assertEqual(WeatherInfo(date='2026-10-18',day_temp='-3.5°C').day_temp,-3.5)

    async def test_grounding_discards_model_forecast_and_only_uses_tool_dates(self):
        plan=TripPlan(**PLAN);plan.days=[]
        plan.weather_info=[WeatherInfo(date=plan.start_date,day_weather='模型编造的晴天',day_temp=30)]
        planner=object.__new__(LangGraphTripPlanner)
        planner._ground_selected_places(plan,{})
        self.assertEqual(plan.weather_info,[])
        rows=[{**self.cast,'date':plan.start_date,'source':'amap','report_time':self.report,'observed_at':datetime.now(timezone.utc).isoformat()}]
        self.assertEqual(grounded_weather({'weather_info':json.dumps(rows)},plan.start_date,plan.end_date)[0].day_temp,0)

    async def test_imported_or_manually_posted_forecast_cannot_claim_supplier_evidence(self):
        plan=TripPlan(**PLAN)
        plan.weather_info=[WeatherInfo(date=plan.start_date,day_weather='晴',source='amap',observed_at=self.report,report_time=self.report)]
        response=await self.api.post('/api/trips',json={'request':REQUEST.model_dump(),'plan':plan.model_dump()})
        self.assertEqual(response.status_code,200)
        saved=TripPlan(**response.json()['plan']);self.assertEqual(saved.weather_info[0].source,'user')
        changed=saved.model_copy(deep=True);changed.weather_info=plan.weather_info
        reconcile_manual_facts(changed,saved)
        self.assertEqual(changed.weather_info[0].source,'user')

    async def test_packing_uses_an_explicit_numeric_zero_temperature(self):
        plan=TripPlan(**PLAN)
        prep=build_prep(plan,{plan.start_date:{'date':plan.start_date,'daytemp':0,'nighttemp':None}})
        self.assertIn('保暖外套',str(prep))

    async def test_photos_require_the_named_place_and_never_substitute_stock_photos(self):
        self.raw_pois['pois'][0]['name']='附近另一家博物馆'
        response=await self.api.get('/api/poi/photo',params={'name':'示例博物馆','city':'北京'})
        self.assertEqual(response.status_code,200);self.assertIsNone(response.json()['data']['photo_url'])
        self.raw_pois['pois'][0]['name']='示例博物馆'
        response=await self.api.get('/api/poi/photo',params={'name':'示例 博物馆','city':'北京'})
        self.assertEqual(response.json()['data']['photo_url'],'https://example.com/photo.jpg')

    async def test_real_monday_failure_snapshot_no_longer_passes_reference_checks(self):
        from eval.run_eval import summarize
        source=Path(__file__).resolve().parent/'fixtures/monday_failure.json'
        row=json.loads(source.read_text(encoding='utf-8'))
        plan=TripPlan(**row['plan_snapshot'])
        request=type(REQUEST)(**row['case_request'])
        # No route queries or changes to the original report: re-assess saved facts.
        result=summarize(plan,request,row['case_expect'])
        self.assertFalse(result['schedule_passed']);self.assertFalse(result['reference_checks_passed'])
        self.assertGreaterEqual(result['opening_hours_conflicts'],3)

    async def test_automatic_breakfast_rejects_lodging_and_a_weekday_excluded_restaurant(self):
        from app.services.restaurant_candidates import suitable_restaurant
        plan=TripPlan(**PLAN);day=plan.days[0];day.date='2026-10-19';day.meals=[]
        anchor=day.attractions[0].location
        def candidate(name,id,hours):
            return {'name':name,'poi_id':id,'address':'公开测试地址','coordinates':[anchor.longitude,anchor.latitude],
                    'category':'餐饮服务;餐饮相关场所;餐饮相关','open_hours':hours}
        rows=[candidate('北京京韵四合院(北池子大街分店)','lodging',None),candidate('宫廷餐厅','closed','周二至周日08:30-15:30'),candidate('全日早餐铺','food','周一至周日07:00-11:00')]
        unfilled=LangGraphTripPlanner._place_nearby_meals([(day,'breakfast',anchor)],[rows],set(),2)
        self.assertEqual(unfilled,[]);self.assertEqual(day.meals[0].poi_id,'food')
        self.assertTrue(suitable_restaurant('四合院私房菜','lunch','餐饮服务;中餐厅'))

    async def test_weekday_exclusion_is_enforced_by_schedule_validation(self):
        from app.services.schedule_validator import validate_schedule
        plan=TripPlan(**PLAN);plan.start_date=plan.end_date=plan.days[0].date='2026-10-19'
        plan.days[0].attractions[0].open_hours='周二至周日09:00-17:00'
        request=REQUEST.model_copy(update={'start_date':plan.start_date,'end_date':plan.end_date})
        await validate_schedule(plan,request,AsyncMock(return_value={'duration_minutes':10,'distance_meters':500}))
        self.assertEqual(plan.days[0].schedule.status,'conflict')
        self.assertTrue(any('未覆盖当天' in issue for issue in plan.days[0].schedule.conflicts))
