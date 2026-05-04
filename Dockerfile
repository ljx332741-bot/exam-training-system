FROM python:3.10-slim-bullseye

# 安装必要系统依赖（不含 libssl1.1，让 wkhtmltopdf 的 deb 自动处理）
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        wget \
        xfonts-75dpi xfonts-base \
        fonts-wqy-microhei \
        && rm -rf /var/lib/apt/lists/*

# 下载并安装 wkhtmltopdf（使用 apt 自动解决依赖）
RUN wget https://github.com/wkhtmltopdf/packaging/releases/download/0.12.6.4-1/wkhtmltox_0.12.6.4-1.bullseye_amd64.deb && \
    apt-get install -y ./wkhtmltox_0.12.6.4-1.bullseye_amd64.deb && \
    rm wkhtmltox_0.12.6.4-1.bullseye_amd64.deb

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN touch reviewer.txt reviewers.json

ENV PYTHONUNBUFFERED=1
ENV FLASK_DEBUG=False

EXPOSE 8000

CMD gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --threads 8 --timeout 120