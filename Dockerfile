FROM python:3.10-slim-bullseye

# 安装系统依赖（包括 wkhtmltopdf 和中文字体）
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        wkhtmltopdf \
        xfonts-75dpi xfonts-base \
        fonts-wqy-microhei \
        && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN touch reviewer.txt reviewers.json

ENV PYTHONUNBUFFERED=1
ENV FLASK_DEBUG=False

EXPOSE 8000

CMD gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --threads 8 --timeout 120