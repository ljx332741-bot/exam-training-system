FROM python:3.10-slim-bullseye

# 安装系统依赖（包括 wkhtmltopdf 和中文字体）
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        wkhtmltopdf \
        xfonts-75dpi \
        xfonts-base \
        fontconfig \
        fonts-wqy-microhei \
        fonts-wqy-zenhei \
        fonts-noto-cjk \
        fonts-liberation \
        && rm -rf /var/lib/apt/lists/*

# 刷新字体缓存（让系统识别新安装的字体）
RUN fc-cache -fv

# 验证字体是否安装成功
RUN fc-list :lang=zh || echo "No Chinese fonts found"

# 设置字体路径
ENV FONTCONFIG_PATH=/etc/fonts
ENV FONTCONFIG_FILE=/etc/fonts/fonts.conf

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN touch reviewer.txt reviewers.json

ENV PYTHONUNBUFFERED=1
ENV FLASK_DEBUG=False

EXPOSE 8000

CMD gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --threads 8 --timeout 120
