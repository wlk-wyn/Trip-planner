# 本地运行与排错

以下操作以仓库根目录和 Windows PowerShell 为起点。当前项目没有经过公网部署验收。

## 配置

复制并填写 [backend/.env.example](../backend/.env.example) 和 [frontend/.env.example](../frontend/.env.example) 对应的 `.env`。已有配置不要覆盖。

- 后端：`AMAP_API_KEY`、`LLM_API_KEY`、`LLM_BASE_URL`、`LLM_MODEL_ID`；默认 `PLANNER_ENGINE=agent`。
- 默认依赖支持 `agent` 和 `langgraph`。历史 HelloAgents 0.2.9 要求 `openai<2`，与当前 LangChain 所需的 OpenAI 2.x 冲突，已从默认安装中分离。历史代码仅在明确选择旧引擎时导入 SDK，需要单独准备兼容环境；不要在新版部署中强装或用 `--no-deps` 绕过冲突。
- 前端：填写高德 JS API 相关配置；`VITE_API_BASE_URL` 支持同源接口，本地 Vite 代理到后端。
- 可选美团：在 `backend/.env` 设置 `MEITUAN_TRAVEL_TOKEN`，重启后端。无需把 Token 放在前端，也无需全局安装 CLI。实现参照官方文档指定的 `@meituan-travel/travel-cli@1.0.16` 的查询协议，通过后端直连其固定 HTTPS 地址；供应商升级时需复核协议。
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

依赖已安装、配置已填写时，也可在仓库根目录运行 `./scripts/start-demo.ps1`。脚本复用正在运行的服务，缺少时启动本项目进程并检查就绪；新进程日志和 PID 位于 `backend/var/local-*.log`、`backend/var/demo-processes.json`。它不会自动安装依赖、生成旅行或查询上游。

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

结果页时间线的景点旁有“查门票与预约”，点击打开窗口后自动读取该日期的票种与价格；改日期后点击“更新价格”重新查询。旧行程会默认选择今天查票，并注明与行程日期不同；查票不会改行程。部分已核实场馆另有官方参观须知。美团原文和商品链接单独展示，价格可能是讲解/套票或脱敏字符串，不自动写入预算，不代表官方授权、实时余票或预约成功。

查票窗口先展示官方须知摘要，再展示可识别的商品卡片；标题标价与原文合计分别保留，展示供应商明确提供的包含内容与适用条件，不计算或推断最终支付价。只有政策票价时，按季节与票种展示“平台票价参考”，不当作所选日期的实时商品报价；多个价格混在一个句子里不擅自拆配。仅返回景点链接时显示平台入口，不伪造商品或价格。用户决定后点击“去美团预订”在平台完成购买。票种、优惠与平台完整回复折叠保留，窗口内部滚动，标题与日期操作不随长文滚出屏幕。

官方须知独立读取，不必等待美团返回。目前收录故宫、国博、天坛和颐和园，以城市、地点 ID、名称与坐标范围匹配；摘要、入场与退改细则可在窗口内阅读，购票预约通过已核实的外部入口完成。颐和园尚未核实直接官方购票入口，不凭空生成链接。资料来源及复核范围可展开查看。

同一窗口展开“行程入场时间”，按实际凭证填写固定场次或具体入场时段，再点“检查与预览”。该操作复用已保存的交通，不等待价格查询，也不产生模型或地图调用。晚到且存在可行方案时，可选提前出发；展开当天安排查看变化后保存，形成一个历史版本。本地即时预览提供不删地点、不缩短停留、最多提前两小时的出发调整；其他路线方案另行查询，不承诺解决所有冲突。日期与行程不同时不能把查票时间写入原行程；未知交通仍保留待核实，需另行重新校验。录入和外部跳转均不代表预订成功。

若仍有冲突或交通未知，可点“寻找其他调整方案”，尝试同日换序；勾选后才尝试移动未预约的灵活景点到相邻一天。该操作会检查新路段，最多 3 个候选、24 段交通、约 24 秒总预算，单项最多等待 8 秒；交通未知或仍冲突的方案不能采用。采用时服务端重新核对，再保存成一个可恢复版本。已预订的景点及其日期保持不变；严格攻略、明确分日要求或锁定条件可能使跨日建议不可用。

