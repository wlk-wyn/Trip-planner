"""FastAPI主应用"""

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from ..config import get_settings, validate_config, print_config
from .routes import trip, poi, saved_trips, map as map_routes
from ..services.request_gate import configure_request_gate

# 获取配置
settings = get_settings()

@asynccontextmanager
async def lifespan(_app: FastAPI):
    """启动时恢复未完成任务并校验配置；关闭时停止任务、释放高德连接。"""
    from ..services.planning_runs import start_recovery, shutdown_runs
    from ..services.amap_rest import close_client
    from ..services.schedule_validator import close_route_queries
    print("\n" + "="*60)
    print(f"🚀 {settings.app_name} v{settings.app_version}")
    print("="*60)
    print_config()
    try:
        validate_config()
        print("\n✅ 配置验证通过")
    except ValueError as e:
        print(f"\n❌ 配置验证失败:\n{e}")
        print("\n请检查.env文件并确保所有必要的配置项都已设置")
        raise
    print("\n" + "="*60)
    print("📚 API文档: http://localhost:8000/docs")
    print("📖 ReDoc文档: http://localhost:8000/redoc")
    print("="*60 + "\n")
    start_recovery()
    try:
        yield
    finally:
        await shutdown_runs()
        from ..services.meituan_travel import close_queries
        await close_queries()
        await close_route_queries()
        await close_client()
    print("\n" + "="*60)
    print("👋 应用正在关闭...")
    print("="*60 + "\n")


# 创建FastAPI应用
app = FastAPI(
    lifespan=lifespan,
    title=settings.app_name,
    version=settings.app_version,
    description="基于 LangGraph Agent 的智能旅行规划助手 API",
    docs_url="/docs",
    redoc_url="/redoc"
)
configure_request_gate(app, settings)

# 配置CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.get_cors_origins_list(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Retry-After"],
)

# 注册路由
app.include_router(trip.router, prefix="/api")
app.include_router(saved_trips.router, prefix="/api")
app.include_router(poi.router, prefix="/api")
app.include_router(map_routes.router, prefix="/api")


@app.get("/")
async def root():
    """根路径"""
    return {
        "name": settings.app_name,
        "version": settings.app_version,
        "status": "running",
        "docs": "/docs",
        "redoc": "/redoc"
    }


@app.get("/health")
async def health():
    """健康检查"""
    return {
        "status": "healthy",
        "service": settings.app_name,
        "version": settings.app_version
    }


@app.get('/ready', summary='本地存储与任务执行器就绪检查，不探测外部服务')
async def readiness():
    from ..services.planning_runs import readiness as local_readiness
    from ..services.provider_limits import readiness as limiter_readiness
    report = await local_readiness()
    if report['status'] == 'ready':
        coordinator = await limiter_readiness()
        report['provider_coordinator'] = 'available' if coordinator else 'unavailable'
        if not coordinator:
            report['status'] = 'not_ready'
    else:
        report['provider_coordinator'] = 'not_checked'
    try:
        await asyncio.to_thread(app.state.request_gate.probe)
        report['http_resource_coordinator'] = 'available'
    except Exception:
        report['http_resource_coordinator'] = 'unavailable'
        report['status'] = 'not_ready'
    return JSONResponse(report, status_code=200 if report['status'] == 'ready' else 503)


if __name__ == "__main__":
    import uvicorn
    
    uvicorn.run(
        "app.api.main:app",
        host=settings.host,
        port=settings.port,
        reload=True
    )

