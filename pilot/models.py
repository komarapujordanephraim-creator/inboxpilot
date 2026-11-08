from django.conf import settings
from django.db import models


class Order(models.Model):
    PLACED, SHIPPED, DELIVERED, REFUNDED = "placed", "shipped", "delivered", "refunded"
    STATUSES = [(PLACED, "Placed"), (SHIPPED, "Shipped"), (DELIVERED, "Delivered"), (REFUNDED, "Refunded")]

    number = models.CharField(max_length=20, unique=True)
    customer_email = models.EmailField()
    customer_phone = models.CharField(max_length=20, blank=True)
    total = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(max_length=12, choices=STATUSES, default=PLACED)

    def __str__(self):
        return self.number


class Message(models.Model):
    CHANNELS = [("whatsapp", "WhatsApp"), ("email", "Email"), ("web", "Web chat")]
    NEW, AUTO, APPROVAL, IGNORED, DONE = "new", "auto", "approval", "ignored", "done"
    STATES = [(NEW, "New"), (AUTO, "Handled automatically"), (APPROVAL, "Waiting for approval"),
              (IGNORED, "Ignored (spam)"), (DONE, "Closed")]

    channel = models.CharField(max_length=10, choices=CHANNELS)
    sender = models.CharField(max_length=200)
    body = models.TextField()
    received_at = models.DateTimeField(auto_now_add=True)
    intent = models.CharField(max_length=20, blank=True)
    confidence = models.FloatField(default=0)
    extracted = models.JSONField(default=dict, blank=True)
    flagged_injection = models.BooleanField(default=False)
    reasons = models.JSONField(default=list, blank=True)
    state = models.CharField(max_length=10, choices=STATES, default=NEW)
    reply = models.TextField(blank=True)

    class Meta:
        ordering = ["-received_at", "-id"]

    def __str__(self):
        return "%s from %s" % (self.get_channel_display(), self.sender)


class ProposedAction(models.Model):
    REPLY, REFUND, LEAD, ESCALATE = "reply", "refund", "lead", "escalate"
    KINDS = [(REPLY, "Send reply"), (REFUND, "Issue refund"), (LEAD, "Create lead"), (ESCALATE, "Escalate to a person")]
    PENDING, APPROVED, REJECTED, EXECUTED = "pending", "approved", "rejected", "executed"
    STATES = [(PENDING, "Pending"), (APPROVED, "Approved"), (REJECTED, "Rejected"), (EXECUTED, "Executed")]

    message = models.ForeignKey(Message, on_delete=models.CASCADE, related_name="actions")
    kind = models.CharField(max_length=10, choices=KINDS)
    params = models.JSONField(default=dict, blank=True)
    risk = models.CharField(max_length=6, choices=[("low", "Low"), ("high", "High")])
    state = models.CharField(max_length=10, choices=STATES, default=PENDING)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["id"]


class Lead(models.Model):
    name = models.CharField(max_length=120, blank=True)
    email = models.CharField(max_length=200)
    need = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    source = models.ForeignKey(Message, null=True, on_delete=models.SET_NULL)


class AuditLog(models.Model):
    at = models.DateTimeField(auto_now_add=True)
    actor = models.CharField(max_length=150)  # "autopilot" or a username
    event = models.CharField(max_length=60)
    detail = models.TextField(blank=True)
    message = models.ForeignKey(Message, null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        ordering = ["-at", "-id"]
