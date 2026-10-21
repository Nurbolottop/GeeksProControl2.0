from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import User

Model = get_user_model()


class PMScopeMiddlewareTests(TestCase):
    """ПМ заперт в /pm/, у head/administrator ничего не меняется."""

    def setUp(self):
        self.pm = Model.objects.create_user(
            username="+996700000001", password="x", role=User.Role.PROJECT_MANAGER,
        )
        self.head = Model.objects.create_user(
            username="head", password="x", role=User.Role.HEAD,
        )

    def test_pm_redirected_away_from_admin_pages(self):
        self.client.force_login(self.pm)
        response = self.client.get(reverse("projects:list"))
        self.assertRedirects(response, reverse("pm_portal:dashboard"))

    def test_pm_can_reach_pm_portal(self):
        self.client.force_login(self.pm)
        response = self.client.get(reverse("pm_portal:dashboard"))
        self.assertEqual(response.status_code, 200)

    def test_head_unaffected_on_admin_pages(self):
        self.client.force_login(self.head)
        for name in ("projects:list", "interns:list", "teams:lead_list"):
            response = self.client.get(reverse(name))
            self.assertEqual(response.status_code, 200)

    def test_head_redirected_away_from_pm_portal(self):
        """Портал ПМ — только для роли pm, остальных уводит на обычный сайт."""
        self.client.force_login(self.head)
        response = self.client.get(reverse("pm_portal:dashboard"))
        self.assertRedirects(response, "/")

    def test_anonymous_still_redirected_to_login(self):
        response = self.client.get(reverse("projects:list"))
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith("/login/"))


class LeadScopeMiddlewareTests(TestCase):
    """Тимлид заперт в /lead/, у head/administrator/ПМ ничего не меняется."""

    def setUp(self):
        self.lead = Model.objects.create_user(
            username="+996700000010", password="x", role=User.Role.TEAM_LEAD,
        )
        self.head = Model.objects.create_user(
            username="head10", password="x", role=User.Role.HEAD,
        )
        self.pm = Model.objects.create_user(
            username="+996700000011", password="x", role=User.Role.PROJECT_MANAGER,
        )

    def test_lead_redirected_away_from_admin_pages(self):
        self.client.force_login(self.lead)
        response = self.client.get(reverse("projects:list"))
        self.assertRedirects(response, reverse("lead_portal:dashboard"))

    def test_lead_can_reach_lead_portal(self):
        self.client.force_login(self.lead)
        response = self.client.get(reverse("lead_portal:dashboard"))
        self.assertEqual(response.status_code, 200)

    def test_head_unaffected_on_admin_pages(self):
        self.client.force_login(self.head)
        for name in ("projects:list", "interns:list", "teams:lead_list"):
            response = self.client.get(reverse(name))
            self.assertEqual(response.status_code, 200)

    def test_head_redirected_away_from_lead_portal(self):
        self.client.force_login(self.head)
        response = self.client.get(reverse("lead_portal:dashboard"))
        self.assertRedirects(response, "/")

    def test_pm_redirected_away_from_lead_portal(self):
        """У ПМ своя роль и свой /pm/ — на /lead/ его не пускает,
        уводит сразу в его собственный портал."""
        self.client.force_login(self.pm)
        response = self.client.get(reverse("lead_portal:dashboard"))
        self.assertRedirects(response, reverse("pm_portal:dashboard"))

    def test_lead_redirected_away_from_pm_portal(self):
        """Тимлида на /pm/ не пускает, уводит сразу в его портал —
        не зацикливая через общий сайт."""
        self.client.force_login(self.lead)
        response = self.client.get(reverse("pm_portal:dashboard"))
        self.assertRedirects(response, reverse("lead_portal:dashboard"))


