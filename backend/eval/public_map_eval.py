"""Public Beijing POIs/addresses only; real provider queries through API adapters.

No saved-trip DB, model generation or UI mutation. Receipt is not planning load.
"""
import asyncio
import json
import logging
import time
from datetime import datetime
from pathlib import Path
import httpx
from fastapi import FastAPI
from app.api.routes import map as maps, poi
from app.services.amap_rest import close_client


async def run():
    app=FastAPI();app.include_router(maps.router,prefix='/api');app.include_router(poi.router,prefix='/api')
    rows=[]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://public-map-eval') as client:
        async def query(name,method,path,**kwargs):
            started=time.monotonic()
            try:
                response=await client.request(method,path,**kwargs)
                row={'case':name,'http_status':response.status_code,'seconds':round(time.monotonic()-started,3),'response':response.json()}
            except Exception as error:
                row={'case':name,'error_type':type(error).__name__,'seconds':round(time.monotonic()-started,3)}
            rows.append(row);return row
        try:
            search=await query('public-poi-search','GET','/api/map/poi',params={'keywords':'故宫博物院','city':'北京'})
            places=search.get('response',{}).get('data') or []
            if places:await query('public-poi-detail','GET','/api/poi/detail/'+places[0]['id'])
            await query('public-weather','GET','/api/map/weather',params={'city':'北京'})
            await query('public-address-walking','POST','/api/map/route',json={'origin_address':'北京市东城区天安门','destination_address':'北京市东城区故宫博物院','origin_city':'北京','destination_city':'北京','route_type':'walking'})
        finally:await close_client()
    return {'kind':'real-public-map-api-asgi-not-planning-load','saved_trip_writes':0,'model_calls':0,'rows':rows}


if __name__=='__main__':
    logging.getLogger('httpx').setLevel(logging.WARNING)
    logging.getLogger('httpcore').setLevel(logging.WARNING)
    receipt=asyncio.run(run())
    target=Path(__file__).resolve().parent/'results'/f"public-map-{datetime.now():%Y%m%d-%H%M%S}.json"
    target.write_text(json.dumps(receipt,ensure_ascii=False,indent=2),encoding='utf-8')
    print(target.name)
    print(json.dumps([{k:v for k,v in row.items() if k!='response'} for row in receipt['rows']],ensure_ascii=False))
