FROM python:3.10-slim-bullseye

# ============================================================
# 1. 安装系统依赖
# ============================================================
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        # wkhtmltopdf 依赖
        libxrender1 \
        libfontconfig1 \
        libx11-6 \
        libxext6 \
        libxcb1 \
        libxkbcommon0 \
        libxcb1 \
        libxcb-xinerama0 \
        # 字体和中文字体
        fontconfig \
        fonts-wqy-microhei \
        fonts-wqy-zenhei \
        fonts-noto-cjk \
        fonts-liberation \
        # 其他工具
        wget \
        xfonts-75dpi \
        xfonts-base \
        && rm -rf /var/lib/apt/lists/*

# ============================================================
# 2. 安装完整版 wkhtmltopdf（0.12.6 版本）
# ============================================================
# 下载官方完整版（支持 header-left/right）
RUN wget -q https://github.com/wkhtmltopdf/packaging/releases/download/0.12.6-1/wkhtmltox_0.12.6-1.bullseye_amd64.deb && \
    dpkg -i wkhtmltox_0.12.6-1.bullseye_amd64.deb || true && \
    apt-get install -f -y && \
    rm -f wkhtmltox_0.12.6-1.bullseye_amd64.deb

# ============================================================
# 3. 验证安装
# ============================================================
RUN wkhtmltopdf --version

# 刷新字体缓存
RUN fc-cache -fv

# 验证中文字体
RUN fc-list :lang=zh || echo "No Chinese fonts found"

# ============================================================
# 4. 设置环境变量
# ============================================================
ENV WKHTMLTOPDF_PATH=/usr/local/bin/wkhtmltopdf
ENV FONTCONFIG_PATH=/etc/fonts
ENV FONTCONFIG_FILE=/etc/fonts/fonts.conf
ENV PYTHONUNBUFFERED=1
ENV FLASK_DEBUG=False

# ============================================================
# 5. 安装 Python 依赖
# ============================================================
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN touch reviewer.txt reviewers.json

# ============================================================
# 6. 启动命令
# ============================================================
EXPOSE 8000

CMD gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --threads 8 --timeout 120