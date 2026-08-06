FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# 安装系统依赖：tesseract-ocr 用于图片文字识别(OCR)
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

# 先安装 Python 依赖，利用 Docker 层缓存
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 单独安装 hello-agents：它声明 openai<2.0.0 与 langchain-openai(需openai>=2.x) 冲突，
# 但 hello-agents 实际能在 openai 2.x 上运行(本地已验证)，用 --no-deps 绕过约束
RUN pip install --no-cache-dir --no-deps "hello-agents[protocols]==0.2.9" "langchain-protocol==0.0.16"

# 复制项目代码
COPY . .

EXPOSE 8000

# 启动后端：监听 Render 注入的 PORT 环境变量（默认 8000）
CMD uvicorn app.api.main:app --host 0.0.0.0 --port ${PORT:-8000}
