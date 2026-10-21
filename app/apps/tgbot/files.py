"""Скриншот из Telegram (этап 8): проверка размера до скачивания, тип — по
содержимому (сигнатура файла), имя присланного файла не используется."""
import uuid

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile

SIGNATURES = (
    (b'\x89PNG\r\n\x1a\n', 'png', 'image/png'),
    (b'\xff\xd8\xff', 'jpg', 'image/jpeg'),
    (b'GIF87a', 'gif', 'image/gif'),
    (b'GIF89a', 'gif', 'image/gif'),
)


class FileRejected(ValueError):
    pass


def sniff(content: bytes):
    for magic, ext, mime in SIGNATURES:
        if content.startswith(magic):
            return ext, mime
    if content[:4] == b'RIFF' and content[8:12] == b'WEBP':
        return 'webp', 'image/webp'
    raise FileRejected('Это не изображение (PNG, JPEG, WEBP или GIF).')


def screenshot_from(message: dict):
    """(file_id, size) из фото или документа-изображения; иначе None."""
    if message.get('photo'):
        best = max(message['photo'], key=lambda p: p.get('file_size') or p.get('width', 0))
        return best.get('file_id'), best.get('file_size') or 0
    document = message.get('document') or {}
    if document and str(document.get('mime_type', '')).startswith('image/'):
        return document.get('file_id'), document.get('file_size') or 0
    return None


def download(file_id: str, size: int, *, prefix='telegram') -> SimpleUploadedFile:
    from apps.graduate_bot.bot import bot

    limit = settings.FILE_UPLOAD_MAX_MEMORY_SIZE
    if size and size > limit:
        raise FileRejected('Файл больше 20 МБ.')
    info = bot.get_file(file_id)  # telegram_guard: только file_id из принятого сообщения
    if (getattr(info, 'file_size', 0) or 0) > limit:
        raise FileRejected('Файл больше 20 МБ.')
    content = bot.download_file(info.file_path)  # GuardedTeleBot: только путь из этого getFile
    if len(content) > limit:
        raise FileRejected('Файл больше 20 МБ.')
    ext, mime = sniff(content)
    return SimpleUploadedFile(f'{prefix}_{uuid.uuid4().hex[:12]}.{ext}', content, content_type=mime)