在“我的门票记录”中填写整笔实付、覆盖人数、票种和渠道，勾选已完成对应行程日期的购买/预约，再预览费用变化后保存。记录日期固定为原行程日期，另查其他日期的商品不会改变记录日期或自动填入价格，已过期行程也能更新实际退款。当前仅支持每个景点每天一笔有效汇总记录；多张不同优惠票可合并名称、总人数与总金额。取消/退款入口只更新本地记录，不向平台发起退款；填累计实际已退金额，未到账的钱继续保留在小计中。若勾选同时清除关联入场时间，时间仍与原记录一致才会清除，之后需重新校验时间线。

资料维护在 `backend/app/services/admission_guides.py`，本轮复核日为 2026-10-22，有效参考期限 30 天；失效后隐藏具体规则并提示复核。更新时需阅读官方原文、核对场馆身份并更新对应测试。当前是人工复核的常规资料，不是自动全网抓取、临时公告监测或实时余票查询。故宫放票时间仅按常规规则推算，周一不推断节假日开放。读取接口校验行程 owner、版本及景点身份，不修改行程，也不调用模型或美团。

同一 Token 下相同城市/查询在进程内缓存 10 分钟（最多 64 条），并发相同查询合并；失败不缓存、不自动重试。结果标明原始查询时间。后端使用约 125 秒请求超时、130 秒总截止，并受共享交互请求容量限制；前端关闭窗口后可继续看行程，再次查询可能合并或使用缓存。多进程缓存不共享。实际免费额度与公开网站使用范围仍须查看美团控制台。

授权错误检查 Token 和能力权限；429 检查供应商额度或本地频率限制；504 可稍后手动重试。健康检查不会调用美团。当前只有查询与外部链接，没有代下单、支付和订单同步。

运行数据位于 `backend/var/`。不要删除用户库来重置测试，也不要只复制正在写入的 SQLite 主文件作为可靠备份；停写后备份，或使用 SQLite 一致性备份机制，并验证恢复。

求职演示先准备公开示例和可恢复的本地运行环境。公开展示链接需要另外确认部署、HTTPS、持久卷、访问控制和调用额度；本地 owner token 不等于完整账号体系。测试方法见[评测](EVALUATION.md)。

启动后，在仓库根目录运行 `./scripts/check-demo.ps1` 可检查前端入口、后端健康和本地存储就绪，不调用模型或地图。完整演示操作见[演示说明](DEMO.md)。

## GitHub 更新到 Render 与 Cloudflare Pages（无需 Docker）

本地仓库当前分支为 `master`，远程为 `github.com/wlk-wyn/Trip-planner`。这只说明代码托管位置，不证明线上服务已经连接或部署成功。已有服务优先更新原服务，不必重新创建；先在控制台确认连接的仓库、部署分支及自动部署开关。

### 已有 Render Docker 后端

旧分支中的部署配置使用 Docker。继续更新原 Docker 服务时，采用以下配置；镜像由 Render 云端构建，本机无需安装 Docker：

| 设置 | 本项目填写值 |
| --- | --- |
| Branch | `master` |
| Root Directory | 留空（仓库根目录） |
| Dockerfile Path | `deploy/Dockerfile.backend` |
| Docker Build Context Directory | `.` |
| Docker Command | 留空，使用 Dockerfile 的 CMD |
| Health Check Path | `/ready` |

