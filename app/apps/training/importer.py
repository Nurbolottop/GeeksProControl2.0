"""Разбор сообщения от IT-академии со списком групп.

Филиалы присылают текст по-разному — понимаем оба вида:

    🎨 Design                                          (Ош)
    * 39 группа — старт: 09.06.2026 · конец: 06.10.2026 — 4 студента
    * 48 группа — старт: 28.09.2026 · конец: 11.03.2027 — количество пока неизвестно

    UX - UI                                            (Бишкек)
    * группа 62-2 старт: 30.03.2026 · финиш: 10.09.2026 - 5-7 студентов
    * группа 68 старт: 05.06.2026 · финиш: 20.11.2026 -
    15 студентов

Номер группы может стоять до или после слова «группа» и быть составным
(«62-2»), дата выпуска — «конец» или «финиш», численность — перенесена на
следующую строку.

Строка без «группа» — заголовок направления, строки ниже относятся к нему.
Строка «Бишкек» или «Ош» — заголовок филиала: всё ниже уходит в него.
Если филиала в тексте нет, берётся выбранный при вставке.

Группа определяется тройкой «филиал + направление + номер»: в Бишкеке и
Оше номера групп набираются независимо и могут совпадать. Повторная
вставка того же сообщения обновляет даты и численность, а не плодит дубли.
"""
import datetime
import re
from dataclasses import dataclass, field

from apps.training.models import BRANCHES, Specialization, TrainingGroup

# Как академия называет направления → как они называются у нас.
# Ключи — без пробелов и знаков: «UX - UI», «ux/ui» и «UXUI» — одно и то же.
ALIASES = {
    'design': 'UX/UI', 'дизайн': 'UX/UI', 'uiux': 'UX/UI', 'uxui': 'UX/UI', 'ux': 'UX/UI',
    'flutter': 'Mobile', 'mobile': 'Mobile', 'мобильнаяразработка': 'Mobile',
    'frontend': 'Frontend', 'фронтенд': 'Frontend',
    'backend': 'Backend', 'бэкенд': 'Backend', 'python': 'Backend',
    'qa': 'Testing/QA', 'testing': 'Testing/QA', 'testingqa': 'Testing/QA',
    'тестирование': 'Testing/QA',
    'devops': 'DevOps', 'pm': 'PM',
}


def _key(text: str) -> str:
    return re.sub(r'[\W_]+', '', text.lower())


NUMBER = r'\d+(?:\s*[-–]\s*\d+)?'
GROUP_RE = re.compile(
    rf'(?:(?P<before>{NUMBER})\s*групп\w*|групп\w*\s*№?\s*(?P<after>{NUMBER}))'
    r'.*?старт\w*\s*:?\s*(?P<start>\d{1,2}\.\d{1,2}\.\d{4})'
    r'.*?(?:конец|финиш|окончание|выпуск)\w*\s*:?\s*(?P<end>\d{1,2}\.\d{1,2}\.\d{4})'
    r'\s*(?:[—–-]\s*(?P<students>.*))?$',
    re.IGNORECASE,
)
RANGE_RE = re.compile(r'(\d+)\s*[–—-]\s*(\d+)')
NUMBER_RE = re.compile(r'\d+')


@dataclass
class ParsedGroup:
    line: str
    direction_name: str
    specialization: Specialization | None
    branch: str = ''
    number: str = ''
    start_date: datetime.date | None = None
    end_date: datetime.date | None = None
    students_count: int | None = None
    students_note: str = ''
    errors: list[str] = field(default_factory=list)
    existing: TrainingGroup | None = None
    changes: list[str] = field(default_factory=list)

    @property
    def action(self) -> str:
        if self.errors:
            return 'error'
        if self.existing is None:
            return 'create'
        return 'update' if self.changes else 'same'


def find_branch(text: str) -> str:
    """«📍 Ош», «Филиал Бишкек», «БИШКЕК:» → название филиала или ''."""
    words = re.sub(r'[^\w\s]', ' ', text).lower().split()
    words = [word for word in words if word not in ('филиал', 'город', 'г')]
    for value, _ in BRANCHES:
        if words == [value.lower()]:
            return value
    return ''


def find_specialization(name: str) -> Specialization | None:
    cleaned = _key(name)
    by_name = {_key(spec.name): spec for spec in Specialization.objects.all()}
    if cleaned in by_name:
        return by_name[cleaned]
    alias = ALIASES.get(cleaned)
    if alias:
        return by_name.get(_key(alias))
    return None


def _number(raw: str) -> str:
    """«06» → «6», «62 - 2» → «62-2»: один и тот же номер записан одинаково."""
    parts = re.split(r'\s*[-–]\s*', raw.strip())
    return '-'.join(str(int(part)) for part in parts)


def _join_wrapped(text: str) -> list[str]:
    """Склеиваем строку группы с численностью, перенесённой на следующую:
    «… финиш: 20.11.2026 -» + «15 студентов»."""
    lines = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if lines and re.search(r'[—–-]$', lines[-1]) and 'групп' not in line.lower():
            lines[-1] = f'{lines[-1]} {line}'
        else:
            lines.append(line)
    return lines


