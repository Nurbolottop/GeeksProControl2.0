import datetime

from django.test import TestCase
from django.utils import timezone
from django.urls import reverse

from apps.accounts.models import User
from apps.training import importer, selectors
from apps.training.models import GroupStatus, Specialization, TrainingGroup

TODAY = datetime.date(2026, 9, 16)

# Сообщение ровно в том виде, в каком его присылает академия
ACADEMY_MESSAGE = """🎨 Design
* 39 группа — старт: 09.06.2026 · конец: 06.10.2026 — 4 студента
* 40 группа — старт: 15.07.2026 · конец: 07.11.2026 — 6 студентов
* 41 группа — старт: 14.09.2026 · конец: 04.01.2027 — 8 студентов

💻 Frontend
* 43 группа — старт: 17.03.2026 · конец: 18.09.2026 — 8 студентов
* 44 группа — старт: 11.05.2026 · конец: 05.11.2026 — 9 студентов
* 45 группа — старт: 15.06.2026 · конец: 03.12.2026 — 7 студентов
* 46 группа — старт: 21.07.2026 · конец: 08.01.2027 — 5 студентов
* 47 группа — старт: 29.08.2026 · конец: 10.02.2027 — 8 студентов
* 48 группа — старт: 28.09.2026 · конец: 11.03.2027 — количество студентов пока неизвестно

⚙️Backend
* 39 группа — старт: 25.03.2026 · конец: 26.09.2026 — 8 студентов
* 40 группа — старт: 04.05.2026 · конец: 26.10.2026 — 8 студентов
* 41 группа — старт: 06.07.2026 · конец: 20.11.2026 — 7 студентов
* 42 группа — старт: 06.07.2026 · конец: 21.12.2026 — 7 студентов
* 43 группа — старт: 19.08.2026 · конец: 03.02.2027 — 5–7 студентов
* 44 группа — старт: 23.09.2026 · конец: 10.03.2027 — 11 студентов на старте

📱Flutter
* 4 группа — старт: 15.06.2026 · конец: 10.12.2026 — 5 студентов
* 5 группа — старт: 10.08.2026 · конец: 28.01.2027 — 7 студентов
* 6 группа — старт: 15.09.2026 · конец: 05.03.2027 — 9 студентов
"""

# Бишкек присылает в другом виде: «группа 62-2», «финиш», численность
# бывает перенесена на следующую строку, в конце — свободный текст
BISHKEK_MESSAGE = """UX - UI
* группа 62 старт: 11.06.2026 · финиш: 01.10.2026 - 11 студентов
* группа 63 старт: 16.07.2026 · финиш: 02.11.2026 - 5-7 студентов
* группа 64 старт: 14.08.2026 · финиш: 08.12.2026 - 8 студентов

FRONTEND
* группа 62-2 старт: 30.03.2026 · финиш: 10.09.2026 - 8 студентов
* группа 63-1 старт: 07.05.2026 · финиш: 29.10.2026 - 7-9 студентов
* группа 64 старт: 08.06.2026 · финиш: 26.11.2026 - 10-12 студентов

BACKEND
* группа 67-1 старт: 28.04.2026 · финиш: 13.10.2026 - 8 студентов
* группа 67-2 старт: 28.04.2026 · финиш: 13.10.2026 - 11-12 студентов
* группа 68 старт: 05.06.2026 · финиш: 20.11.2026 - 
15 студентов
* группа 69-1 старт: 03.07.2026 · финиш: 18.12.2026 - 
10 студентов
* группа 69-2 старт: 03.07.2026 · финиш: 18.12.2026 - 
12 студентов


TESTING
* группа 33  старт: 15.06.2026 · финиш: 03.09.2026 - 
7 студентов 
* группа 34  старт: 30.07.2026 · финиш: 24.10.2026 - 
8-10 студентов 
* группа 35  старт: 03.09.2026 · финиш: 26.11.2026 - 
9-11 студентов 

FLUTTER
* группа 06  старт: 04.04.2026 · финиш: 03.10.2026 - 
6 студентов
* группа 07  старт: 30.05.2026 · финиш: 18.11.2026 - 
6-8 студентов  
* группа 08  старт: 11.07.2026 · финиш: 30.12.2026 - 
5-7 студентов 

PM
по необходимости, сами следим за ними


Это план график бишкек филиала
"""


