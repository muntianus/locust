FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=4070

COPY locustfile.py index.js README.md ./
COPY swarm_service ./swarm_service

RUN useradd --system --uid 10001 --home-dir /app swarm

EXPOSE 4070

HEALTHCHECK --interval=15s --timeout=3s --start-period=5s --retries=3 \
  CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:4070/health', timeout=2).read()"

USER swarm

CMD ["python3", "-m", "swarm_service.server"]
