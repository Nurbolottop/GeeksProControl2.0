from django.apps import AppConfig


class InternsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.interns'
    verbose_name = 'Стажёры'

    def ready(self):
        from django.db.models.signals import post_save

        from apps.interns.internships import on_membership_saved

        post_save.connect(on_membership_saved, sender='teams.TeamMember', dispatch_uid='internship-on-join')
