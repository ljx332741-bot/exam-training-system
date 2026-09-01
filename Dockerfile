FROM python:3.10-slim-bullseye

# ============================================================
# 1. 安装系统依赖
# ============================================================
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        # wkhtmltopdf 依赖库
        libxrender1 \
        libfontconfig1 \
        libx11-6 \
        libxext6 \
        libxcb1 \
        libxkbcommon0 \
        libxcb-xinerama0 \
        libjpeg62-turbo \
        libpng16-16 \
        # 字体
        fontconfig \
        fonts-wqy-microhei \
        fonts-wqy-zenhei \
        fonts-noto-cjk \
        fonts-liberation \
        wget \
        xz-utils \
        && rm -rf /var/lib/apt/lists/*

# ============================================================
# 2. 下载 wkhtmltopdf 静态编译版本（推荐）
# ============================================================
# 直接下载静态编译版本，无需 dpkg 安装，避免依赖问题
RUN wget -q https://github.com/wkhtmltopdf/packaging/releases/download/0.12.6-1/wkhtmltox_0.12.6-1.buster_amd64.deb && \
    # 解压 deb 包（无需安装）
    dpkg -x wkhtmltox_0.12.6-1.buster_amd64.deb /opt/wkhtmltox && \
    rm -f wkhtmltox_0.12.6-1.buster_amd64.deb

# 创建符号链接到 PATH
RUN ln -s /opt/wkhtmltox/usr/local/bin/wkhtmltopdf /usr/local/bin/wkhtmltopdf && \
    ln -s /opt/wkhtmltox/usr/local/bin/wkhtmltoimage /usr/local/bin/wkhtmltoimage

# 设置执行权限
RUN chmod +x /usr/local/bin/wkhtmltopdf /usr/local/bin/wkhtmltoimage

# ============================================================
# 3. 验证安装
# ============================================================
RUN /usr/local/bin/wkhtmltopdf --version

# 刷新字体缓存
RUN fc-cache -fv

# ============================================================
# 4. 设置环境变量
# ============================================================
ENV WKHTMLTOPDF_PATH=/usr/local/bin/wkhtmltopdf
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

EXPOSE 8000

CMD gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --threads 8 --timeout 120