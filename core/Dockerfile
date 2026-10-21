FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv
COPY core/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY core/app ./app
COPY core/alembic.ini ./alembic.ini
COPY core/migrations ./migrations
COPY modules ./modules
RUN useradd -r -u 1000 jarvis && mkdir /data && chown jarvis /data
USER jarvis
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/api/health')"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
