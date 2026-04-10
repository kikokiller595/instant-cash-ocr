FROM mcr.microsoft.com/playwright/python:v1.58.0-noble

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_HEADLESS=1 \
    HOST=0.0.0.0 \
    PORT=8080

WORKDIR /app

COPY requirements.txt /app/requirements.txt

RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr \
    && pip install -r /app/requirements.txt \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

COPY . /app

RUN chmod +x /app/start.sh

EXPOSE 8080

CMD ["/app/start.sh"]
