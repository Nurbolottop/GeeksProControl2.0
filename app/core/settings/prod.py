from core.settings.base import *

DEBUG = False

# Cookies только по HTTPS
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

# Приложение работает за nginx, который терминирует HTTPS.
# Без этого Django считает запрос http-запросом и ломает CSRF/редиректы.
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
USE_X_FORWARDED_HOST = True

# Прочие security-заголовки
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = 'same-origin'
X_FRAME_OPTIONS = 'DENY'

# В production файлы media отдаёт nginx из internal-location
# (deploy/nginx/gpro.zeastudio.su.conf), Django только проверяет права.
MEDIA_ACCEL_REDIRECT_PREFIX = os.getenv('MEDIA_ACCEL_REDIRECT_PREFIX', '/protected-media/')

# HSTS включай только когда HTTPS точно настроен и работает,
# иначе браузеры запомнят домен и http перестанет открываться.
# SECURE_HSTS_SECONDS = 31536000
# SECURE_HSTS_INCLUDE_SUBDOMAINS = True

# Имена статических файлов с хешем содержимого (app.3f2a….css): nginx отдаёт
# /static/ с долгим кэшем, и без хеша после обновления у людей остался бы
# старый CSS/JS. collectstatic в entrypoint переписывает и url()/@import в CSS.
STORAGES = {
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.ManifestStaticFilesStorage'},
}
