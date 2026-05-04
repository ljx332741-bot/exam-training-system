FROM python:3.10-slim

# 安装 wkhtmltopdf 及其依赖（使用 apt-get）
RUN apt-get update && \
    apt-get install -y --no-install-recommends wkhtmltopdf xfonts-75dpi xfonts-base && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 复制依赖文件并安装（只保留生产必需的包，可手动精简 requirements.txt）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制整个项目
COPY . .

# 创建存放 reviewer.txt 等文件的目录（如果不存在）
RUN touch reviewer.txt reviewers.json || true

# 设置环境变量（生产用）
ENV PYTHONUNBUFFERED=1
ENV FLASK_DEBUG=False

# 暴露端口（Render 会动态设置 PORT）
EXPOSE 8000

# 使用 gunicorn 启动，单 worker（避免调度器重复运行）
CMD gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --threads 8 --timeout 120