class RoleAwareLoginTests(TestCase):
    """Логин: ПМ/тимлид уходят на свой портал, остальные — как раньше."""

    def setUp(self):
        self.pm = Model.objects.create_user(
            username="+996700000002", password="secretpass", role=User.Role.PROJECT_MANAGER,
        )
        self.lead = Model.objects.create_user(
            username="+996700000003", password="secretpass", role=User.Role.TEAM_LEAD,
        )
        self.head = Model.objects.create_user(
            username="head2", password="secretpass", role=User.Role.HEAD,
        )

    def test_pm_login_redirects_to_pm_portal(self):
        response = self.client.post(reverse("login"), {
            "username": "+996700000002", "password": "secretpass",
        })
        self.assertRedirects(response, reverse("pm_portal:dashboard"))

    def test_lead_login_redirects_to_lead_portal(self):
        response = self.client.post(reverse("login"), {
            "username": "+996700000003", "password": "secretpass",
        })
        self.assertRedirects(response, reverse("lead_portal:dashboard"))

    def test_head_login_redirects_to_home(self):
        response = self.client.post(reverse("login"), {
            "username": "head2", "password": "secretpass",
        })
        self.assertRedirects(response, "/")

    def test_login_form_labels_field_as_phone(self):
        response = self.client.get(reverse("login"))
        self.assertContains(response, "Телефон")


class DisplayNameTests(TestCase):
    """У ПМ/тимлида логин — номер телефона, а не имя, поэтому шапка
    портала должна показывать ФИО из карточки стажёра, не username."""

    def test_shows_linked_intern_full_name(self):
        from apps.interns.models import Intern

        user = Model.objects.create_user(
            username="+996700000050", password="x", role=User.Role.TEAM_LEAD,
        )
        Intern.objects.create(full_name="Макетбаев Пробобек", user=user)
        self.assertEqual(user.display_name, "Макетбаев Пробобек")

    def test_falls_back_to_username_without_linked_intern(self):
        user = Model.objects.create_user(username="head3", password="x")
        self.assertEqual(user.display_name, "head3")

    def test_falls_back_to_full_name_field_without_linked_intern(self):
        user = Model.objects.create_user(
            username="head4", password="x",
            first_name="Тест", last_name="Тестов",
        )
        self.assertEqual(user.display_name, "Тест Тестов")


class PhoneLoginFormatTests(TestCase):
    """Номер логином записан по-разному, а человек вводит его как помнит:
    формат не должен мешать войти."""

    def setUp(self):
        self.plus = User.objects.create_user(
            username="+996700123456", password="Pass12345", role=User.Role.PROJECT_MANAGER,
        )
        self.zero = User.objects.create_user(
            username="0770654321", password="Pass54321", role=User.Role.TEAM_LEAD,
        )

    def _login(self, username, password):
        return self.client.post(
            reverse("login"), {"username": username, "password": password},
        )

    def test_exact_login_still_works(self):
        self.assertEqual(self._login("+996700123456", "Pass12345").status_code, 302)

    def test_plus_user_enters_local_format(self):
        for typed in ("0700123456", "700123456", "996700123456", "+996 700 12 34 56"):
            with self.subTest(typed=typed):
                self.client.logout()
                self.assertEqual(self._login(typed, "Pass12345").status_code, 302)

    def test_local_user_enters_international_format(self):
        for typed in ("+996770654321", "996 770 654 321", "0770-654-321"):
            with self.subTest(typed=typed):
                self.client.logout()
                self.assertEqual(self._login(typed, "Pass54321").status_code, 302)

    def test_wrong_password_still_rejected(self):
        response = self._login("0700123456", "неверный")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Неверный логин или пароль")

    def test_unknown_number_rejected(self):
        response = self._login("0700000000", "Pass12345")
        self.assertEqual(response.status_code, 200)

    def test_inactive_user_cannot_enter(self):
        self.zero.is_active = False
        self.zero.save(update_fields=["is_active"])
        self.assertEqual(self._login("+996770654321", "Pass54321").status_code, 200)

    def test_name_login_unaffected(self):
        User.objects.create_user(username="probobek", password="Pass00000")
        self.assertEqual(self._login("probobek", "Pass00000").status_code, 302)
