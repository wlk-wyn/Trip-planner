"""Local-only busy-state UI fixture; never run as the application server."""
from contextlib import AsyncExitStack, asynccontextmanager
from uuid import uuid4
from app.api.routes import trip
from app.config import get_settings
from app.models.schemas import TripIntakeData
from app.services.request_gate import configure_request_gate
from . import frontend_preview as base

app=base.app
settings=get_settings().model_copy(update={'http_interactive_running':2,'http_interactive_per_peer_running':2})
configure_request_gate(app,settings,path=base.ROOT/'var'/f'frontend-resource-{uuid4().hex}.sqlite3')
gate=app.state.request_gate
audit={'fixture':'synthetic-busy-state-no-model','intake_calls':0}


async def fake_intake(_history):
    audit['intake_calls']+=1
    return TripIntakeData(ready=False,reply='合成验收，不调用模型')


trip.interpret_trip_intake=fake_intake


@asynccontextmanager
async def lifetime(_app):
    async with base.lifetime(_app), AsyncExitStack() as stack:
        for i in range(2):await stack.enter_async_context(gate.permit('interactive',f'synthetic-load-{i}'))
        yield


app.router.lifespan_context=lifetime


@app.get('/resource-preview-audit')
def resource_audit():
    db=gate.connect()
    try:count=db.execute("SELECT COUNT(*) FROM http_leases WHERE bucket='interactive'").fetchone()[0]
    finally:db.close()
    return {**audit,'held_interactive_slots':count}
