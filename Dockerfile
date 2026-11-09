FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DATA_DIR=/data
RUN useradd --uid 10001 --create-home app && mkdir /data && chown 10001 /data
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY observatory ./observatory
USER 10001
EXPOSE 8082
HEALTHCHECK --interval=30s CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8082/healthz')" || exit 1
CMD ["gunicorn","-b","0.0.0.0:8082","-w","2","--worker-tmp-dir","/dev/shm","--access-logfile","-","observatory.server:create_app()"]
