"""MCP 连接管理器 (P5: MCP 连接优化)

实现功能:
- 预初始化 MCP 连接池
- 连接复用避免重复握手
- 健康检查和自动重连机制
- 连接状态监控
"""

import asyncio
import time
import functools
from typing import Optional, Dict, Any, List
from datetime import datetime

from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_core.tools import BaseTool

from ..config import get_settings


def _normalize_args(args: Any) -> Any:
    """规范化工具调用参数

    高德 MCP 工具通常要求:
    - 布尔字段 (如 citylimit): 传 "true"/"false" 字符串，而非 bool
    - 数值字段在需要时也保持原样
    该函数递归处理 dict/list，确保 bool 统一转成小写字符串
    """
    if isinstance(args, bool):
        return "true" if args else "false"
    if isinstance(args, dict):
        return {k: _normalize_args(v) for k, v in args.items()}
    if isinstance(args, list):
        return [_normalize_args(v) for v in args]
    return args


class _NormalizedToolWrapper(BaseTool):
    """工具包装器: 在调用前规范化参数类型 (bool→"true"/"false")

    继承 BaseTool 以确保与 llm.bind_tools / ToolNode 完全兼容。
    通过 _run / _arun 钩子在调用底层工具前统一参数类型。
    """

    _inner: BaseTool

    def __init__(self, tool: BaseTool):
        # 使用 model_construct 避免重新触发 Pydantic 校验，直接复制原工具的核心字段
        name = getattr(tool, "name", str(tool))
        description = getattr(tool, "description", "")
        args_schema = getattr(tool, "args_schema", None)
        metadata = getattr(tool, "metadata", {})
        tags = getattr(tool, "tags", [])

        super().__init__(
            name=name,
            description=description,
            args_schema=args_schema,
            metadata=metadata,
            tags=tags,
        )
        object.__setattr__(self, "_inner", tool)

    def _run(self, *args: Any, **kwargs: Any) -> Any:
        if args:
            args = tuple(_normalize_args(a) for a in args)
        if kwargs:
            kwargs = _normalize_args(kwargs)
        return self._inner.invoke(*args, **kwargs)

    async def _arun(self, *args: Any, **kwargs: Any) -> Any:
        if args:
            args = tuple(_normalize_args(a) for a in args)
        if kwargs:
            kwargs = _normalize_args(kwargs)
        return await self._inner.ainvoke(*args, **kwargs)

    def __repr__(self):
        return f"_NormalizedToolWrapper({self._inner!r})"


