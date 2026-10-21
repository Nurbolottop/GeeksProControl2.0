"""Gunicorn для GeeksPro Control 2.0 (production / stage).

Порт наружу не открывается: контейнер публикует 8000 только на
127.0.0.1 хоста, дальше — nginx (deploy/nginx/gpro.zeastudio.su.conf).
"""
import os

bind = '0.0.0.0:8000'
# Сервер общий с 1.0 и другими проектами, памяти немного — по умолчанию 2.
workers = int(os.getenv('GUNICORN_WORKERS', '2'))
threads = int(os.getenv('GUNICORN_THREADS', '2'))
timeout = int(os.getenv('GUNICORN_TIMEOUT', '120'))
# Время на завершение текущих запросов после SIGTERM.
graceful_timeout = 30
keepalive = 5
# Перезапуск воркеров против утечек памяти, с разбросом, чтобы не все разом.
max_requests = 1000
max_requests_jitter = 100

accesslog = '-'
errorlog = '-'
loglevel = os.getenv('LOG_LEVEL', 'info').lower()
# Реальный IP клиента приходит от nginx в X-Forwarded-For.
forwarded_allow_ips = '*'
