# 智能旅行规划助手 - 改进计划

> 创建日期: 2026-07-29
> 当前阶段: 效率优化 Phase 1
> 
> 本文档记录项目的整体改进规划，涵盖效率、功能、UI 三大方向。

---

## 📋 改进路线图

```
Phase 1: 效率优化（当前）
├── P0 ✅ 并行化搜索节点
├── P1 ✅ 添加缓存层
├── P2 📋 简化 ReAct 循环
├── P3 🔧 结构化输出
├── P4 📡 SSE 流式响应
└── P5 ⚡ MCP 连接优化

Phase 2: 功能增强
├── F1 实时交通状况整合
├── F2 景点人流密度预测
├── F3 智能行李打包建议
├── F4 旅行预算智能追踪
└── F5 多语言语音导览

Phase 3: UI/UX 优化
├── U1 拖拽式行程编辑器
├── U2 动画过渡效果
├── U3 响应式移动端适配
└── U4 离线 PWA 支持
```

---

## Phase 1: 效率优化

### P0 - 并行化搜索节点 ✅

| 项目 | 详情 |
|------|------|
| **状态** | ✅ 已完成 |
| **分支** | `feature/p0-parallel-search` |
| **提交** | `caf0c5c`, `f36eae5` |
| **预计提速** | 30-50% |
| **文档** | [P0-PARALLEL-SEARCH.md](P0-PARALLEL-SEARCH.md) |

**改动摘要**:
- 添加 `_str_reducer` 支持并行分支状态合并
- 重构 `_build_graph` 为 fan-out/fan-in 并行模式
- 预先初始化 MCP 客户端避免竞态条件

---

### P1 - 添加缓存层 ✅

| 项目 | 详情 |
|------|------|
| **状态** | ✅ 已完成 |
| **优先级** | 高 |
| **实际提速** | 22.4%（冷缓存 68.3s → 热缓存 53.0s） |
| **实施难度** | ⭐ 低 |

**改进方案**:
```python
# 简单 TTL 缓存实现
import time
_cache = {}

def cached_search(city: str, keywords: str, ttl: int = 43200):
    key = f"{city}:{keywords}"
    if key in _cache and time.time() - _cache[key]['time'] < ttl:
        return _cache[key]['data']
    # ... 实际搜索 ...
    _cache[key] = {'data': result, 'time': time.time()}
    return result
```

**缓存策略**:
- 天气缓存 TTL: 4 小时
- POI 缓存 TTL: 12 小时
- 酒店缓存 TTL: 12 小时

**参考**: FloatTrip 的 Redis 缓存层实现

---

### P2 - 简化 ReAct 循环 📋

| 项目 | 详情 |
|------|------|
| **状态** | 🔲 待开始 |
| **优先级** | 中 |
| **预计提速** | 15-20% |
| **实施难度** | ⭐⭐ 中 |

**改进方案**:
- **天气查询**: 直接调用 `maps_weather` API（单次调用，无需 ReAct）
- **酒店搜索**: 直接调用 `maps_text_search` API（单次调用）
- **景点搜索**: 保留简化版 ReAct（最多 2 轮循环）

**对比**:
```python
# 当前: 每个搜索都用 ReAct（2-3 次 LLM 调用）
# 改进后: 
#   天气/酒店 → 直接 API 调用（0 次 LLM）
#   景点 → 简化 ReAct（最多 2 轮）
```

---

### P3 - 结构化输出 🔧

| 项目 | 详情 |
|------|------|
| **状态** | 🔲 待开始 |
| **优先级** | 中 |
| **影响** | 稳定性大幅提升 |
| **实施难度** | ⭐⭐ 中 |

**问题**:
- 当前 `_parse_plan_json` 有 200+ 行四级兜底解析代码
- LLM 返回 JSON 格式不稳定导致解析失败

**改进方案**:
```python
from pydantic import BaseModel, Field
from typing import List

class AttractionOutput(BaseModel):
    name: str
    address: str
    longitude: float
    latitude: float
    duration_minutes: int = 120

class DayPlanOutput(BaseModel):
    day_index: int
    attractions: List[AttractionOutput]
    # ...

# 使用结构化输出
llm_with_schema = llm.with_structured_output(DayPlanOutput)
result = await llm_with_schema.ainvoke(messages)
```