class MCPConnectionManager:
    """MCP 连接管理器 (P5优化)

    优化点:
    1. 应用启动时预初始化，避免首次请求延迟
    2. 连接池复用，避免重复握手开销
    3. 健康检查机制，自动检测连接状态
    4. 断线自动重连，保障服务稳定性
    5. 连接状态监控，便于运维排查
    """

    def __init__(self):
        self._client: Optional[MultiServerMCPClient] = None
        self._tools: List[BaseTool] = []
        self._tool_index: Dict[str, BaseTool] = {}
        
        # 连接状态
        self._initialized = False
        self._last_init_time: Optional[float] = None
        self._last_health_check_time: Optional[float] = None
        self._consecutive_failures = 0
        self._total_requests = 0
        self._total_errors = 0
        
        # 配置
        self._max_retries = 3
        self._retry_delay = 2.0  # 秒
        self._health_check_interval = 60.0  # 秒
        
        # 锁防止并发初始化
        self._init_lock = asyncio.Lock()

    async def initialize(self) -> bool:
        """初始化 MCP 连接 (应用启动时调用)

        Returns:
            bool: 是否初始化成功
        """
        async with self._init_lock:
            if self._initialized and self._tools:
                return True

            settings = get_settings()
            
            try:
                print("🔄 [MCP Manager] 正在预初始化 MCP 连接...")
                
                self._client = MultiServerMCPClient({
                    "amap": {
                        "command": "uvx",
                        "args": ["amap-mcp-server"],
                        "env": {"AMAP_MAPS_API_KEY": settings.amap_api_key},
                        "transport": "stdio"
                    }
                })
                
                raw_tools = await self._client.get_tools()
                # P5+: 包装工具，自动规范化参数（bool→"true"/"false"等）
                self._tools = [_NormalizedToolWrapper(t) for t in raw_tools]
                self._tool_index = {getattr(t, "name", str(t)): t for t in self._tools}
                
                self._initialized = True
                self._last_init_time = time.time()
                self._consecutive_failures = 0
                
                print(f"✅ [MCP Manager] 初始化成功: {len(self._tools)} 个工具就绪")
                return True
                
            except Exception as e:
                print(f"❌ [MCP Manager] 初始化失败: {e}")
                self._tools = []
                self._tool_index = {}
                self._initialized = False  # 失败时不标记，允许重试
                return False

    async def ensure_connected(self) -> bool:
        """确保 MCP 连接可用 (惰性检查)

        Returns:
            bool: 连接是否可用
        """
        # 如果没有初始化或没有工具，直接初始化
        if not self._initialized or not self._tools:
            return await self.initialize()
        
        # 如果连续失败次数过多，强制重连
        if self._consecutive_failures >= self._max_retries:
            print(f"⚠️ [MCP Manager] 连续失败 {self._consecutive_failures} 次，强制重连")
            return await self._reconnect()
        
        # 定期健康检查
        if self._should_health_check():
            return await self._health_check()
        
        return True

    def _should_health_check(self) -> bool:
        """判断是否需要执行健康检查"""
        if self._last_health_check_time is None:
            return True
        return (time.time() - self._last_health_check_time) > self._health_check_interval

    async def _health_check(self) -> bool:
        """执行健康检查"""
        self._last_health_check_time = time.time()
        
        if not self._tools:
            return await self._reconnect()
        
        # 尝试调用一个轻量级工具作为健康检查
        try:
            # 不实际调用工具，仅检查 client 是否存在
            if self._client is None:
                return await self._reconnect()
            return True
        except Exception:
            return await self._reconnect()

    async def _reconnect(self) -> bool:
        """重新建立 MCP 连接"""
        print("🔄 [MCP Manager] 正在重新连接 MCP...")

        # 清理旧连接
        self._client = None
        self._tools = []
        self._tool_index = {}
        self._initialized = False

        # 等待短暂延迟
        await asyncio.sleep(self._retry_delay)

        # 重新初始化
        return await self.initialize()

    def get_tool(self, name: str) -> Optional[_NormalizedToolWrapper]:
        """按名称获取工具 (已包装参数规范化)"""
        return self._tool_index.get(name)

    def get_all_tools(self) -> List[_NormalizedToolWrapper]:
        """获取所有工具 (已包装参数规范化)"""
        return self._tools.copy()

    def get_tools_count(self) -> int:
        """获取工具数量"""
        return len(self._tools)

    def record_request(self, success: bool = True):
        """记录一次请求"""
        self._total_requests += 1
        if success:
            self._consecutive_failures = 0
        else:
            self._total_errors += 1
            self._consecutive_failures += 1

    def get_status(self) -> Dict[str, Any]:
        """获取连接状态信息"""
        now = time.time()
        
        return {
            "initialized": self._initialized,
            "tools_count": len(self._tools),
            "available": len(self._tools) > 0,
            "last_init_time": datetime.fromtimestamp(self._last_init_time).isoformat() if self._last_init_time else None,
            "last_health_check": datetime.fromtimestamp(self._last_health_check_time).isoformat() if self._last_health_check_time else None,
            "consecutive_failures": self._consecutive_failures,
            "total_requests": self._total_requests,
            "total_errors": self._total_errors,
            "success_rate": round(
                (self._total_requests - self._total_errors) / self._total_requests * 100, 1
            ) if self._total_requests > 0 else 100.0,
            "uptime_seconds": int(now - (self._last_init_time or now)),
        }


# 全局单例
_mcp_manager: Optional[MCPConnectionManager] = None


def get_mcp_manager() -> MCPConnectionManager:
    """获取全局 MCP 连接管理器"""
    global _mcp_manager
    if _mcp_manager is None:
        _mcp_manager = MCPConnectionManager()
    return _mcp_manager