def make_specializations():
    return {
        name: Specialization.objects.create(name=name)
        for name in ('Backend', 'Frontend', 'UX/UI', 'Mobile', 'Testing/QA')
    }


class AcademyImportTests(TestCase):
    """Разбор сообщения академии."""

    def setUp(self):
        self.specs = make_specializations()

    def test_whole_message_is_understood(self):
        rows = importer.parse(ACADEMY_MESSAGE, 'Бишкек')
        self.assertEqual(len(rows), 18)
        self.assertEqual([row.errors for row in rows if row.errors], [])

    def test_academy_names_map_to_our_directions(self):
        rows = importer.parse(ACADEMY_MESSAGE, 'Бишкек')
        by_header = {row.direction_name: row.specialization.name for row in rows}
        self.assertEqual(by_header['Design'], 'UX/UI')
        self.assertEqual(by_header['Flutter'], 'Mobile')
        self.assertEqual(by_header['Backend'], 'Backend')

    def test_dates_and_count(self):
        row = importer.parse(ACADEMY_MESSAGE, 'Бишкек')[0]
        self.assertEqual(row.number, '39')
        self.assertEqual(row.start_date, datetime.date(2026, 6, 9))
        self.assertEqual(row.end_date, datetime.date(2026, 10, 6))
        self.assertEqual(row.students_count, 4)
        self.assertEqual(row.students_note, '')

    def test_unknown_range_and_at_start_counts(self):
        rows = {(row.specialization.name, row.number): row for row in importer.parse(ACADEMY_MESSAGE, 'Бишкек')}
        unknown = rows[('Frontend', '48')]
        self.assertIsNone(unknown.students_count)
        self.assertIn('неизвестно', unknown.students_note)
        ranged = rows[('Backend', '43')]
        self.assertEqual(ranged.students_count, 5)
        self.assertEqual(ranged.students_note, '5–7 студентов')
        at_start = rows[('Backend', '44')]
        self.assertEqual(at_start.students_count, 11)
        self.assertEqual(at_start.students_note, '11 студентов на старте')

    def test_same_number_in_different_directions_are_different_groups(self):
        importer.apply(importer.parse(ACADEMY_MESSAGE, 'Бишкек'))
        self.assertEqual(TrainingGroup.objects.filter(number='39').count(), 2)
        self.assertEqual(TrainingGroup.objects.count(), 18)

    def test_pasting_again_does_not_duplicate(self):
        importer.apply(importer.parse(ACADEMY_MESSAGE, 'Бишкек'))
        rows = importer.parse(ACADEMY_MESSAGE, 'Бишкек')
        self.assertTrue(all(row.action == 'same' for row in rows))
        result = importer.apply(rows)
        self.assertEqual(result['created'], 0)
        self.assertEqual(TrainingGroup.objects.count(), 18)

    def test_updated_message_changes_dates_and_count(self):
        importer.apply(importer.parse(ACADEMY_MESSAGE, 'Бишкек'))
        updated = ACADEMY_MESSAGE.replace(
            '* 48 группа — старт: 28.09.2026 · конец: 11.03.2027 — количество студентов пока неизвестно',
            '* 48 группа — старт: 30.09.2026 · конец: 11.03.2027 — 10 студентов',
        )
        row = next(r for r in importer.parse(updated, 'Бишкек') if r.number == '48')
        self.assertEqual(row.action, 'update')
        self.assertIn('студентов', row.changes)
        importer.apply([row])
        group = TrainingGroup.objects.get(number='48', specialization=self.specs['Frontend'])
        self.assertEqual(group.students_count, 10)
        self.assertEqual(group.start_date, datetime.date(2026, 9, 30))

    def test_unknown_direction_is_an_error_not_a_guess(self):
        rows = importer.parse('🧪 Кибербезопасность\n* 1 группа — старт: 01.09.2026 · конец: 01.03.2027 — 5 студентов', 'Бишкек')
        self.assertEqual(rows[0].action, 'error')
        self.assertIn('Кибербезопасность', rows[0].errors[0])
        importer.apply(rows)
        self.assertFalse(TrainingGroup.objects.exists())

    def test_broken_line_is_reported(self):
        rows = importer.parse('💻 Frontend\n* 49 группа — старт скоро', 'Бишкек')
        self.assertEqual(rows[0].action, 'error')


