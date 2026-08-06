# 智能旅行规划助手 🌍

基于LangGraph框架构建的智能旅行规划助手,集成高德地图MCP服务,提供个性化的旅行计划生成。

## ✨ 功能特点

- 🤖 **AI驱动的旅行规划**: 基于LangGraph框架的SimpleAgent,智能生成详细的多日旅程
- 🗺️ **高德地图集成**: 通过MCP协议接入高德地图服务,支持景点搜索、路线规划、天气查询
- 🧠 **智能工具调用**: Agent自动调用高德地图MCP工具,获取实时POI、路线和天气信息
- 🎨 **现代化前端**: Vue3 + TypeScript + Vite,响应式设计,流畅的用户体验
- 📱 **完整功能**: 包含住宿、交通、餐饮和景点游览时间推荐

## 🏗️ 技术栈

### 后端
- **框架**: LangGraph 
- **API**: FastAPI
- **MCP工具**: amap-mcp-server (高德地图)
- **LLM**: DeepSeek

### 前端
- **框架**: Vue 3 + TypeScript
- **构建工具**: Vite
- **UI组件库**: Ant Design Vue
- **地图服务**: 高德地图 JavaScript API
- **HTTP客户端**: Axios

## 📁 项目结构

```
Trip-planner/
├── backend/                    # 后端服务
│   ├── app/
│   │   ├── agents/            # Agent实现
│   │   │   └── trip_planner_agent.py
│   │   ├── api/               # FastAPI路由
│   │   │   ├── main.py
│   │   │   └── routes/
│   │   │       ├── trip.py
│   │   │       └── map.py
│   │   ├── services/          # 服务层
│   │   │   ├── amap_service.py
│   │   │   └── llm_service.py
│   │   ├── models/            # 数据模型
│   │   │   └── schemas.py
│   │   └── config.py          # 配置管理
│   ├── requirements.txt
│   ├── .env.example
│   └── .gitignore
├── frontend/                   # 前端应用
│   ├── src/
│   │   ├── components/        # Vue组件
│   │   ├── services/          # API服务
│   │   ├── types/             # TypeScript类型
│   │   └── views/             # 页面视图
│   ├── package.json
│   └── vite.config.ts
└── README.md
```

## 🚀 快速开始

### 前提条件

- Python 3.10+
- Node.js 16+
- 高德地图API密钥 (Web服务API + Web端JS API)
- LLM API密钥 (DeepSeek/OpenAI等)

### 配置环境变量

```bash
# 后端：填入 AMAP_API_KEY、LLM_API_KEY、LLM_BASE_URL 等
cd backend && cp .env.example .env

# 前端：填入 VITE_AMAP_WEB_KEY、VITE_AMAP_WEB_JS_KEY
cd ../frontend && cp .env.example .env
```

### 方式一：本地开发（双终端，支持热更新）

适用于开发调试，前后端独立运行、代码改动自动生效。

**终端 1 — 后端：**
```bash
cd backend
venv\Scripts\activate                    # Windows | source venv/bin/activate (Linux/Mac)
pip install -r requirements.txt          # 首次安装依赖
uvicorn app.api.main:app --reload --host 0.0.0.0 --port 8000
```

**终端 2 — 前端：**
```bash
cd frontend
npm install
npm run dev
```

打开浏览器访问 `http://localhost:5173`

### 方式二：服务器部署（生产环境，无需手动激活）

部署到服务器后**不需要像本地那样分别激活前后端进程**：前端构建为静态文件由 Nginx 托管，后端以常驻服务方式运行，可开机自启、对外提供服务。

**1. 后端 — 常驻服务运行（Linux 推荐 gunicorn）：**
```bash
cd backend
pip install -r requirements.txt
gunicorn app.api.main:app -w 4 -b 0.0.0.0:8000 --daemon
# 或配置为 systemd 服务实现开机自启
```

**2. 前端 — 构建静态文件：**
```bash
cd frontend
npm install && npm run build             # 产物输出至 frontend/dist
```

**3. Nginx 配置（静态托管 + API 反向代理）：**
```nginx
server {
    listen 80;
    server_name your-domain.com;

    location / {
        root /path/to/frontend/dist;
        try_files $uri $uri/ /index.html;     # Vue Router history 模式
    }

    location /api/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_buffering off;                  # SSE 流式响应需关闭缓冲
        proxy_read_timeout 300s;
    }
}
```

部署完成后访问 `http://your-domain.com` 即可使用。

## 📝 使用指南

1. 在首页填写旅行信息:
   - 目的地城市
   - 旅行日期和天数
   - 交通方式偏好
   - 住宿偏好
   - 旅行风格标签

2. 点击"生成旅行计划"按钮

3. 系统将:
   - 调用Agent生成初步计划
   - Agent自动调用高德地图MCP工具搜索景点
   - Agent获取天气信息和路线规划
   - 整合所有信息生成完整行程

4. 查看结果:
   - 每日详细行程
   - 景点信息与地图标记
   - 交通路线规划
   - 天气预报
   - 餐饮推荐


### MCP工具调用

Agent可以自动调用以下高德地图MCP工具:
- `maps_text_search`: 搜索景点POI
- `maps_weather`: 查询天气
- `maps_direction_walking_by_address`: 步行路线规划
- `maps_direction_driving_by_address`: 驾车路线规划
- `maps_direction_transit_integrated_by_address`: 公共交通路线规划

## 📄 API文档

启动后端服务后,访问 `http://localhost:8000/docs` 查看完整的API文档。

主要端点:
- `POST /api/trip/plan` - 生成旅行计划
- `GET /api/map/poi` - 搜索POI
- `GET /api/map/weather` - 查询天气
- `POST /api/map/route` - 规划路线
