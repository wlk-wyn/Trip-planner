# 本地运行与排错

以下操作以仓库根目录和 Windows PowerShell 为起点。当前项目没有经过公网部署验收。

## 配置

复制并填写 [backend/.env.example](../backend/.env.example) 和 [frontend/.env.example](../frontend/.env.example) 对应的 `.env`。已有配置不要覆盖。

- 后端：`AMAP_API_KEY`、`LLM_API_KEY`、`LLM_BASE_URL`、`LLM_MODEL_ID`；默认 `PLANNER_ENGINE=agent`。
- 前端：填写高德 JS API 相关配置；`VITE_API_BASE_URL` 支持同源接口，本地 Vite 代理到后端。
- QPS、模型超时、缓存、任务容量和 HTTP 容量由后端示例配置说明。不要超过账户实际可用额度。

密钥留在环境配置中，不写进文档、截图或提交记录。后端 Web Service 凭据与浏览器地图凭据按各自用途配置。

## 启动

后端：

```powershell
cd backend
python -m venv venv
.\venv\Scripts\python.exe -m pip install -r requirements.txt
.\venv\Scripts\python.exe -X utf8 -m uvicorn app.api.main:app --host 127.0.0.1 --port 8000
```

已有虚拟环境时直接使用，不必重建。另开终端启动前端：

```powershell
cd frontend
npm ci
npm run dev
```

页面：`http://127.0.0.1:5173/`；API 文档：`http://127.0.0.1:8000/docs`。正式构建用 `npm run build`，不等于已经部署。

## 检查与排错

| 现象 | 先检查 |
| --- | --- |
| `/ready` 未就绪 | 本地存储、任务执行器和共享资源协调是否可用 |
| 高德网络连接失败 | 启动后端的环境能否访问外网；受限环境中的进程可能继承网络限制 |
| 高德权限或额度错误 | 按返回代码检查对应 Key 的服务权限、白名单和账户额度 |
| 任务提交返回 429 | 容量或频率限制；遵循 `Retry-After`，已接收任务仍可查看或取消 |
| 交通、开放时间或费用待核实 | 查缺失字段与来源；不能把本地就绪状态当成供应商数据完整 |
| 版本保存返回 409 | 行程已被更新，重新读取并核对，不覆盖较新的版本 |

`/health`、`/ready`、`/api/map/health` 不会证明模型和地图上游查询成功。需要时用 API 文档中的公开示例调用实际查询；真实模型和地图调用与本地健康检查分开。

## 数据与演示

运行数据位于 `backend/var/`。不要删除用户库来重置测试，也不要只复制正在写入的 SQLite 主文件作为可靠备份；停写后备份，或使用 SQLite 一致性备份机制，并验证恢复。

求职演示先准备公开示例和可恢复的本地运行环境。公开展示链接需要另外确认部署、HTTPS、持久卷、访问控制和调用额度；本地 owner token 不等于完整账号体系。测试方法见[评测](EVALUATION.md)。
