from django.contrib import admin

from .models import AuditLog, Lead, Message, Order, ProposedAction

admin.site.register([Order, Lead])


class ActionInline(admin.TabularInline):
    model = ProposedAction
    extra = 0


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ("received_at", "channel", "sender", "intent", "state", "flagged_injection")
    list_filter = ("state", "intent", "channel")
    inlines = [ActionInline]


@admin.register(AuditLog)
class AuditAdmin(admin.ModelAdmin):
    list_display = ("at", "actor", "event", "detail")
    readonly_fields = ("at", "actor", "event", "detail", "message")
