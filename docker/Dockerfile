FROM python:3.11-slim

# Без этого stdout буферизируется блоками (не построчно) без TTY —
# долгоживущие процессы вроде graduate_bot не показывают логи в
# `docker logs`, пока буфер не заполнится.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
        netcat-openbsd \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements-dev.txt ./
# INSTALL_DEV=1 — только локальная разработка (docker-compose.yml): плюс
# зависимости тестов. Production-образ (docker-compose.prod.yml) их не получает.
ARG INSTALL_DEV=0
RUN pip install -r requirements.txt \
    && if [ "$INSTALL_DEV" = "1" ]; then pip install -r requirements-dev.txt; fi

# Код приложения. Секреты, media, импортные xlsx и .env сюда не попадают —
# их отсекает .dockerignore; они приходят только через env и volumes.
COPY app /app
COPY scripts/entrypoint.sh /entrypoint.sh
COPY docker/gunicorn.conf.py /etc/gunicorn.conf.py

# Процессы работают не от root. Каталоги, куда пишет приложение
# (collectstatic, загрузки), принадлежат этому пользователю.
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --home-dir /app --shell /usr/sbin/nologin app \
    && mkdir -p /app/staticfiles /app/media \
    && chown -R app:app /app/staticfiles /app/media \
    && chmod +x /entrypoint.sh

USER app

EXPOSE 8000

ENTRYPOINT ["/entrypoint.sh"]
CMD ["gunicorn", "core.wsgi:application", "--config", "/etc/gunicorn.conf.py"]