**收益**:
- 消除 200+ 行脆弱的解析代码
- 彻底解决 JSON 格式不稳定问题
- 减少 token 消耗（无需重试）

---

### P4 - SSE 流式响应 📡

| 项目 | 详情 |
|------|------|
| **状态** | 🔲 待开始 |
| **优先级** | 中 |
| **影响** | 用户体验提升 |
| **实施难度** | ⭐⭐ 中 |

**改进方案**:
```python
from sse_starlette.sse import EventSourceResponse

@router.post("/plan/stream")
async def plan_trip_stream(request: TripRequest):
    async def event_generator():
        yield {"event": "progress", "data": "开始搜索景点..."}
        # ... 执行各阶段 ...
        yield {"event": "progress", "data": "景点搜索完成"}
        yield {"event": "progress", "data": "天气查询完成"}
        yield {"event": "complete", "data": trip_plan}

    return EventSourceResponse(event_generator())
```

**前端配合**:
- 实时显示各阶段进度
- 改善加载等待体验
- 支持取消进行中的请求

---

### P5 - MCP 连接优化 ⚡

| 项目 | 详情 |
|------|------|
| **状态** | 🔲 待开始 |
| **优先级** | 低 |
| **预计提速** | 5-10% |
| **实施难度** | ⭐⭐⭐ 高 |

**改进方案**:
- 应用启动时预初始化 MCP 连接池
- 连接复用避免重复握手
- 添加健康检查和自动重连机制

---

## Phase 2: 功能增强规划

### F1 - 实时交通状况整合
- 引入实时交通数据 API
- 动态调整出发时间和交通方式
- 智能避开拥堵路线

### F2 - 景点人流密度预测
- 分析历史参观数据
- 生成"人流热力图"
- 推荐最佳游览时段

### F3 - 智能行李打包建议
- 基于目的地天气预测
- 根据行程类型生成清单
- 支持重量估算

### F4 - 旅行预算智能追踪
- 实时消费追踪
- 对比预算与实际支出
- 超支提醒与省钱建议

### F5 - 多语言语音导览
- 离线语音包下载
- GPS 定位触发讲解
- 支持语速调节

---

## Phase 3: UI/UX 优化规划

### U1 - 拖拽式行程编辑器
- 支持景点拖拽排序
- 跨天移动景点
- 实时路线计算

### U2 - 动画过渡效果
- 卡片展开动画
- 地图标记动画
- 进度条动画

### U3 - 响应式移动端适配
- 移动端专属布局
- 触摸手势优化
- 安全区域适配

### U4 - 离线 PWA 支持
- Service Worker 缓存
- 离线地图瓦片
- 本地数据持久化

---

## 📊 技术决策记录

| 决策 | 当前选择 | 改进方向 |
|------|----------|----------|
| 并行执行 | 串行 | fan-out/fan-in 并行 |
| 缓存策略 | 无 | 内存 TTL → Redis |
| LLM 输出 | 原始文本 | Function Calling |
| 响应方式 | 同步等待 | SSE 流式 |
| MCP 初始化 | 惰性加载 | 预初始化 + 连接池 |

---

## 🔗 参考项目

| 项目 | Stars | 参考价值 |
|------|-------|----------|
| [TREK](https://github.com/liketrek/TREK) | 11k | 实时协作、PWA、完整功能 |
| [FloatTrip](https://github.com/shouzhuoshouzhuo/FloatTrip) | 34 | LangGraph 多 Agent、Redis 缓存 |
| [TravelPlanner](https://github.com/OSU-NLP-Group/TravelPlanner) | 学术 | 规划算法、评估体系 |
| [Multi-Agent-AI-Travel-Advisor](https://github.com/kbhujbal/Multi-Agent-AI-Travel-Advisor) | 55 | CrewAI 多 Agent、RAG |

---

## 📝 变更日志

| 日期 | 版本 | 变更内容 |
|------|------|----------|
| 2026-07-29 | v1.0 | 创建改进计划文档 |
| 2026-07-29 | v1.0 | 完成 P0 并行化优化 |
| 2026-07-29 | v1.1 | 完成 P1 缓存层优化 |

---

> **下一步**: 开始 P2 简化 ReAct 循环
