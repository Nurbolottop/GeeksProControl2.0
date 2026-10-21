from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.clients.models import Client as Company
from apps.interns.models import Intern
from apps.reserve import services
from apps.reserve.models import (
    CandidateStatus, EventKind, RecommendationStatus, ReserveCandidate,
    ReserveInvite, ReserveRecommendation, ReserveShareLink,
)
from apps.training.models import Specialization

APPLICATION = {
    'full_name': 'Тест Кандидатов',
    'phone': '0700123456',
    'city': 'Бишкек',
    'skills': 'Python, Django, PostgreSQL',
    'desired_position': 'Backend-разработчик',
    'is_looking_for_job': 'on',
    'work_format': 'remote',
    'employment_type': 'full',
    'consent_given': 'on',
}


class ReserveApplyTests(TestCase):
    """Публичная анкета по ссылке."""

    def setUp(self):
        self.invite = services.issue_invite()
        self.url = self.invite.get_absolute_url()

    def test_form_opens_by_link(self):
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_application_without_consent_is_rejected(self):
        payload = dict(APPLICATION)
        payload.pop('consent_given')
        response = self.client.post(self.url, payload)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(ReserveCandidate.objects.exists())
        self.assertContains(response, 'Без согласия отправить анкету нельзя.')

    def test_application_creates_candidate_on_review(self):
        self.client.post(self.url, APPLICATION)
        candidate = ReserveCandidate.objects.get(full_name='Тест Кандидатов')
        self.assertEqual(candidate.status, CandidateStatus.REVIEW)
        self.assertTrue(candidate.consent_given)
        self.assertIsNotNone(candidate.submitted_at)
        self.assertIsNotNone(candidate.consent_at)

    def test_link_serves_many_candidates(self):
        """Ссылка общая: каждое заполнение — отдельная карточка."""
        self.client.post(self.url, APPLICATION)
        self.client.post(self.url, dict(
            APPLICATION, full_name='Второй Кандидат', phone='0700999888',
        ))
        self.assertEqual(ReserveCandidate.objects.count(), 2)
        self.invite.refresh_from_db()
        self.assertEqual(self.invite.submissions, 2)
        self.assertTrue(self.invite.is_active)
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_same_phone_updates_the_same_card(self):
        self.client.post(self.url, APPLICATION)
        self.client.post(self.url, dict(APPLICATION, city='Ош'))
        self.assertEqual(ReserveCandidate.objects.count(), 1)
        candidate = ReserveCandidate.objects.get()
        self.assertEqual(candidate.city, 'Ош')
        self.assertTrue(
            candidate.events.filter(title='Кандидат обновил свою анкету').exists(),
        )

    def test_checked_candidate_is_not_thrown_back_by_an_edit(self):
        self.client.post(self.url, APPLICATION)
        candidate = ReserveCandidate.objects.get()
        services.change_status(candidate, CandidateStatus.RESERVE)
        self.client.post(self.url, dict(APPLICATION, city='Ош'))
        candidate.refresh_from_db()
        self.assertEqual(candidate.status, CandidateStatus.RESERVE)

    def test_disabled_link_does_not_open(self):
        self.invite.deactivate()
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.client.post(self.url, APPLICATION)
        self.assertFalse(ReserveCandidate.objects.exists())

    def test_expired_link_does_not_open(self):
        ReserveInvite.objects.filter(pk=self.invite.pk).update(
            expires_at=timezone.now() - timedelta(minutes=1),
        )
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_links_live_independently(self):
        second = services.issue_invite()
        self.invite.deactivate()
        second.refresh_from_db()
        self.assertTrue(second.is_active)
        self.assertEqual(self.client.get(second.get_absolute_url()).status_code, 200)

    def test_candidate_sees_no_internal_data(self):
        ReserveCandidate.objects.create(
            full_name='Оценённый', comment_pm='Внутренний комментарий',
            score_hard_skills=9, decision_comment='Секрет',
        )
        response = self.client.get(self.url)
        self.assertNotContains(response, 'Внутренний комментарий')
        self.assertNotContains(response, 'Секрет')
        self.assertNotContains(response, 'Оценённый')


