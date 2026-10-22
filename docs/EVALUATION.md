# 评测与证据

本页记录验证方法和可追溯的历史结果，不随文档整理把旧成绩升级为当前全量结论。

## 三种结果

| 结论 | 含义 |
| --- | --- |
| 任务完成 | 后台任务完成并保存了结果 |
| 参考检查通过 | 符合该版本支持的地点、时间、预算和用户约束检查 |
| 实际可执行 | 还需要官方开放、预约、票种及必要的实际预订条件等证据 |

评测器位于 `backend/eval/run_eval.py`。真实生成流程覆盖持久任务、Agent、地点与交通、费用参考、保存和结果读取。评估脚本使用隔离存储，避免写入用户行程。

## 本地离线检查

后端在 `backend` 目录运行：

```powershell
.\venv\Scripts\python.exe -X utf8 -m unittest discover -s tests
```

前端在 `frontend` 目录运行，逐个执行避免环境限制测试子进程：

```powershell
$failures = 0
Get-ChildItem tests -Filter '*.test.mjs' | ForEach-Object {
    node $_.FullName
    if ($LASTEXITCODE -ne 0) { $failures++ }
}
if ($failures) { throw "$failures 个测试文件失败" }
npm run build
```

前端测试使用可直接运行 TypeScript 的 Node 环境；本地检查环境为 Node 24.15.0、Python 3.12.3。`npm run build` 包括类型检查、生产构建和包体积门槛。

## 真实生成评估

真实评估会使用配置中的模型与地图服务，可能产生接口费用，不作为普通离线检查的一部分。只跑一个公开案例的示例，在 `backend` 目录运行：

```powershell
.\venv\Scripts\python.exe -X utf8 -m eval.load_eval --repeat 1 --cases candidate-bj-history-2d --case-file eval/candidate_quality_cases.json --capture-schedules
```

报告生成在 `backend/eval/results/`。该脚本最多使用两个配置的任务执行槽；运行前核对配置与配额。它不是 HTTP 压测，单个案例不能证明 p95 或整体准确率。

## 已保留的检查结果

| 时间和范围 | 结果 | 限制 |
| --- | --- | --- |
| 2026-10-19 闭馆调整阶段收尾 | 后端全量 582 项、前端 75 项通过；正式构建和包体积检查通过 | 后续又有改动，不代表全部当前代码重新完成全量验证 |
| 后续营业时间与排程专项 | 70 项相关测试通过 | 不替代全量和真实生成验收 |
| 后续模型输入投影专项 | 18 项相关测试通过 | 离线重复查询文本变短不等于实际 token 同比例降低 |
| 后续任务跟踪专项 | 17 项相关前端测试及类型检查通过 | 使用受控故障，不是公网长时间运行结果 |

阶段说明和原始统计保存在[原验收记录](archive/2026-10/PRODUCTION_READINESS_2026-10-16.md)。其中测试数量是当时的检查范围，不应相加成累计覆盖率。

## 真实样本与回放

2026-10-19 的公开北京两日样本分别观察到 24.80 秒、24.41 秒；两份内容不同，不能作为严格的前后性能对照。它们也暴露了迟午餐、闭馆时间漏报和票种范围待确认。

同一冻结样本的离线回放用原已查询路段验证修复：午餐可从 15:31 调至 11:30；另一份样本中的恭王府从 15:56—17:37 调至 15:25—17:00。回放没有新模型或地图请求，不是重新完整生成。

本地原始报告与回放包括 `load-20261019-164806.json`、`load-20261019-165227.json`、`meal-closing-replay-20261019.json`、`weekly-hours-replay-20261019.json`，位于 `backend/eval/results/`。文件可能属于本地生成产物；历史附件索引见[归档](archive/README.md)。

## 尚需验证

继续检查代表性城市、不同天数、明确偏好、冷暖缓存及故障情形。性能比较必须同时说明代码版本、模型、供应商 QPS、缓存条件和样本数；离线测试、真实单次生成、HTTP 负载和公网可用性分别报告。
