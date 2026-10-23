"""Offline API acceptance journey in a disposable database; no provider calls.

This is a controlled fixture replay, not a real generation/performance report.
Run from backend: python -X utf8 -m eval.demo_journey --report var/demo-journey.json
"""
import argparse
import asyncio
import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from app.api.routes import saved_trips
from app.models.schemas import Attraction, DayPlan, Location, TripPlan, TripRequest
from app.services.trip_repository import TripRepository
from app.services.schedule_validator import validate_schedule
from app.services.entry_options import checked_options


async def fixture_validator(plan, request, **kwargs):
    # Deliberately synthetic transport, confined to a temporary acceptance DB.
    return await validate_schedule(plan, request, AsyncMock(return_value={'duration_minutes':20,'distance_meters':1000}))


def fixture():
    days=[]
    for day_index, count in enumerate((3,1)):
        day_date=f'2026-11-0{3+day_index}'
        attractions=[Attraction(name=f'离线演示景点{day_index+1}-{i+1}',address='受控样本，不是实际推荐',
            location=Location(longitude=116.4+i*.01+day_index*.05,latitude=39.9),visit_duration=90,
            description='用于验证修改、预订记账与恢复；不代表真实交通或营业信息。',ticket_price=60,cost_status='estimated') for i in range(count)]
        days.append(DayPlan(date=day_date,day_index=day_index,description='离线演示',transportation='公共交通',
                            accommodation='演示样本未设住宿',attractions=attractions))
    plan=TripPlan(city='北京',start_date=days[0].date,end_date=days[-1].date,days=days,overall_suggestions='受控离线验收样本')
    request=TripRequest(city=plan.city,start_date=plan.start_date,end_date=plan.end_date,travel_days=2,
        transportation='公共交通',accommodation='演示样本未设住宿',party_size=3,room_count=1,destination_budget=2000)
    return plan,request


async def run_journey():
    steps=[]
    with tempfile.TemporaryDirectory(prefix='trip-demo-') as folder:
        repo=TripRepository(Path(folder)/'trips.sqlite3')
        app=FastAPI();app.include_router(saved_trips.router,prefix='/api')
        app.dependency_overrides[saved_trips.repository]=lambda:repo
        owner='7fc300cc-e1f7-491a-a4c3-039f98edb7ed'
        async with AsyncClient(transport=ASGITransport(app=app),base_url='http://demo',headers={'X-Trip-Owner':owner}) as client:
            plan,request=fixture();await fixture_validator(plan,request)
            async def call(path,body,expected=200):
                response=await client.post(path,json=body)
                if response.status_code!=expected: raise AssertionError(f'{path}: {response.status_code} != {expected}')
                return response.json()
            saved=await call('/api/trips',{'plan':plan.model_dump(),'request':request.model_dump()})
            url=f"/api/trips/{saved['id']}"; target=plan.days[0].attractions[-1].item_id
            steps.append('从明确标注的离线样本创建隔离行程')
            initial=saved['version']
            edited=saved['plan'];edited['days'][1]['attractions'][0]['visit_duration']=75
            with patch.object(saved_trips,'validate_schedule',side_effect=fixture_validator):
                saved=await call(url+'/versions',{'base_version':saved['version'],'origin':'manual','plan':edited})
            steps.append('DIY 修改停留时长并保存版本')
            booking={'base_version':saved['version'],'day_date':plan.days[0].date,'item_id':target,'ticket_name':'离线样本成人票', 'participants':3,'paid_total':150.0,'source_label':'离线演示，不是平台订单'}
            preview=await call(url+'/ticket-booking-preview',booking)
            if round(preview['before_total']-preview['after_total'],2)!=30: raise AssertionError('paid total must replace 3 × 60, once')
            saved=await call(url+'/ticket-booking',booking);steps.append('预订总额替换对应人数参考费，避免重复计算')
            timing={'base_version':saved['version'],'day_date':plan.days[0].date,'item_id':target,'entry_window':{'start':'09:00','end':'12:00'},'allow_cross_day':True}
            entry=await call(url+'/entry-preview',timing)
            if entry['day']['schedule']['status']!='conflict': raise AssertionError('expected controlled appointment conflict')
            async def controlled_options(*args,**kwargs): return await checked_options(*args,**kwargs,validator=fixture_validator)
            with patch('app.services.entry_options.checked_options',side_effect=controlled_options):
                options=await call(url+'/entry-options',timing)
                selected=next(row for row in options['options'] if row['status']=='feasible')
                saved=await call(url+'/entry-option',{**timing,'selection_id':selected['id']})
            if len(saved['plan']['ticket_bookings'])!=1: raise AssertionError('repair lost the booking ledger')
            steps.append('发现预约冲突，核对可行换序并保存，保留预订记录')
            booking_id=saved['plan']['ticket_bookings'][0]['id']
            saved=await call(url+'/ticket-booking-cancel',{'base_version':saved['version'],'booking_id':booking_id,'refunded_total':100.0})
            report=await client.get(url+'/checks');report.raise_for_status()
            paid=[line for line in report.json()['cost_report']['lines'] if line.get('booking_id')==booking_id]
            if paid[0]['amount']!=50: raise AssertionError('unreturned money was silently removed')
            steps.append('记录取消与部分退款，未退金额继续计入费用')
            restored=await call(url+'/restore',{'base_version':saved['version'],'target_version':initial})
            if restored['plan']['ticket_bookings']: raise AssertionError('restore did not recover the original fixture')
            reread=await client.get(url);reread.raise_for_status()
            if reread.json()['version']!=restored['version']: raise AssertionError('reload lost restored version')
            steps.append('恢复初始版本并重新读取，验证持久化闭环')
    return {'mode':'offline_controlled','passed':True,'external_queries':0,'user_trips_modified':False,
            'limits':'受控交通与样本数据，仅验证 API/记账/版本流程；不代表真实生成、路线质量或公网可用性。','steps':steps}


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--report',type=Path)
    args=parser.parse_args();report=asyncio.run(run_journey())
    if args.report:
        args.report.parent.mkdir(parents=True,exist_ok=True)
        args.report.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(f"PASS: {len(report['steps'])} offline API journey steps; no external queries or user trip writes.")


if __name__=='__main__':main()