在 Environment 设置 `PORT=8000`，与当前 CMD 一致，凭据和 `CORS_ORIGINS` 同下方说明。需要持久存储时，Docker 服务的挂载路径是 `/app/var`；先备份旧数据。不要将下方 Python 原生服务的根目录、启动命令或磁盘路径混用于 Docker 服务。Render 的 Docker/根目录规则见[官方 Docker 文档](https://render.com/docs/docker)和[单仓库多项目配置](https://render.com/docs/monorepo-support)。

### Render Python 原生后端（另一种选择）

按 [Render FastAPI 部署说明](https://render.com/docs/deploy-fastapi/)使用 Python 原生 Web Service，项目参数如下：

| 设置 | 本项目填写值 |
| --- | --- |
| Root Directory | `backend` |
| Build Command | `pip install -r requirements.txt -c constraints-demo.txt` |
| Start Command | `uvicorn app.api.main:app --host 0.0.0.0 --port $PORT --workers 1` |
| Health Check Path | `/ready` |

本地依赖约束和容器以 Python 3.12 为基准，原生 Linux 依赖安装仍需查看线上构建日志验收。已有服务变更构建命令前，保留原来的可用配置作为回退参考。

在 Render 的 Environment 中配置 `AMAP_API_KEY`、`LLM_API_KEY`、`LLM_BASE_URL`、`LLM_MODEL_ID`，需要门票查询时增加 `MEITUAN_TRAVEL_TOKEN`；`PLANNER_ENGINE=agent`。`CORS_ORIGINS` 填实际前端 HTTPS 源，例如 `https://your-site.pages.dev`，不带路径或末尾斜杠，多个源用逗号分隔。其余容量和 QPS 参数见后端示例，演示初期可设置 `PLANNING_MAX_RUNNING=1`、`PLANNING_MAX_PENDING=3`、`PLANNING_MAX_PENDING_PER_OWNER=1`。不要把真实密钥提交到 Git。

所有默认 SQLite 文件位于 `backend/var`。按上述目录部署，持久磁盘应挂载到 `/opt/render/project/src/backend/var`，并保持单实例、单工作进程。挂载前先备份旧服务数据，确认恢复方式；只推送源码不会搬运本地行程。Render 默认文件系统会在重新部署或重启时丢弃运行时写入的数据，持久磁盘需要付费服务，详见[持久磁盘说明](https://render.com/docs/disks)。没有持久存储只能作为允许丢失数据的临时演示，不能承诺保留行程。

图片 OCR 还需要系统安装 Tesseract 及中文语言包，仅安装 Python 依赖不能提供该能力；未安装的原生环境需要另外配置系统依赖，或选择带这些依赖的后端 Docker 镜像。

### Cloudflare Pages 前端

按 [Cloudflare Vue 部署说明](https://developers.cloudflare.com/pages/framework-guides/deploy-a-vue-site/)连接同一 GitHub 仓库，参数如下：

| 设置 | 本项目填写值 |
| --- | --- |
| Production branch | `master`（以实际绑定的分支为准） |
| Root directory | `frontend` |
| Build command | `npm ci && npm run build` |
| Build output directory | `dist` |

构建环境配置 `VITE_API_BASE_URL=https://实际后端服务.onrender.com`（仅源地址，不附加 `/api`），以及 `VITE_AMAP_WEB_JS_KEY`。浏览器地图 Key 按高德控制台配置实际前端域名；模型、美团和高德 Web Service 密钥只放后端。修改 `VITE_*` 后需要重新构建前端，不能只重启后端。

### 本次发布顺序

1. 备份线上行程库并核对上述存储、环境变量和分支。现有本地 `localhost` 行程链接及浏览器 owner 标识不会随源码自动迁移到线上。
2. 本地提交后运行 `git push origin master`。若开启自动部署，前后端可能同时开始构建；未开启则分别在控制台部署最新提交。Render 支持[绑定分支自动部署](https://render.com/docs/deploys)。
3. 等待两边成功，在控制台确认部署的是本次提交；访问后端 `/ready`，再打开前端新建一条测试行程，检查生成、地图、门票查询、保存与刷新恢复。
4. 如需保证旧页面兼容或避免同时发布，先在控制台关闭自动部署，推送后手动先部署后端，再部署前端。保留原服务的访问控制；Docker 方案中的 Nginx 认证不会自动应用到这两个独立平台。

当前 `master` 代码已推送，平台配置由控制台设置；公网运行效果仍需实际部署验收。

## 可部署演示配置

已提供根目录 `compose.demo.yml`、`deploy/Dockerfile.backend`、`deploy/Dockerfile.frontend` 和 `deploy/nginx.conf`。前端同源代理、SPA 回退、SSE 不缓冲、后端 `/ready` 启动依赖及持久化 `trip-data` 卷已写入配置。后端只有一个工作进程、不发布宿主机端口；网页默认绑定 `127.0.0.1:8080`，所有网页和 API 都继承邀请访问认证。Docker 构建上下文排除环境文件、用户数据库及认证文件。配置参考 [Compose 就绪依赖](https://docs.docker.com/compose/how-tos/startup-order/)、[构建上下文排除](https://docs.docker.com/build/concepts/context/#dockerignore-files)、[Nginx 认证](https://nginx.org/en/docs/http/ngx_http_auth_basic_module.html)和[代理缓冲](https://nginx.org/en/docs/http/ngx_http_proxy_module.html#proxy_buffering)。

前提：目标机器已安装 Docker Engine/Desktop 和 Compose 插件。当前开发机没有 Docker，本轮没有构建或启动容器；以下是已准备、待容器环境验收的配置。

首次准备（PowerShell，仓库根目录）：

```powershell
if (!(Test-Path deploy/.env)) { Copy-Item deploy/.env.example deploy/.env }
New-Item -ItemType Directory -Path deploy/secrets -Force | Out-Null
$demoAuthDir = (Resolve-Path deploy/secrets).Path
docker run --rm -it --mount "type=bind,source=$demoAuthDir,target=/out" httpd:2.4-alpine htpasswd -Bc /out/htpasswd demo
```

最后一条命令让你交互设置邀请密码，不在命令中填写密码。仅首次创建认证文件时使用 `-c`；后续修改用 `-B /out/htpasswd demo`，避免覆盖其他用户。在 Linux 主机上运行时让 Nginx 工作进程可读该散列文件，例如 `chmod 644 deploy/secrets/htpasswd`。认证文件在 Git 和构建上下文中都被排除。

保留现有 `backend/.env`，填写其中服务端凭据；在 `deploy/.env` 设置浏览器地图 JS Key、端口和展示源。不要把模型、高德 Web Service 或美团 Token 填为浏览器 Key。浏览器 Key 是构建参数，调整后需重建前端镜像。后端直接依赖版本记录在 `backend/constraints-demo.txt`，不是完整传递依赖锁，也不是 Linux 兼容性验证结果。

```powershell
docker compose --env-file deploy/.env -f compose.demo.yml config --quiet
docker compose --env-file deploy/.env -f compose.demo.yml up -d --build --wait --wait-timeout 120
```

用浏览器访问 `http://127.0.0.1:8080`，输入邀请账号密码。不要打印完整的 `docker compose config` 或容器环境，它们可能包含服务端凭据。常规停止使用 `docker compose --env-file deploy/.env -f compose.demo.yml down`，保留持久卷；不要为重置测试删除用户数据卷。

配置默认规划同时运行 1、待处理最多 3、单 owner 待处理 1，交互请求同时 1；QPS 不应超过账户权限。频率和并发限制不是每日金额上限，供应商侧仍需设置可用的消费上限/告警；项目没有每日金额熔断。`eval.load_eval` 要求至少两个执行槽，不能直接拿演示容量执行该脚本。

服务器和域名确定后，在宿主机通过 HTTPS 反向代理转发到回环端口，更新 `DEMO_ORIGIN` 和高德浏览器域名限制；保留入口认证。上线仍需验收 HTTPS、真实生成、SSE、编辑/恢复和重启持久化；owner 标识不是正式账号体系。`.github/workflows/demo-containers.yml` 提供仅手动触发的容器构建与匿名拦截、就绪、SPA 检查，使用可丢弃的假凭据，不调用真实供应商。本轮未推送或运行该工作流。

## 备份与隔离演示验收

一致性备份使用 SQLite backup API，新目标文件不允许覆盖，完成后做完整性检查。本地开发库的备份：

```powershell
./backend/venv/Scripts/python.exe -X utf8 ./scripts/backup-trips.py --destination ./backend/var/backups/trips-demo.sqlite3
```

每次使用新的目标文件名。该工具只备份旅行/版本数据库，不包含密钥、其他缓存或所有 Agent 检查点。恢复和跨机迁移应停写后进行，并单独验证；当前容器配置使用独立卷，不自动导入本地已有旅行。

容器卷里的库与本地库不同，运行中的容器可这样创建一个新快照：

```powershell
docker compose --env-file deploy/.env -f compose.demo.yml exec -T api python /app/backup-trips.py --source /app/var/trips.sqlite3 --destination /app/var/backups/trips-demo.sqlite3
```

同样每次使用新文件名，并将快照另行导出到选定的备份位置；留在同一数据卷里的快照不能防止该卷丢失。

在 `backend` 目录运行隔离验收，不调用外部服务，不改用户数据库：

```powershell
./venv/Scripts/python.exe -X utf8 -m eval.demo_journey --report var/demo-journey.json
```

脚本以明确标注的受控样本验证 DIY、预订记账、冲突方案、部分退款及版本恢复的六步 API 流程。合成交通不代表真实路段质量；真实生成与五分钟展示仍按[演示说明](DEMO.md)执行。
