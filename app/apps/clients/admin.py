from django.contrib import admin

from apps.clients.models import Client, ClientContact


class ClientContactInline(admin.TabularInline):
    model = ClientContact
    extra = 0


@admin.register(Client)
class ClientAdmin(admin.ModelAdmin):
    list_display = ('organization', 'contact_name', 'phone', 'city', 'is_archived')
    list_filter = ('city', 'is_archived')
    search_fields = ('organization', 'contact_name', 'phone', 'email')
    inlines = [ClientContactInline]