class BishkekFormatTests(TestCase):
    """Формат сообщения Бишкека."""

    def setUp(self):
        self.specs = make_specializations()
        self.rows = importer.parse(BISHKEK_MESSAGE, 'Бишкек')
        self.by_key = {(r.specialization.name, r.number): r for r in self.rows}

    def test_whole_message_is_understood(self):
        self.assertEqual(len(self.rows), 17)
        self.assertEqual([r.line for r in self.rows if r.errors], [])

    def test_headers(self):
        names = {r.direction_name: r.specialization.name for r in self.rows}
        self.assertEqual(names['UX - UI'], 'UX/UI')
        self.assertEqual(names['TESTING'], 'Testing/QA')
        self.assertEqual(names['FLUTTER'], 'Mobile')
        self.assertEqual(names['BACKEND'], 'Backend')

    def test_number_after_the_word_and_compound_numbers(self):
        row = self.by_key[('Frontend', '62-2')]
        self.assertEqual(row.start_date, datetime.date(2026, 3, 30))
        self.assertEqual(row.end_date, datetime.date(2026, 9, 10))
        self.assertIn(('Backend', '67-1'), self.by_key)
        self.assertIn(('Backend', '67-2'), self.by_key)

    def test_leading_zeros_dropped(self):
        self.assertEqual(
            sorted(n for (spec, n) in self.by_key if spec == 'Mobile'), ['6', '7', '8'],
        )

    def test_count_wrapped_to_the_next_line(self):
        row = self.by_key[('Backend', '68')]
        self.assertEqual(row.students_count, 15)
        self.assertEqual(row.students_note, '')
        ranged = self.by_key[('Testing/QA', '34')]
        self.assertEqual(ranged.students_count, 8)
        self.assertEqual(ranged.students_note, '8-10 студентов')

    def test_free_text_does_not_become_an_error(self):
        # «PM / по необходимости…» и «Это план график…» — не группы
        self.assertFalse(any('необходимости' in r.line for r in self.rows))

    def test_both_formats_side_by_side(self):
        importer.apply(importer.parse(ACADEMY_MESSAGE, 'Ош'))
        importer.apply(self.rows)
        self.assertEqual(TrainingGroup.objects.filter(branch='Ош').count(), 18)
        self.assertEqual(TrainingGroup.objects.filter(branch='Бишкек').count(), 17)
        again = importer.parse(BISHKEK_MESSAGE, 'Бишкек')
        self.assertTrue(all(r.action == 'same' for r in again))

    def test_same_number_written_differently_is_the_same_group(self):
        importer.apply(self.rows)
        row = importer.parse('FLUTTER\n* группа 6 старт: 04.04.2026 · финиш: 03.10.2026 - 6 студентов', 'Бишкек')[0]
        self.assertEqual(row.action, 'same')

    def test_testing_listed_before_mobile(self):
        importer.apply(self.rows)
        names = [d['specialization'].name for d in selectors.academy_list('Бишкек', today=TODAY)]
        self.assertEqual(names, ['UX/UI', 'Frontend', 'Backend', 'Testing/QA', 'Mobile'])

    def test_graduated_bishkek_groups_are_not_listed(self):
        importer.apply(self.rows)
        dirs = {d['specialization'].name: d for d in selectors.academy_list('Бишкек', today=TODAY)}
        self.assertNotIn('62-2', [l['group'].number for l in dirs['Frontend']['lines']])
        self.assertNotIn('33', [l['group'].number for l in dirs['Testing/QA']['lines']])


