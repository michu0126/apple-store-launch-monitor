FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DB_PATH=/data/dashboard.sqlite3 \
    WEBDRIVER_URL=http://firefox:4444/wd/hub

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY stores.json catalog.json web_server.py container_worker.py container_main.py ./
COPY web ./web
RUN mkdir -p /data && useradd --create-home monitor && chown -R monitor:monitor /app /data
USER monitor
EXPOSE 8765
VOLUME ["/data"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/api/state', timeout=3)"]
CMD ["python", "container_main.py"]