class ReserveEditLinkTests(TestCase):
    """Ссылка на редактирование конкретной карточки."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='head', password='pass12345', role=User.Role.HEAD,
        )
        self.candidate = ReserveCandidate.objects.create(
            full_name='Демобай Демобаев', phone='0700111222', city='Бишкек',
            skills='Python', status=CandidateStatus.RESERVE,
        )

    def test_form_opens_prefilled(self):
        link = services.issue_invite(self.candidate, user=self.user)
        response = self.client.get(link.get_absolute_url())
        self.assertContains(response, 'Демобай Демобаев')
        self.assertContains(response, '0700111222')

    def test_edit_updates_the_same_card(self):
        link = services.issue_invite(self.candidate, user=self.user)
        self.client.post(link.get_absolute_url(), dict(
            APPLICATION, full_name='Демобай Демобаев', phone='0700111222', city='Ош',
        ))
        self.assertEqual(ReserveCandidate.objects.count(), 1)
        self.candidate.refresh_from_db()
        self.assertEqual(self.candidate.city, 'Ош')
        self.assertEqual(self.candidate.status, CandidateStatus.RESERVE)

    def test_changed_phone_does_not_split_the_card(self):
        """По ссылке на правку карточка та же, даже если сменился телефон."""
        link = services.issue_invite(self.candidate, user=self.user)
        self.client.post(link.get_absolute_url(), dict(
            APPLICATION, full_name='Демобай Демобаев', phone='0700555666',
        ))
        self.assertEqual(ReserveCandidate.objects.count(), 1)
        self.candidate.refresh_from_db()
        self.assertEqual(self.candidate.phone, '0700555666')

    def test_new_edit_link_kills_the_previous_one(self):
        first = services.issue_invite(self.candidate, user=self.user)
        services.issue_invite(self.candidate, user=self.user)
        first.refresh_from_db()
        self.assertFalse(first.is_active)
        self.assertEqual(self.client.get(first.get_absolute_url()).status_code, 404)

    def test_edit_link_is_created_from_the_card(self):
        self.client.force_login(self.user)
        self.client.post(
            reverse('reserve:edit_link', args=[self.candidate.pk]), {'ttl_days': '7'},
        )
        link = ReserveInvite.objects.get(candidate=self.candidate)
        self.assertTrue(link.is_open)
        self.assertTrue(
            self.candidate.events.filter(kind='invited').exists(),
        )

    def test_edit_links_are_not_shown_among_general_links(self):
        services.issue_invite(self.candidate, user=self.user)
        self.client.force_login(self.user)
        response = self.client.get(reverse('reserve:list'))
        self.assertEqual(response.context['open_invites'], [])

    def test_disabled_edit_link_does_not_open(self):
        link = services.issue_invite(self.candidate, user=self.user)
        link.deactivate()
        self.assertEqual(self.client.get(link.get_absolute_url()).status_code, 404)


class ReserveRatingTests(TestCase):
    """Общий рейтинг считается сам по выставленным критериям."""

    def test_rating_is_average_of_filled_scores(self):
        candidate = ReserveCandidate.objects.create(
            full_name='Оценка', score_hard_skills=8, score_quality=9,
        )
        services.recalculate_rating(candidate)
        self.assertEqual(str(candidate.rating), '8.50')

    def test_rating_is_empty_without_scores(self):
        candidate = ReserveCandidate.objects.create(full_name='Без оценок')
        services.recalculate_rating(candidate)
        self.assertIsNone(candidate.rating)


class ReservePermissionTests(TestCase):
    """Внутренние данные меняют только руководитель и администратор."""

    def setUp(self):
        self.candidate = ReserveCandidate.objects.create(full_name='Кандидат')
        # Не head/administrator, и не ПМ/тимлид — у тех своя портальная
        # зона и middleware туда не пускает вовсе; тут нужна роль без
        # отдельного портала, чтобы дойти до самой проверки прав.
        self.viewer = User.objects.create_user(
            username='viewer', password='pass12345', role=User.Role.INTERN,
        )
        self.head = User.objects.create_user(
            username='head', password='pass12345', role=User.Role.HEAD,
        )

    def test_anonymous_is_sent_to_login(self):
        response = self.client.get(reverse('reserve:list'))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login/', response['Location'])

    def test_intern_can_neither_view_nor_change(self):
        """В 2.0 у стажёра нет веб-кабинета (RoleAccessMiddleware): резерв —
        это персональные данные кандидатов, его не видно и не изменить."""
        self.client.force_login(self.viewer)
        self.assertEqual(self.client.get(reverse('reserve:list')).status_code, 403)
        self.assertEqual(
            self.client.get(reverse('reserve:detail', args=[self.candidate.pk])).status_code,
            403,
        )
        response = self.client.post(
            reverse('reserve:status', args=[self.candidate.pk]),
            {'status': CandidateStatus.RESERVE},
        )
        self.assertEqual(response.status_code, 403)
        self.candidate.refresh_from_db()
        self.assertEqual(self.candidate.status, CandidateStatus.NEW)

    def test_head_can_view_and_change(self):
        self.client.force_login(self.head)
        self.assertEqual(self.client.get(reverse('reserve:list')).status_code, 200)
        self.assertEqual(
            self.client.get(reverse('reserve:detail', args=[self.candidate.pk])).status_code,
            200,
        )

    def test_team_lead_is_walled_off_to_own_portal(self):
        """Тимлид теперь портальная роль, как ПМ — общий сайт (включая
        резерв) ему недоступен вообще, его уводит в /lead/."""
        lead = User.objects.create_user(
            username='lead', password='pass12345', role=User.Role.TEAM_LEAD,
        )
        self.client.force_login(lead)
        response = self.client.get(reverse('reserve:list'))
        self.assertRedirects(response, reverse('lead_portal:dashboard'))

    def test_head_can_change_status(self):
        self.client.force_login(self.head)
        self.client.post(
            reverse('reserve:status', args=[self.candidate.pk]),
            {'status': CandidateStatus.RESERVE, 'comment': 'Проверен'},
        )
        self.candidate.refresh_from_db()
        self.assertEqual(self.candidate.status, CandidateStatus.RESERVE)
        self.assertTrue(
            self.candidate.events.filter(title__contains='Статус').exists(),
        )


class ReserveFlowTests(TestCase):
    """Полный сценарий: анкета → проверка → резерв → рекомендация → оффер."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='head', password='pass12345', role=User.Role.HEAD,
        )
        self.client.force_login(self.user)
        self.company = Company.objects.create(organization='ООО Работодатель')

    def _candidate(self):
        invite = services.issue_invite(user=self.user)
        self.client.post(invite.get_absolute_url(), APPLICATION)
        return ReserveCandidate.objects.get(full_name='Тест Кандидатов')

    def test_evaluation_sets_rating_and_history(self):
        candidate = self._candidate()
        self.client.post(reverse('reserve:evaluate', args=[candidate.pk]), {
            'score_hard_skills': 8, 'score_quality': 9, 'score_independence': 8,
            'score_responsibility': 9, 'score_deadlines': 8,
            'score_communication': 9, 'score_teamwork': 8, 'score_learning': 9,
            'geekspro_level': 'junior_plus', 'decision': 'recommend',
        })
        candidate.refresh_from_db()
        self.assertEqual(str(candidate.rating), '8.50')
        self.assertEqual(candidate.geekspro_level, 'junior_plus')
        self.assertTrue(candidate.events.filter(kind='evaluated').exists())

    def test_recommendation_moves_candidate_through_statuses(self):
        candidate = self._candidate()
        services.change_status(candidate, CandidateStatus.RESERVE, user=self.user)
        self.client.post(reverse('reserve:recommend', args=[candidate.pk]), {
            'company': self.company.pk, 'vacancy': 'Junior Backend',
            'sent_on': timezone.localdate().isoformat(),
            'status': RecommendationStatus.SENT, 'comment': 'Отправили резюме',
        })
        recommendation = ReserveRecommendation.objects.get(candidate=candidate)
        candidate.refresh_from_db()
        self.assertEqual(candidate.status, CandidateStatus.PROPOSED)

        for status, expected in [
            (RecommendationStatus.INTERVIEW, CandidateStatus.INTERVIEW),
            (RecommendationStatus.OFFER, CandidateStatus.OFFER),
            # этап 6B: «нанят» компанией — ещё не подтверждённое трудоустройство
            (RecommendationStatus.HIRED, CandidateStatus.OFFER),
        ]:
            self.client.post(
                reverse('reserve:recommendation_status', args=[recommendation.pk]),
                {'status': status},
            )
            candidate.refresh_from_db()
            self.assertEqual(candidate.status, expected)

        # история не теряется при смене статусов
        self.assertGreaterEqual(candidate.events.count(), 8)
        self.assertEqual(candidate.recommendations_count, 1)

    def test_company_rejection_returns_candidate_to_reserve(self):
        candidate = self._candidate()
        recommendation = ReserveRecommendation.objects.create(
            candidate=candidate, company=self.company,
            sent_on=timezone.localdate(), status=RecommendationStatus.SENT,
        )
        services.change_status(candidate, CandidateStatus.PROPOSED, user=self.user)
        self.client.post(
            reverse('reserve:recommendation_status', args=[recommendation.pk]),
            {'status': RecommendationStatus.COMPANY_REJECT},
        )
        candidate.refresh_from_db()
        self.assertEqual(candidate.status, CandidateStatus.RESERVE)

    def test_recommendation_requires_a_company(self):
        candidate = self._candidate()
        response = self.client.post(reverse('reserve:recommend', args=[candidate.pk]), {
            'vacancy': 'Junior Backend', 'sent_on': timezone.localdate().isoformat(),
            'status': RecommendationStatus.SENT,
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(ReserveRecommendation.objects.exists())


class ReserveListTests(TestCase):
    """Поиск, фильтры и сортировка списка."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='head', password='pass12345', role=User.Role.HEAD,
        )
        self.client.force_login(self.user)
        backend = Specialization.objects.create(name='Backend')
        design = Specialization.objects.create(name='UX/UI')
        self.first = ReserveCandidate.objects.create(
            full_name='Демобай Демобаев', specialization=backend, city='Бишкек',
            skills='Python, Django', rating=9, geekspro_level='middle',
            status=CandidateStatus.RESERVE, is_looking_for_job=True,
        )
        self.second = ReserveCandidate.objects.create(
            full_name='Псигуль Квотова', specialization=design, city='Ош',
            skills='Figma', rating=7, geekspro_level='junior',
            status=CandidateStatus.EMPLOYED, is_looking_for_job=False,
        )

    def test_search_by_name(self):
        response = self.client.get(reverse('reserve:list'), {'q': 'Псигуль'})
        self.assertContains(response, 'Псигуль Квотова')
        self.assertNotContains(response, 'Демобай Демобаев')

    def test_filter_by_skill_and_city(self):
        response = self.client.get(reverse('reserve:list'), {'skill': 'Django'})
        self.assertContains(response, 'Демобай Демобаев')
        self.assertNotContains(response, 'Псигуль Квотова')
        response = self.client.get(reverse('reserve:list'), {'city': 'Ош'})
        self.assertContains(response, 'Псигуль Квотова')
        self.assertNotContains(response, 'Демобай Демобаев')

    def test_filter_by_rating_status_and_readiness(self):
        response = self.client.get(reverse('reserve:list'), {'rating': '8'})
        self.assertContains(response, 'Демобай Демобаев')
        self.assertNotContains(response, 'Псигуль Квотова')
        response = self.client.get(
            reverse('reserve:list'), {'status': CandidateStatus.EMPLOYED},
        )
        self.assertContains(response, 'Псигуль Квотова')
        response = self.client.get(reverse('reserve:list'), {'readiness': 'looking'})
        self.assertContains(response, 'Демобай Демобаев')
        self.assertNotContains(response, 'Псигуль Квотова')

    def test_sorting_by_name_and_rating(self):
        by_rating, _ = self.client.get(reverse('reserve:list')).context['page'], None
        self.assertEqual(list(by_rating)[0], self.first)
        by_name = self.client.get(reverse('reserve:list'), {'sort': 'name'}).context['page']
        self.assertEqual(list(by_name)[0], self.first)


class ReserveFromInternTests(TestCase):
    """Кандидат из карточки стажёра — данные не перепечатываются."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='head', password='pass12345', role=User.Role.HEAD,
        )
        self.client.force_login(self.user)
        self.spec = Specialization.objects.create(name='Backend')
        self.intern = Intern.objects.create(
            full_name='Стажёр Стажёров', phone='0700999111',
            city='Бишкек', specialization=self.spec,
        )

    def test_candidate_inherits_intern_data(self):
        self.client.post(reverse('reserve:from_intern'), {'intern': self.intern.pk})
        candidate = ReserveCandidate.objects.get(intern=self.intern)
        self.assertEqual(candidate.full_name, 'Стажёр Стажёров')
        self.assertEqual(candidate.phone, '0700999111')
        self.assertEqual(candidate.specialization, self.spec)
        self.assertTrue(candidate.events.filter(kind='created').exists())

    def test_same_intern_is_not_duplicated(self):
        self.client.post(reverse('reserve:from_intern'), {'intern': self.intern.pk})
        self.client.post(reverse('reserve:from_intern'), {'intern': self.intern.pk})
        self.assertEqual(ReserveCandidate.objects.filter(intern=self.intern).count(), 1)


class ReserveOverviewTests(TestCase):
    """Главная резерва: сводка по направлениям."""

    def setUp(self):
        from apps.training.models import Specialization

        self.user = User.objects.create_user(
            username='viewer', password='pass12345',
        )
        self.client.force_login(self.user)
        self.url = reverse('reserve:overview')
        self.backend = Specialization.objects.create(name='Backend')
        self.design = Specialization.objects.create(name='UX/UI')
        make = ReserveCandidate.objects.create
        make(full_name='Новый', specialization=self.backend, status=CandidateStatus.NEW)
        make(full_name='Проверяется', specialization=self.backend, status=CandidateStatus.REVIEW)
        make(full_name='В резерве', specialization=self.backend, status=CandidateStatus.RESERVE)
        make(full_name='На интервью', specialization=self.backend, status=CandidateStatus.INTERVIEW)
        make(full_name='С оффером', specialization=self.backend, status=CandidateStatus.OFFER)
        make(full_name='Работает', specialization=self.backend, status=CandidateStatus.EMPLOYED)
        make(full_name='Дизайнер', specialization=self.design, status=CandidateStatus.RESERVE)
        make(full_name='Ничей', specialization=None, status=CandidateStatus.RESERVE)

    def _row(self, response, key):
        return next(row for row in response.context['rows'] if row['key'] == key)

    def test_reserve_opens_on_the_summary(self):
        response = self.client.get('/reserve/')
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'reserve/overview.html')

    def test_counts_split_by_stage(self):
        response = self.client.get(self.url)
        row = self._row(response, str(self.backend.pk))
        self.assertEqual(row['total'], 6)
        self.assertEqual(row['new'], 2)
        self.assertEqual(row['available'], 1)
        self.assertEqual(row['in_progress'], 2)
        self.assertEqual(row['employed'], 1)

    def test_totals_sum_every_direction(self):
        totals = self.client.get(self.url).context['totals']
        self.assertEqual(totals['total'], 8)
        self.assertEqual(totals['available'], 3)

    def test_candidates_without_direction_get_their_own_row(self):
        response = self.client.get(self.url)
        row = self._row(response, 'none')
        self.assertIsNone(row['specialization'])
        self.assertEqual(row['total'], 1)
        self.assertContains(response, 'Без направления')

    def test_direction_leads_to_the_filtered_list(self):
        response = self.client.get(
            reverse('reserve:list'), {'specialization': self.backend.pk},
        )
        names = [c.full_name for c in response.context['page'].object_list]
        self.assertIn('В резерве', names)
        self.assertNotIn('Дизайнер', names)

    def test_row_without_direction_leads_to_its_own_people(self):
        response = self.client.get(
            reverse('reserve:list'), {'specialization': 'none'},
        )
        names = [c.full_name for c in response.context['page'].object_list]
        self.assertEqual(names, ['Ничей'])

    def test_archived_candidates_are_not_counted(self):
        candidate = ReserveCandidate.objects.get(full_name='Дизайнер')
        candidate.archive()
        row = self._row(self.client.get(self.url), str(self.design.pk))
        self.assertEqual(row['total'], 0)

    def test_direction_without_candidates_is_still_listed(self):
        from apps.training.models import Specialization

        Specialization.objects.create(name='DevOps')
        response = self.client.get(self.url)
        self.assertContains(response, 'DevOps')


