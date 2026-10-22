"""Isolated API for production-bundle UI verification, seeded with public data.

Run only on localhost. It never opens the user's trip database and does not
recover background jobs. Every browser owner gets its own public sample trip.
"""
import json
import os
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI
from app.api.routes import saved_trips, trip, poi, map as map_routes
from app.models.schemas import TripPlan, TripRequest
from app.services.trip_repository import TripRepository
from app.services.planning_runs import shutdown_runs
from app.services.amap_rest import close_client
from app.services.schedule_validator import close_route_queries
from contextlib import asynccontextmanager

ROOT=Path(__file__).resolve().parents[1]
source=(ROOT/'eval/results'/os.getenv('FRONTEND_PREVIEW_REPORT','run-20261016-220706-214304.json')).resolve()
if source.parent!=(ROOT/'eval/results').resolve():raise ValueError('UI fixture report must be inside eval/results')
records=json.loads(source.read_text(encoding='utf-8'))
if isinstance(records,dict):records=records['rows']
row=next(r for r in records if r['id']==os.getenv('FRONTEND_PREVIEW_CASE','bj-history-2d'))

class PreviewRepository(TripRepository):
    def list(self, owner):
        existing=super().list(owner)
        if not existing:
            self.create(owner,TripRequest(**row['case_request']).model_dump(),TripPlan(**row['plan_snapshot']).model_dump(),[])
        return super().list(owner)

repo=PreviewRepository(ROOT/'var'/f'frontend-preview-{uuid4().hex}.sqlite3')

@asynccontextmanager
async def lifetime(_app):
    try:yield
    finally:
        await shutdown_runs()
        await close_route_queries()
        await close_client()

app=FastAPI(lifespan=lifetime)
for router in (saved_trips.router,trip.router,poi.router,map_routes.router):app.include_router(router,prefix='/api')
app.dependency_overrides[saved_trips.repository]=lambda:repo

@app.get('/health')
def health():return {'status':'healthy','fixture':'public-snapshot-only'}

@app.get('/ready')
def ready():return {'status':'ready','fixture':'isolated-ui-preview','background_recovery':False}