class AcademyBranchTests(TestCase):
    """Бишкек и Ош — отдельно."""

    # Даты заведомо в будущем: группы должны оставаться «учатся» и через
    # год, иначе тест начинает падать просто от того, что время идёт
    OSH_MESSAGE = """⚙️Backend
* 39 группа — старт: 01.04.2026 · конец: 01.10.2028 — 6 студентов

📱Flutter
* 2 группа — старт: 01.07.2026 · конец: 01.01.2029 — 4 студента
"""

    def setUp(self):
        self.specs = make_specializations()
        self.user = User.objects.create_user(username='head', password='pass12345')
        self.client.force_login(self.user)

    def test_branch_is_required(self):
        rows = importer.parse(ACADEMY_MESSAGE)
        self.assertTrue(all(row.action == 'error' for row in rows))
        self.assertIn('филиал', rows[0].errors[0])

    def test_branch_from_the_form(self):
        importer.apply(importer.parse(self.OSH_MESSAGE, 'Ош'))
        self.assertEqual(set(TrainingGroup.objects.values_list('branch', flat=True)), {'Ош'})

    def test_same_number_in_both_branches_are_two_groups(self):
        importer.apply(importer.parse(ACADEMY_MESSAGE, 'Бишкек'))
        importer.apply(importer.parse(self.OSH_MESSAGE, 'Ош'))
        backend_39 = TrainingGroup.objects.filter(number='39', specialization=self.specs['Backend'])
        self.assertEqual(sorted(backend_39.values_list('branch', flat=True)), ['Бишкек', 'Ош'])
        self.assertEqual(TrainingGroup.objects.count(), 20)

    def test_branch_headers_inside_the_message(self):
        text = '📍 Бишкек\n' + ACADEMY_MESSAGE + '\nФилиал Ош\n' + self.OSH_MESSAGE
        rows = importer.parse(text, 'Бишкек')
        self.assertEqual([r.errors for r in rows if r.errors], [])
        branches = [row.branch for row in rows]
        self.assertEqual(branches.count('Бишкек'), 18)
        self.assertEqual(branches.count('Ош'), 2)

    def test_find_branch(self):
        self.assertEqual(importer.find_branch('📍 Ош'), 'Ош')
        self.assertEqual(importer.find_branch('Филиал Бишкек:'), 'Бишкек')
        self.assertEqual(importer.find_branch('БИШКЕК'), 'Бишкек')
        self.assertEqual(importer.find_branch('⚙️Backend'), '')
        self.assertEqual(importer.find_branch('Ош и Бишкек'), '')

    def test_groups_without_branch_are_claimed_not_duplicated(self):
        legacy = TrainingGroup.objects.create(
            number='39', specialization=self.specs['Backend'],
            start_date=datetime.date(2026, 3, 25), end_date=datetime.date(2026, 9, 26),
            students_count=8,
        )
        rows = importer.parse(ACADEMY_MESSAGE, 'Бишкек')
        row = next(r for r in rows if r.number == '39' and r.specialization.name == 'Backend')
        self.assertEqual(row.action, 'update')
        self.assertEqual(row.changes, ['филиал'])
        importer.apply(rows)
        legacy.refresh_from_db()
        self.assertEqual(legacy.branch, 'Бишкек')
        self.assertEqual(TrainingGroup.objects.filter(number='39', specialization=self.specs['Backend']).count(), 1)

    def test_one_legacy_group_is_claimed_by_one_branch_only(self):
        TrainingGroup.objects.create(number='39', specialization=self.specs['Backend'])
        text = 'Бишкек\n' + ACADEMY_MESSAGE + '\nОш\n' + self.OSH_MESSAGE
        importer.apply(importer.parse(text, 'Бишкек'))
        backend_39 = TrainingGroup.objects.filter(number='39', specialization=self.specs['Backend'])
        self.assertEqual(sorted(backend_39.values_list('branch', flat=True)), ['Бишкек', 'Ош'])

    def test_page_shows_one_branch_at_a_time(self):
        importer.apply(importer.parse(ACADEMY_MESSAGE, 'Бишкек'))
        importer.apply(importer.parse(self.OSH_MESSAGE, 'Ош'))
        response = self.client.get(reverse('training:plan'), {'branch': 'Ош'})
        dirs = {d['specialization'].name: d for d in response.context['all_directions']}
        self.assertEqual(sorted(dirs), ['Backend', 'Mobile'])
        self.assertEqual([l['group'].number for l in dirs['Mobile']['lines']], ['2'])
        response = self.client.get(reverse('training:plan'), {'branch': 'Бишкек'})
        self.assertEqual(len(response.context['all_directions']), 4)

    def test_tabs_carry_counts(self):
        importer.apply(importer.parse(self.OSH_MESSAGE, 'Ош'))
        tabs = {tab['value']: tab for tab in selectors.branch_tabs(today=TODAY)}
        self.assertEqual(tabs['Ош']['groups'], 2)
        self.assertEqual(tabs['Ош']['students'], 10)
        self.assertEqual(tabs['Бишкек']['groups'], 0)
        self.assertNotIn(selectors.NO_BRANCH, tabs)

    def test_default_tab_is_a_branch_with_groups(self):
        importer.apply(importer.parse(self.OSH_MESSAGE, 'Ош'))
        response = self.client.get(reverse('training:plan'))
        self.assertEqual(response.context['branch'], 'Ош')

    def test_unassigned_tab_appears_only_when_needed(self):
        TrainingGroup.objects.create(
            number='1', specialization=self.specs['Backend'],
            end_date=datetime.date(2027, 1, 1),
        )
        tabs = [tab['value'] for tab in selectors.branch_tabs(today=TODAY)]
        self.assertIn(selectors.NO_BRANCH, tabs)
        response = self.client.get(reverse('training:plan'), {'branch': selectors.NO_BRANCH})
        self.assertContains(response, 'филиалов ещё не было')

    def test_import_redirects_to_the_loaded_branch(self):
        response = self.client.post(
            reverse('training:group_import'),
            {'text': self.OSH_MESSAGE, 'branch': 'Ош', 'confirm': '1'},
        )
        self.assertRedirects(response, reverse('training:plan') + '?branch=Ош')


