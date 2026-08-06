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

# 复制项目代码
COPY . .

EXPOSE 8000

# 启动后端：监听 Render 注入的 PORT 环境变量（默认 8000）
CMD uvicorn app.api.main:app --host 0.0.0.0 --port ${PORT:-8000}
