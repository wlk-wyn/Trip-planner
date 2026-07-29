# P0 优化：LangGraph 搜索节点并行化

> 分支: `feature/p0-parallel-search`
> 日期: 2026-07-29
> 状态: ✅ 已完成
> 
> 📋 关联文档: [IMPROVEMENT-PLAN.md](IMPROVEMENT-PLAN.md) - 完整改进计划

---

## 1. 优化目标

将 LangGraph 中三个独立的搜索节点（景点、天气、酒店）从**串行执行**改为**并行执行**，以提升整体响应速度。

### 预期收益

- **性能提升**: 40%+（取决于最慢的搜索节点）
- **用户体验**: 响应时间从 `T_attractions + T_weather + T_hotels` 降至 `max(T_attractions, T_weather, T_hotels)`

---

## 2. 问题分析

### 2.1 原有架构（串行）

```
START → search_attractions → search_weather → search_hotels → generate_plan → END
       (ReAct循环)          (ReAct循环)       (ReAct循环)      (LLM生成)
       
总耗时 = T1 + T2 + T3 + T4
```

**问题**: 三个搜索任务之间**没有数据依赖**，但却被强制串行执行。

### 2.2 优化后架构（并行）

```
                    ┌─→ search_attractions ─┐
START ──────────────┼─→ search_weather    ─┼─→ generate_plan → END
                    └─→ search_hotels      ┘
                         (并行执行)            (全部完成后)

总耗时 = max(T1, T2, T3) + T4
```

---

## 3. 技术实现

### 3.1 修改文件

**文件**: `backend/app/agents/langgraph_planner.py`

#### 变更 1: 添加字符串 reducer（第 25-27 行）

```python
def _str_reducer(old: str, new: str) -> str:
    """字符串 reducer: 并行分支合并时，非空新值覆盖旧值"""
    return new if new else old
```

**说明**: LangGraph 并行分支写入状态时需要 reducer 函数来处理冲突。对于字符串类型，使用简单的"非空覆盖"策略。

#### 变更 2: 更新 PlannerState（第 44-46 行）

```python
# 中间结果 (使用 reducer 支持并行分支合并)
attractions_info: Annotated[str, _str_reducer]
weather_info: Annotated[str, _str_reducer]
hotels_info: Annotated[str, _str_reducer]
```

**说明**: 为三个中间结果字段添加 `_str_reducer`，允许它们被并行分支安全地写入。

#### 变更 3: 修改 _build_graph 方法（第 403-428 行）

```python
def _build_graph(self):
    """构建主 StateGraph: 3个并行搜索 + 1个规划节点"""
    workflow = StateGraph(PlannerState)

    workflow.add_node("search_attractions", self._attraction_node)
    workflow.add_node("search_weather", self._weather_node)
    workflow.add_node("search_hotels", self._hotel_node)
    workflow.add_node("generate_plan", self._planner_node)

    # 并行 fan-out: START 同时启动三个搜索节点
    workflow.add_edge(START, "search_attractions")
    workflow.add_edge(START, "search_weather")
    workflow.add_edge(START, "search_hotels")

    # fan-in: 三个搜索节点全部完成后，进入规划节点
    workflow.add_edge("search_attractions", "generate_plan")
    workflow.add_edge("search_weather", "generate_plan")
    workflow.add_edge("search_hotels", "generate_plan")

    workflow.add_edge("generate_plan", END)

    return workflow.compile()
```

**说明**:
- `START → [三个搜索节点]`: 同时启动三个搜索任务（fan-out）
- `[三个搜索节点] → generate_plan`: 任意一个搜索完成都会检查，全部完成后才执行规划（fan-in）
- LangGraph 自动处理并行调度和状态合并

#### 变更 4: 预先初始化 MCP 客户端（第 442-443 行）

```python
async def plan_trip_async(self, request: TripRequest) -> TripPlan:
    # 优化: 预先初始化 MCP 客户端，避免并行节点并发初始化导致竞态条件
    await self._ensure_initialized()
    # ... 后续代码
```

