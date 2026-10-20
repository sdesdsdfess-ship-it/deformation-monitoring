# 通用 Dockerfile：适配 Render / Railway / Fly.io 等免费 PaaS
FROM python:3.11-slim

# 系统依赖：中文字体（matplotlib 出图必须）、gcc（编译部分 Python 包）
RUN apt-get update && apt-get install -y --no-install-recommends \
        fonts-noto-cjk \
        fonts-wqy-zenhei \
        gcc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 先装依赖，利用 Docker 层缓存
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 拷贝应用代码与静态资源
COPY app/ ./app/
COPY sample_data/ ./sample_data/
COPY static/ ./static/

# 清掉 matplotlib 字体缓存，强制重建（新装了 Noto CJK）
RUN rm -rf /root/.cache/matplotlib

# 监听 Render 等平台注入的 PORT 环境变量，默认 10000
EXPOSE 10000
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-10000}