class ReserveShareLinkTests(TestCase):
    """Ссылка на профиль кандидата для работодателя."""

    def setUp(self):
        self.head = User.objects.create_user(
            username='head', password='pass12345', role=User.Role.HEAD,
        )
        self.candidate = ReserveCandidate.objects.create(
            full_name='Профиль Кандидатов', phone='0700111222',
            email='candidate@example.com', skills='Python, Django',
            comment_pm='Внутренний комментарий PM', improvements='Тайное улучшение',
        )

    def test_head_creates_link_and_it_is_logged(self):
        self.client.force_login(self.head)
        self.client.post(
            reverse('reserve:share_create', args=[self.candidate.pk]),
            {'recipient': 'ОсОО Ромашка', 'ttl_days': '30', 'show_contacts': 'on'},
        )
        link = ReserveShareLink.objects.get(candidate=self.candidate)
        self.assertEqual(link.recipient, 'ОсОО Ромашка')
        self.assertTrue(link.show_contacts)
        self.assertIsNotNone(link.expires_at)
        self.assertTrue(self.candidate.events.filter(kind=EventKind.SHARED).exists())

    def test_viewer_without_role_cannot_create_link(self):
        # Не head/administrator — ни ПМ, ни тимлид не подходят: у обоих
        # свой портал и туда их уводит middleware раньше, чем сработает
        # проверка прав здесь, так что для этой проверки нужна роль без
        # отдельного портала.
        viewer = User.objects.create_user(
            username='viewer', password='pass12345', role=User.Role.INTERN,
        )
        self.client.force_login(viewer)
        response = self.client.post(
            reverse('reserve:share_create', args=[self.candidate.pk]), {},
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(ReserveShareLink.objects.exists())

    def test_public_profile_hides_internal_data_and_counts_views(self):
        link = services.issue_share_link(self.candidate, user=self.head)
        response = self.client.get(link.get_absolute_url())
        self.assertContains(response, 'Профиль Кандидатов')
        self.assertContains(response, 'Django')
        self.assertNotContains(response, 'Внутренний комментарий PM')
        self.assertNotContains(response, 'Тайное улучшение')
        self.assertNotContains(response, '0700111222')
        link.refresh_from_db()
        self.assertEqual(link.views, 1)
        self.assertIsNotNone(link.viewed_at)

    def test_contacts_shown_only_when_allowed(self):
        link = services.issue_share_link(self.candidate, show_contacts=True)
        response = self.client.get(link.get_absolute_url())
        self.assertContains(response, '0700111222')
        self.assertContains(response, 'candidate@example.com')

    def test_several_links_live_side_by_side(self):
        first = services.issue_share_link(self.candidate, recipient='Первая')
        services.issue_share_link(self.candidate, recipient='Вторая')
        first.refresh_from_db()
        self.assertTrue(first.is_open)

    def test_disabled_or_expired_link_does_not_open(self):
        disabled = services.issue_share_link(self.candidate)
        disabled.deactivate()
        self.assertEqual(self.client.get(disabled.get_absolute_url()).status_code, 404)
        expired = services.issue_share_link(self.candidate)
        expired.expires_at = timezone.now() - timedelta(minutes=1)
        expired.save()
        self.assertEqual(self.client.get(expired.get_absolute_url()).status_code, 404)

    def test_archived_candidate_is_not_shown(self):
        link = services.issue_share_link(self.candidate)
        self.candidate.is_archived = True
        self.candidate.save()
        self.assertEqual(self.client.get(link.get_absolute_url()).status_code, 404)

    def test_detail_page_lists_active_links(self):
        services.issue_share_link(self.candidate, recipient='ОсОО Ромашка')
        self.client.force_login(self.head)
        response = self.client.get(self.candidate.get_absolute_url())
        self.assertContains(response, 'Поделиться профилем')
        self.assertContains(response, 'ОсОО Ромашка')


class ReserveShareCollectionTests(TestCase):
    """Одна ссылка на подборку из нескольких кандидатов."""

    def setUp(self):
        self.head = User.objects.create_user(
            username='head', password='pass12345', role=User.Role.HEAD,
        )
        self.spec = Specialization.objects.create(name='Backend')
        self.first = ReserveCandidate.objects.create(
            full_name='Первый Кандидат', specialization=self.spec, phone='0700000001',
        )
        self.second = ReserveCandidate.objects.create(
            full_name='Второй Кандидат', specialization=self.spec,
        )
        self.other = ReserveCandidate.objects.create(full_name='Чужой Кандидат')

    def test_selected_candidates_go_into_collection(self):
        self.client.force_login(self.head)
        self.client.post(reverse('reserve:share_collection_create'), {
            'candidates': [self.first.pk, self.second.pk],
            'recipient': 'ОсОО Ромашка', 'ttl_days': '30',
        })
        link = ReserveShareLink.objects.get()
        self.assertTrue(link.is_collection)
        self.assertEqual(set(link.candidates.all()), {self.first, self.second})
        self.assertTrue(self.first.events.filter(kind=EventKind.SHARED).exists())

    def test_without_selection_takes_current_filter(self):
        self.client.force_login(self.head)
        self.client.post(reverse('reserve:share_collection_create'), {
            'filters': f'specialization={self.spec.pk}', 'ttl_days': '30',
        })
        link = ReserveShareLink.objects.get()
        self.assertEqual(set(link.candidates.all()), {self.first, self.second})

    def test_viewer_without_role_cannot_create_collection(self):
        # См. комментарий в test_viewer_without_role_cannot_create_link —
        # ПМ/тимлид тут не годятся, у них свой портал и свой middleware.
        viewer = User.objects.create_user(
            username='viewer2', password='pass12345', role=User.Role.INTERN,
        )
        self.client.force_login(viewer)
        response = self.client.post(
            reverse('reserve:share_collection_create'), {'ttl_days': '30'},
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(ReserveShareLink.objects.exists())

    def test_public_collection_lists_and_opens_only_its_candidates(self):
        link = services.issue_share_collection([self.first, self.second])
        url = link.get_absolute_url()
        response = self.client.get(url)
        self.assertContains(response, 'Первый Кандидат')
        self.assertContains(response, 'Второй Кандидат')
        self.assertNotContains(response, 'Чужой Кандидат')
        self.assertNotContains(response, '0700000001')

        profile = self.client.get(url, {'c': self.first.pk})
        self.assertContains(profile, 'Первый Кандидат')
        self.assertContains(profile, 'Все кандидаты подборки')

        foreign = self.client.get(url, {'c': self.other.pk})
        self.assertRedirects(foreign, url, fetch_redirect_response=False)

        link.refresh_from_db()
        self.assertEqual(link.views, 1)

    def test_list_page_shows_active_collections(self):
        services.issue_share_collection([self.first], recipient='ОсОО Ромашка')
        self.client.force_login(self.head)
        response = self.client.get(reverse('reserve:list'))
        self.assertContains(response, 'Подборки для работодателей')
        self.assertContains(response, 'ОсОО Ромашка')

    def test_list_shows_only_collections_of_filtered_direction(self):
        frontend = Specialization.objects.create(name='Frontend')
        front = ReserveCandidate.objects.create(
            full_name='Фронт Кандидат', specialization=frontend,
        )
        services.issue_share_collection([self.first], recipient='Для бэкенда')
        services.issue_share_collection([front], recipient='Для фронтенда')
        self.client.force_login(self.head)
        response = self.client.get(
            reverse('reserve:list'), {'specialization': frontend.pk},
        )
        self.assertContains(response, 'Для фронтенда')
        self.assertNotContains(response, 'Для бэкенда')
        response = self.client.get(reverse('reserve:list'))
        self.assertContains(response, 'Для фронтенда')
        self.assertContains(response, 'Для бэкенда')

    def test_overview_shares_all_directions_grouped(self):
        frontend = Specialization.objects.create(name='Frontend')
        ReserveCandidate.objects.create(full_name='Фронт Кандидат', specialization=frontend)
        self.client.force_login(self.head)
        overview = self.client.get(reverse('reserve:overview'))
        self.assertContains(overview, 'Поделиться всеми направлениями')

        response = self.client.post(reverse('reserve:share_collection_create'), {
            'filters': '', 'next': 'overview', 'ttl_days': '30',
        })
        self.assertRedirects(
            response, reverse('reserve:overview'), fetch_redirect_response=False,
        )
        link = ReserveShareLink.objects.get()
        self.assertEqual(link.candidates.count(), 4)

        public = self.client.get(link.get_absolute_url())
        # Backend, Frontend и кандидат без направления
        self.assertContains(public, 'class="share-group"', count=3)
        self.assertContains(public, 'Без направления')
        self.assertContains(public, 'Фронт Кандидат')

        overview = self.client.get(reverse('reserve:overview'))
        self.assertContains(overview, 'Подборки для работодателей')

    def test_overview_shares_one_direction(self):
        self.client.force_login(self.head)
        self.client.post(reverse('reserve:share_collection_create'), {
            'filters': f'specialization={self.spec.pk}', 'next': 'overview',
            'ttl_days': '30',
        })
        link = ReserveShareLink.objects.get()
        self.assertEqual(set(link.candidates.all()), {self.first, self.second})
        public = self.client.get(link.get_absolute_url())
        self.assertNotContains(public, 'class="share-group"')


class ReserveInternLinkTests(TestCase):
    """Карточка в резерве связывается с карточкой человека по контактам."""

    def setUp(self):
        self.person = Intern.objects.create(
            full_name='Ракетбеков Дельтабек', phone='+996 (555) 12-34-56',
            email='Deltabek@Example.com', telegram='@deltabek_dev',
        )

    def _candidate(self, **fields):
        data = {'full_name': 'Дельтабек', **fields}
        return ReserveCandidate.objects.create(**data)

    def test_phone_matches_in_any_format(self):
        candidate = self._candidate(phone='0555123456')
        self.assertEqual(services.find_intern_for(candidate), self.person)

    def test_email_and_telegram_match(self):
        self.assertEqual(
            services.find_intern_for(self._candidate(email='deltabek@example.com')), self.person,
        )
        self.assertEqual(
            services.find_intern_for(self._candidate(telegram='deltabek_dev')), self.person,
        )

    def test_ambiguous_match_is_not_linked(self):
        Intern.objects.create(full_name='Однофамилец', phone='0555123456')
        candidate = self._candidate(phone='0555123456')
        self.assertIsNone(services.link_to_intern(candidate))
        candidate.refresh_from_db()
        self.assertIsNone(candidate.intern_id)

    def test_already_linked_person_is_not_taken_twice(self):
        self._candidate(phone='0555123456', intern=self.person)
        other = self._candidate(full_name='Второй', phone='0555123456')
        self.assertIsNone(services.find_intern_for(other))

    def test_no_contacts_no_link(self):
        self.assertIsNone(services.find_intern_for(self._candidate()))

    def test_link_writes_history(self):
        candidate = self._candidate(phone='0555123456')
        services.link_to_intern(candidate)
        candidate.refresh_from_db()
        self.assertEqual(candidate.intern, self.person)
        self.assertTrue(candidate.events.filter(title__startswith='Связан').exists())

    def test_application_links_automatically(self):
        invite = services.issue_invite()
        self.client.post(invite.get_absolute_url(), dict(APPLICATION, phone='0555123456'))
        candidate = ReserveCandidate.objects.get(phone='0555123456')
        self.assertEqual(candidate.intern, self.person)

    def test_command_previews_then_links(self):
        from io import StringIO

        from django.core.management import call_command

        candidate = self._candidate(phone='0555123456')
        out = StringIO()
        call_command('link_reserve_candidates', stdout=out)
        candidate.refresh_from_db()
        self.assertIsNone(candidate.intern_id)
        self.assertIn('Будет связано: 1', out.getvalue())
        call_command('link_reserve_candidates', apply=True, stdout=StringIO())
        candidate.refresh_from_db()
        self.assertEqual(candidate.intern, self.person)


class ReserveSheetTests(TestCase):
    """Выгрузка резерва в Google-таблицу: кто на какой лист и в каком порядке."""

    def setUp(self):
        from apps.reserve import gsheets

        self.gsheets = gsheets
        self.backend = Specialization.objects.create(name="Backend")
        self.design = Specialization.objects.create(name="UX/UI дизайн")

    def _candidate(self, **kwargs):
        from apps.reserve.models import CandidatePool

        data = {
            "full_name": "Кандидат", "status": CandidateStatus.RESERVE,
            "pool": CandidatePool.INTERN,
        }
        data.update(kwargs)
        return ReserveCandidate.objects.create(**data)

    def test_only_interns_go_to_the_sheet(self):
        from apps.reserve.models import CandidatePool

        self._candidate(full_name="Стажёр Бэкенд", specialization=self.backend)
        self._candidate(
            full_name="Тимлид Бэкенд", specialization=self.backend,
            pool=CandidatePool.STAFF,
        )
        rows = self.gsheets.rows_by_direction()["backend"]
        self.assertEqual([row[0] for row in rows], ["Стажёр Бэкенд"])

    def test_lead_in_reserve_is_staff_and_stays_out(self):
        from apps.reserve import services
        from apps.reserve.models import CandidatePool

        lead = Intern.objects.create(full_name="Тимлид Наш", specialization=self.backend)
        card = services.ensure_lead_in_reserve(lead)
        self.assertEqual(card.pool, CandidatePool.STAFF)
        self.assertEqual(self.gsheets.rows_by_direction(), {})

    def test_row_matches_sheet_columns(self):
        self._candidate(
            full_name="Гаммабек Образцов", specialization=self.backend,
            phone="0555123456", telegram="@gammabek",
            study_end=timezone.localdate().replace(month=6, day=20),
        )
        groups = self.gsheets.rows_by_direction()
        self.assertEqual(list(groups), ["backend"])
        name, direction, projects, spacer, end, phone, telegram = groups["backend"][0]
        self.assertEqual(name, "Гаммабек Образцов")
        self.assertEqual(direction, "Backend")
        self.assertEqual(projects, "")
        self.assertEqual(spacer, "")
        self.assertTrue(end.endswith(str(timezone.localdate().year)))
        self.assertEqual(phone, "0555123456")
        self.assertEqual(telegram, "@gammabek")

    def test_each_direction_goes_to_its_own_tab(self):
        self._candidate(full_name="Ян Дизайнер", specialization=self.design)
        self._candidate(full_name="Борис Бэкенд", specialization=self.backend)
        self._candidate(full_name="Анна Бэкенд", specialization=self.backend)
        groups = self.gsheets.rows_by_direction()
        self.assertEqual(sorted(groups), ["backend", "uxui"])
        self.assertEqual(
            [row[0] for row in groups["backend"]], ["Анна Бэкенд", "Борис Бэкенд"],
        )
        self.assertEqual([row[0] for row in groups["uxui"]], ["Ян Дизайнер"])

    def test_priority_lifts_person_inside_direction(self):
        self._candidate(full_name="Анна Бэкенд", specialization=self.backend)
        self._candidate(full_name="Борис Бэкенд", specialization=self.backend, priority=5)
        rows = self.gsheets.rows_by_direction()["backend"]
        self.assertEqual([row[0] for row in rows], ["Борис Бэкенд", "Анна Бэкенд"])

    def test_projects_of_intern_are_listed(self):
        from apps.projects.models import Project
        from apps.teams.models import TeamMember, TeamRole

        person = Intern.objects.create(full_name="Гаммабек Образцов", specialization=self.backend)
        for name in ("Балажан", "БилимОрдо"):
            TeamMember.objects.create(
                project=Project.objects.create(name=name), intern=person,
                role=TeamRole.BACKEND,
            )
        self._candidate(
            full_name="Гаммабек Образцов", specialization=self.backend, intern=person,
        )
        row = self.gsheets.rows_by_direction()["backend"][0]
        self.assertIn("Балажан", row[2])
        self.assertIn("БилимОрдо", row[2])

    def test_membership_without_project_is_skipped(self):
        from apps.teams.models import TeamMember, TeamRole

        person = Intern.objects.create(full_name="Гаммабек Образцов", specialization=self.backend)
        TeamMember.objects.create(project=None, intern=person, role=TeamRole.BACKEND)
        self._candidate(
            full_name="Гаммабек Образцов", specialization=self.backend, intern=person,
        )
        self.assertEqual(self.gsheets.rows_by_direction()["backend"][0][2], "")

    def test_archived_candidate_is_not_exported(self):
        person = self._candidate(full_name="Ушёл Совсем", specialization=self.backend)
        person.is_archived = True
        person.save(update_fields=["is_archived"])
        self.assertEqual(self.gsheets.rows_by_direction(), {})

    def test_unknown_direction_has_no_tab(self):
        self._candidate(full_name="Свой Путь", direction_other="Копирайтинг")
        self.assertEqual(self.gsheets.rows_by_direction(), {})

    def test_values_go_to_sheet_as_text(self):
        # «+996 …» Google Sheets примет за формулу, если писать USER_ENTERED
        self.assertEqual(self.gsheets.VALUE_INPUT, "RAW")

    def test_phone_keeps_plus_and_leading_zero(self):
        self._candidate(
            full_name="Гаммабек Образцов", specialization=self.backend, phone="+996 700 333 444",
        )
        self._candidate(
            full_name="Юнитбек Образцов", specialization=self.backend, phone="0700990677",
        )
        phones = [row[5] for row in self.gsheets.rows_by_direction()["backend"]]
        self.assertIn("+996 700 333 444", phones)
        self.assertIn("0700990677", phones)

    def test_tab_key_reads_sheet_titles(self):
        self.assertEqual(self.gsheets.tab_key("Список стажеров Backend"), "backend")
        self.assertEqual(self.gsheets.tab_key("Список стажеров UXUI"), "uxui")
        self.assertEqual(self.gsheets.tab_key("Список стажеров Mobile"), "mobile")
        self.assertEqual(self.gsheets.tab_key("Прочее"), "")

    def test_without_credentials_nothing_happens(self):
        from django.test import override_settings

        with override_settings(GOOGLE_SHEETS_CREDENTIALS_FILE=""):
            self.assertFalse(self.gsheets.is_configured())
            self.assertFalse(self.gsheets.sync("тест"))
            # сохранение кандидата не должно ничего требовать от таблицы
            self._candidate(full_name="Тихий Кандидат")

    def test_save_schedules_sync_when_configured(self):
        calls = []
        original = self.gsheets.is_configured
        self.gsheets.is_configured = lambda: True
        from apps.reserve import tasks

        original_delay = tasks.sync_reserve_sheet.delay
        tasks.sync_reserve_sheet.delay = lambda reason='': calls.append(reason)
        try:
            with self.captureOnCommitCallbacks(execute=True):
                self._candidate(full_name="Новый Кандидат")
        finally:
            self.gsheets.is_configured = original
            tasks.sync_reserve_sheet.delay = original_delay
        self.assertEqual(len(calls), 1, calls)

    def test_command_previews_without_credentials(self):
        from io import StringIO

        from django.core.management import call_command
        from django.test import override_settings

        self._candidate(full_name="Гаммабек Образцов", specialization=self.backend)
        self._candidate(full_name="Свой Путь", direction_other="Копирайтинг")
        out = StringIO()
        with override_settings(GOOGLE_SHEETS_CREDENTIALS_FILE=""):
            call_command("sync_reserve_sheet", stdout=out)
        text = out.getvalue()
        self.assertIn("Кандидатов к выгрузке: 1", text)
        self.assertIn("Гаммабек Образцов", text)
        self.assertIn("Без подходящего листа", text)
        self.assertIn("не настроена", text)