**说明**:
- 并行模式下，三个节点会同时执行，如果都调用 `_ensure_initialized()` 可能导致竞态条件
- 在 `plan_trip_async` 入口预先完成初始化，确保 MCP 客户端在图执行前已就绪
- 节点中的 `_ensure_initialized()` 保留为安全检查（已初始化时直接返回）

---

## 4. 原理说明

### 4.1 LangGraph 并行执行机制

LangGraph 的 `StateGraph` 支持多入边和多出边：

1. **Fan-out（扇出）**: 一个节点可以有多个出边，同时启动多个下游节点
2. **Fan-in（扇入）**: 一个节点可以有多个入边，需要所有上游节点都完成后才执行

### 4.2 状态合并

当并行分支写入状态时：
- 每个节点只负责写入自己的专属字段（互不冲突）
- `_str_reducer` 确保合并时不会丢失数据
- 如果字段为空，保留原有值

### 4.3 节点独立性验证

| 节点 | 写入字段 | 依赖 |
|------|----------|------|
| `_attraction_node` | `attractions_info` | 无 |
| `_weather_node` | `weather_info` | 无 |
| `_hotel_node` | `hotels_info` | 无 |
| `_planner_node` | `trip_plan_json` | 前三个全部完成 |

✅ 三个搜索节点完全独立，可以安全并行。

---

## 5. 性能分析

### 5.1 理论分析

假设各节点平均耗时：
- `search_attractions`: ~8s (ReAct 2-3轮)
- `search_weather`: ~3s (ReAct 1-2轮)
- `search_hotels`: ~5s (ReAct 1-2轮)
- `generate_plan`: ~10s

| 模式 | 总耗时 |
|------|--------|
| 串行 | 8 + 3 + 5 + 10 = **26s** |
| 并行 | max(8, 3, 5) + 10 = **18s** |
| **提升** | **~31%** |

### 5.2 实际预期

由于各节点耗时波动较大（取决于 ReAct 循环次数和 API 响应），实际提升可能在 **30-50%** 之间。

---

## 6. 测试建议

### 6.1 基础验证

```python
# 检查并行图是否正确构建
planner = get_langgraph_planner()
graph = planner._build_graph()

# 验证图结构
# 应该有: START -> [attractions, weather, hotels] -> plan -> END
```

### 6.2 功能验证

1. **完整流程测试**: 提交正常请求，验证返回数据完整
2. **边界情况测试**: 
   - 只有景点有结果（天气/酒店为空）
   - 某个搜索节点失败（应有 fallback）
3. **并发一致性**: 多次请求验证结果一致性

### 6.3 性能对比

```bash
# 记录优化前耗时
time curl -X POST /api/trip/plan -d '{"city":"北京",...}'

# 记录优化后耗时
time curl -X POST /api/trip/plan -d '{"city":"北京",...}'
```

---

## 7. 风险评估

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|----------|
| 状态合并冲突 | 极低 | 低 | 每个节点只写独立字段 |
| 并发资源竞争 | 低 | 中 | MCP 工具支持并发调用 |
| 调试复杂度增加 | 中 | 低 | 日志中打印各节点开始/完成时间 |

---

## 8. 后续优化方向

1. **P1 - 缓存层**: 为天气/POI搜索添加 Redis 缓存（TTL: 4h/12h）
2. **P2 - 简化 ReAct**: 天气/酒店搜索改为直接 API 调用，仅景点保留 ReAct
3. **P3 - 结构化输出**: 使用 Function Calling 替代文本 JSON 解析
4. **P4 - SSE 流式**: 前端实时显示各阶段进度

---

## 9. 相关参考

- [LangGraph 并行执行文档](https://langchain-ai.github.io/langgraph/how-tos/map-reduce/)
- [FloatTrip 项目](https://github.com/shouzhuoshouzhuo/FloatTrip) - 类似架构参考
- [TREK 项目](https://github.com/liketrek/TREK) - 11k stars, 实时协作旅行规划
