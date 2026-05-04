FROM python:3.10-slim

# 安装中文字体及必要依赖（不通过 apt 安装 wkhtmltopdf 系统包）
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        wget \
        xfonts-75dpi xfonts-base \
        fonts-wqy-microhei \
        libxrender1 libxext6 libssl1.1 libfontconfig1 \
        && rm -rf /var/lib/apt/lists/*

# 下载 wkhtmltopdf 官方 .deb 包（Debian 11 bullseye 兼容）
RUN wget https://github.com/wkhtmltopdf/packaging/releases/download/0.12.6.4-1/wkhtmltox_0.12.6.4-1.bullseye_amd64.deb && \
    dpkg -i wkhtmltox_0.12.6.4-1.bullseye_amd64.deb || true && \
    apt-get install -f -y && \
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