def _date(raw: str) -> datetime.date:
    day, month, year = (int(part) for part in raw.split('.'))
    return datetime.date(year, month, day)


def _students(raw: str) -> tuple[int | None, str]:
    """«8 студентов» → 8; «5–7» → 5 и пометка; «неизвестно» → None."""
    text = (raw or '').strip().rstrip('.')
    if not text:
        return None, ''
    found = RANGE_RE.search(text)
    if found:
        # Планируем по нижней границе, а как прислали — сохраняем
        return int(found.group(1)), text
    found = NUMBER_RE.search(text)
    if found:
        plain = re.fullmatch(r'\d+\s*студент\w*', text, re.IGNORECASE)
        return int(found.group()), '' if plain else text
    return None, text


def _clean_header(line: str) -> str:
    """Убираем эмодзи и маркеры: «⚙️Backend» → «Backend»."""
    return re.sub(r'[^\w\s/+\-]', '', line).strip()


def parse(text: str, branch: str = '') -> list[ParsedGroup]:
    """Разбирает сообщение. `branch` — филиал по умолчанию, если в тексте его нет."""
    rows = []
    direction_name, specialization = '', None
    current_branch = branch
    for line in _join_wrapped(text):
        body = line.lstrip('*•-–— \t')
        match = GROUP_RE.search(body)
        if match is None:
            if re.search(r'групп', body, re.IGNORECASE):
                rows.append(ParsedGroup(
                    line=line, direction_name=direction_name,
                    specialization=specialization, branch=current_branch,
                    errors=['Не разобрал строку: нужны номер группы, «старт» и «конец» (или «финиш»).'],
                ))
                continue
            named_branch = find_branch(body)
            if named_branch:
                # новый филиал — направление начинается заново
                current_branch = named_branch
                direction_name, specialization = '', None
                continue
            direction_name = _clean_header(body)
            specialization = find_specialization(direction_name)
            continue

        row = ParsedGroup(
            line=line, direction_name=direction_name,
            specialization=specialization, branch=current_branch,
            number=_number(match.group('before') or match.group('after')),
        )
        if not current_branch:
            row.errors.append('Не указан филиал: выберите Бишкек или Ош.')
        if not direction_name:
            row.errors.append('Нет заголовка направления над группой.')
        elif specialization is None:
            row.errors.append(
                f'Направление «{direction_name}» не найдено в справочнике.',
            )
        try:
            row.start_date = _date(match.group('start'))
            row.end_date = _date(match.group('end'))
        except ValueError:
            row.errors.append('Неверная дата.')
        if row.start_date and row.end_date and row.end_date < row.start_date:
            row.errors.append('Конец раньше старта.')
        row.students_count, row.students_note = _students(match.group('students'))
        rows.append(row)

    claimed = set()
    for row in rows:
        if row.errors:
            continue
        row.existing = find_existing(row, claimed)
        if row.existing:
            row.changes = _diff(row.existing, row)
    return rows


def find_existing(row: ParsedGroup, claimed: set) -> TrainingGroup | None:
    """Та же группа в базе.

    Группу без филиала (загруженную, когда филиалов ещё не было) считаем
    той же и забираем в указанный филиал — но только одной строкой: если в
    сообщении и Бишкек, и Ош с одинаковым номером, второй достанется новая.
    """
    same = TrainingGroup.objects.filter(
        specialization=row.specialization, number=row.number,
    )
    exact = same.filter(branch=row.branch).first()
    if exact:
        return exact
    legacy = same.filter(branch='').exclude(pk__in=claimed).first()
    if legacy:
        claimed.add(legacy.pk)
    return legacy


def _diff(group: TrainingGroup, row: ParsedGroup) -> list[str]:
    changes = []
    pairs = [
        ('филиал', group.branch, row.branch),
        ('старт', group.start_date, row.start_date),
        ('конец', group.end_date, row.end_date),
        ('студентов', group.students_count, row.students_count),
        ('уточнение', group.students_note, row.students_note),
    ]
    for label, old, new in pairs:
        if old != new:
            changes.append(label)
    return changes


def apply(rows: list[ParsedGroup]) -> dict:
    """Сохраняет разобранные группы. Строки с ошибками пропускает."""
    result = {'created': 0, 'updated': 0, 'same': 0, 'skipped': 0}
    for row in rows:
        action = row.action
        if action == 'error':
            result['skipped'] += 1
            continue
        if action == 'same':
            result['same'] += 1
            continue
        group = row.existing or TrainingGroup(
            specialization=row.specialization, number=row.number,
        )
        group.branch = row.branch
        group.start_date = row.start_date
        group.end_date = row.end_date
        group.students_count = row.students_count
        group.students_note = row.students_note
        group.save()
        result['created' if action == 'create' else 'updated'] += 1
    return result