class AcademyListTests(TestCase):
    """Страница академии — в формате сообщения академии."""

    def setUp(self):
        self.specs = make_specializations()
        importer.apply(importer.parse(ACADEMY_MESSAGE, 'Бишкек'))

    def _dirs(self, today=TODAY):
        return {d['specialization'].name: d for d in selectors.academy_list(today=today)}

    def test_directions_in_the_academy_order(self):
        names = [d['specialization'].name for d in selectors.academy_list(today=TODAY)]
        self.assertEqual(names, ['UX/UI', 'Frontend', 'Backend', 'Mobile'])

    def test_academy_names_are_shown_next_to_ours(self):
        dirs = self._dirs()
        self.assertEqual(dirs['UX/UI']['academy_name'], 'Design')
        self.assertEqual(dirs['Mobile']['academy_name'], 'Flutter')
        self.assertEqual(dirs['Backend']['academy_name'], '')

    def test_groups_listed_by_start(self):
        numbers = [line['group'].number for line in self._dirs()['Frontend']['lines']]
        self.assertEqual(numbers, ['43', '44', '45', '46', '47', '48'])

    def test_students_as_the_academy_wrote_them(self):
        backend = {l['group'].number: l['students'] for l in self._dirs()['Backend']['lines']}
        frontend = {l['group'].number: l['students'] for l in self._dirs()['Frontend']['lines']}
        design = {l['group'].number: l['students'] for l in self._dirs()['UX/UI']['lines']}
        self.assertEqual(design['39'], '4 студента')
        self.assertEqual(backend['39'], '8 студентов')
        self.assertEqual(backend['43'], '5–7 студентов')
        self.assertEqual(backend['44'], '11 студентов на старте')
        self.assertEqual(frontend['48'], 'количество студентов пока неизвестно')

    def test_graduated_groups_leave_the_list(self):
        later = datetime.date(2026, 10, 10)
        backend = [l['group'].number for l in self._dirs(later)['Backend']['lines']]
        self.assertNotIn('39', backend)   # конец 26.09
        self.assertIn('40', backend)      # конец 26.10

    def test_cancelled_group_is_left_out(self):
        group = TrainingGroup.objects.get(number='39', specialization=self.specs['UX/UI'])
        group.status = GroupStatus.CANCELLED
        group.save()
        numbers = [l['group'].number for l in self._dirs()['UX/UI']['lines']]
        self.assertEqual(numbers, ['40', '41'])

    def test_direction_without_groups_is_not_shown(self):
        Specialization.objects.create(name='DevOps')
        self.assertNotIn('DevOps', self._dirs())

    def test_stage_follows_dates_without_resaving(self):
        group = TrainingGroup.objects.get(number='44', specialization=self.specs['Backend'])
        self.assertEqual(group.stage_by_dates(TODAY), GroupStatus.RECRUITING)
        self.assertEqual(group.stage_by_dates(datetime.date(2026, 10, 1)), GroupStatus.STUDYING)
        self.assertEqual(group.stage_by_dates(datetime.date(2027, 4, 1)), GroupStatus.GRADUATED)

    def test_totals(self):
        totals = selectors.academy_totals(today=TODAY)
        self.assertEqual(totals['groups'], 18)
        self.assertEqual(totals['unknown'], 1)
        # 18 + 37 + 46 + 21
        self.assertEqual(totals['students'], 122)
        self.assertEqual(totals['groups_word'], 'групп')

    def test_plurals(self):
        self.assertEqual(selectors.plural(1, 'студент', 'студента', 'студентов'), 'студент')
        self.assertEqual(selectors.plural(4, 'студент', 'студента', 'студентов'), 'студента')
        self.assertEqual(selectors.plural(11, 'студент', 'студента', 'студентов'), 'студентов')
        self.assertEqual(selectors.plural(22, 'студент', 'студента', 'студентов'), 'студента')

    def test_nav_tree_only_lists_branches_with_groups(self):
        tree = selectors.nav_tree(today=TODAY)
        values = [branch['value'] for branch in tree]
        self.assertEqual(values, ['Бишкек'])
        names = [d['specialization'].name for d in tree[0]['directions']]
        self.assertEqual(names, ['UX/UI', 'Frontend', 'Backend', 'Mobile'])

    def test_nav_tree_empty_when_no_groups_at_all(self):
        TrainingGroup.objects.all().delete()
        self.assertEqual(selectors.nav_tree(today=TODAY), [])


class AcademyViewsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='head', password='pass12345')
        self.client.force_login(self.user)
        self.specs = make_specializations()

    def test_import_shows_preview_before_saving(self):
        response = self.client.post(reverse('training:group_import'), {'text': ACADEMY_MESSAGE, 'branch': 'Бишкек'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['rows']), 18)
        self.assertFalse(TrainingGroup.objects.exists())

    def test_import_saves_on_confirm(self):
        response = self.client.post(
            reverse('training:group_import'), {'text': ACADEMY_MESSAGE, 'branch': 'Бишкек', 'confirm': '1'},
        )
        self.assertRedirects(response, reverse('training:plan') + '?branch=Бишкек')
        self.assertEqual(TrainingGroup.objects.count(), 18)

    def test_branch_page_offers_directions_without_groups(self):
        """Сначала выбор направления: группы на странице филиала не сыплются."""
        importer.apply(importer.parse(ACADEMY_MESSAGE, 'Бишкек'))
        response = self.client.get(reverse('training:plan'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['all_directions']), 4)
        self.assertEqual(response.context['directions'], [])
        self.assertContains(response, 'Выберите направление')
        self.assertContains(response, 'Backend')
        self.assertNotContains(response, '39 группа')

    def test_groups_appear_after_choosing_direction(self):
        """Страница показывает группы, которые учатся прямо сейчас, поэтому
        группу для проверки заводим от сегодняшнего дня, а не из фикстуры —
        иначе тест ломается, как только её выпуск остаётся позади."""
        today = timezone.localdate()
        TrainingGroup.objects.create(
            number='77', specialization=self.specs['Backend'], branch='Бишкек',
            start_date=today - datetime.timedelta(days=30),
            end_date=today + datetime.timedelta(days=60), students_count=8,
        )
        TrainingGroup.objects.create(
            number='78', specialization=self.specs['UX/UI'], branch='Бишкек',
            start_date=today - datetime.timedelta(days=10),
            end_date=today + datetime.timedelta(days=90), students_count=5,
        )
        response = self.client.get(reverse('training:plan'), {
            'branch': 'Бишкек', 'specialization': self.specs['Backend'].pk,
        })
        self.assertEqual(len(response.context['directions']), 1)
        self.assertContains(response, '77 группа')
        self.assertContains(response, '8 студентов')
        self.assertNotContains(response, '78 группа')    # это группа UX/UI
        # другие направления остаются в выборе, чтобы переключаться
        self.assertEqual(len(response.context['all_directions']), 2)
        self.assertNotContains(response, 'Выберите направление')

    def test_empty_academy_page_opens(self):
        response = self.client.get(reverse('training:plan'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'групп пока нет')

    def test_create_group_by_hand_with_unknown_count(self):
        self.client.post(reverse('training:group_create'), {
            'number': '49', 'specialization': self.specs['Frontend'].pk,
            'start_date': '2026-10-20', 'end_date': '2027-04-10',
            'students_count': '', 'students_note': 'набор идёт',
        })
        group = TrainingGroup.objects.get(number='49')
        self.assertIsNone(group.students_count)
        self.assertEqual(group.wants_internship, 0)

    def test_cancel_checkbox(self):
        group = TrainingGroup.objects.create(
            number='50', specialization=self.specs['Backend'],
            start_date=datetime.date(2026, 10, 1), end_date=datetime.date(2027, 3, 1),
        )
        payload = {
            'number': '50', 'specialization': self.specs['Backend'].pk,
            'start_date': '2026-10-01', 'end_date': '2027-03-01',
        }
        self.client.post(reverse('training:group_update', args=[group.pk]), dict(payload, cancelled='on'))
        group.refresh_from_db()
        self.assertEqual(group.status, GroupStatus.CANCELLED)
        self.client.post(reverse('training:group_update', args=[group.pk]), payload)
        group.refresh_from_db()
        self.assertNotEqual(group.status, GroupStatus.CANCELLED)

    def test_wants_cannot_exceed_students(self):
        response = self.client.post(reverse('training:group_create'), {
            'number': '51', 'specialization': self.specs['Backend'].pk,
            'students_count': 10, 'wants_internship': 12,
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(TrainingGroup.objects.filter(number='51').exists())

    def test_graduation_before_start_is_rejected(self):
        self.client.post(reverse('training:group_create'), {
            'number': '52', 'specialization': self.specs['Backend'].pk,
            'start_date': '2026-12-01', 'end_date': '2026-06-01',
        })
        self.assertFalse(TrainingGroup.objects.filter(number='52').exists())

    def test_group_with_interns_is_not_deleted(self):
        from apps.interns.models import Intern

        group = TrainingGroup.objects.create(number='53', specialization=self.specs['Backend'])
        Intern.objects.create(full_name='Стажёр', training_group=group)
        self.client.post(reverse('training:group_delete', args=[group.pk]))
        self.assertTrue(TrainingGroup.objects.filter(pk=group.pk).exists())

    def test_old_graduations_url_leads_to_the_plan(self):
        response = self.client.get(reverse('resources:graduations'))
        self.assertRedirects(response, reverse('training:plan'))

    def test_specialization_filter_narrows_to_one_direction(self):
        importer.apply(importer.parse(ACADEMY_MESSAGE, 'Бишкек'))
        backend = self.specs['Backend']
        response = self.client.get(
            reverse('training:plan'), {'branch': 'Бишкек', 'specialization': backend.pk},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['directions']), 1)
        self.assertEqual(response.context['specialization'], backend)
        self.assertEqual(response.context['directions'][0]['specialization'], backend)

    def test_specialization_filter_for_other_branch_is_empty(self):
        importer.apply(importer.parse(ACADEMY_MESSAGE, 'Бишкек'))
        backend = self.specs['Backend']
        response = self.client.get(
            reverse('training:plan'), {'branch': 'Ош', 'specialization': backend.pk},
        )
        self.assertEqual(response.context['directions'], [])
        self.assertIsNone(response.context['specialization'])

    def test_sidebar_lists_branches_only(self):
        """В меню — только филиалы: направления показывает сама страница
        филиала, а «План-график» и «Все группы» из меню убраны."""
        importer.apply(importer.parse(ACADEMY_MESSAGE, 'Бишкек'))
        response = self.client.get(reverse('training:plan'))
        html = response.content.decode()
        sidebar = html[html.index('IT-академия'):html.index('Резерв')]
        self.assertIn('?branch=%D0%91%D0%B8%D1%88%D0%BA%D0%B5%D0%BA', sidebar)
        self.assertNotIn('specialization=', sidebar)
        self.assertNotIn('План-график', sidebar)
        self.assertNotIn('Все группы', sidebar)

    def test_sidebar_tree_empty_when_no_groups(self):
        response = self.client.get(reverse('training:plan'))
        self.assertEqual(list(response.context['academy_nav']), [])
