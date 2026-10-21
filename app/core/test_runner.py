"""Тесты пишут загруженные файлы во временную папку, а не в рабочую media/."""
import shutil
import tempfile

from django.test.runner import DiscoverRunner
from django.test.utils import override_settings


class TempMediaRunner(DiscoverRunner):
    def setup_test_environment(self, **kwargs):
        super().setup_test_environment(**kwargs)
        self._media = tempfile.mkdtemp(prefix='gp2-test-media-')
        # override_settings, а не присваивание: сбрасывает кэш пути у хранилища
        self._override = override_settings(MEDIA_ROOT=self._media)
        self._override.enable()

    def teardown_test_environment(self, **kwargs):
        self._override.disable()
        shutil.rmtree(self._media, ignore_errors=True)
        super().teardown_test_environment(**kwargs)
