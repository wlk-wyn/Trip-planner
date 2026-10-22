"""Read-only public UI fixture; disable every external-query-capable API."""
import re
from fastapi.responses import JSONResponse
from .frontend_preview import app


@app.middleware('http')
async def public_readonly_preview(request,call_next):
    path=request.url.path
    if path.startswith('/api/') and (request.method!='GET' or not re.fullmatch(
            r'/api/trips(?:/[^/]+(?:/(?:checks|versions|prep))?)?',path)):
        return JSONResponse({'detail':'独立公开快照验收已禁用外部查询及编辑。'},status_code=503)
    return await call_next(request)
