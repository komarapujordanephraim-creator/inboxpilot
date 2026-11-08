"""Glue between the engine and the database. Every change is audited."""
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from . import engine
from .models import AuditLog, Lead, Message, Order, ProposedAction

AUTOPILOT = "autopilot"


def _order_dict(o: Order):
    return {"number": o.number, "customer_email": o.customer_email, "phone": o.customer_phone,
            "total": str(o.total), "status": o.status}


@transaction.atomic
def process(message: Message) -> Message:
    a = engine.analyse(message.body, message.sender)
    order = Order.objects.filter(number=a.extracted.get("order", "")).first()
    plan = engine.decide(a, message.sender, _order_dict(order) if order else None,
                         Decimal(str(settings.PILOT_MAX_REFUND)))
    message.intent, message.confidence = a.intent, a.confidence
    message.extracted, message.flagged_injection, message.reasons = a.extracted, a.injection, plan.reasons
    AuditLog.objects.create(actor=AUTOPILOT, event="classified", message=message,
                            detail="%s (%.0f%%)%s" % (a.intent, a.confidence * 100, ", injection removed" if a.injection else ""))
    if plan.ignore:
        message.state = Message.IGNORED
        message.save()
        return message
    for spec in plan.actions:
        act = ProposedAction.objects.create(message=message, kind=spec["kind"], risk=spec["risk"], params=spec["params"])
        if plan.auto:
            _execute(act, AUTOPILOT, None)
    message.state = Message.AUTO if plan.auto else Message.APPROVAL
    message.save()
    return message


def _execute(act: ProposedAction, actor: str, user):
    msg = act.message
    if act.kind == ProposedAction.REPLY:
        msg.reply = act.params.get("text", "")
        msg.save(update_fields=["reply"])
        detail = "reply sent (simulated): %s" % msg.reply
    elif act.kind == ProposedAction.REFUND:
        order = Order.objects.select_for_update().get(number=act.params["order"])
        if order.status == Order.REFUNDED:
            raise ValueError("order %s is already refunded" % order.number)
        order.status = Order.REFUNDED
        order.save(update_fields=["status"])
        detail = "refund of %s on %s (simulated payment)" % (act.params["amount"], order.number)
    elif act.kind == ProposedAction.LEAD:
        Lead.objects.create(name=act.params.get("name", ""), email=act.params.get("email", ""),
                            need=act.params.get("need", ""), source=msg)
        detail = "lead saved for %s" % act.params.get("email", "")
    else:
        detail = "escalated: %s" % act.params.get("note", "")
    act.state, act.decided_by, act.decided_at = ProposedAction.EXECUTED, user, timezone.now()
    act.save()
    AuditLog.objects.create(actor=actor, event="executed " + act.kind, detail=detail, message=msg)


@transaction.atomic
def decide_message(message: Message, approve: bool, user) -> None:
    """Approve or reject every pending action of a message in one step."""
    message = Message.objects.select_for_update().get(pk=message.pk)
    pending = list(message.actions.filter(state=ProposedAction.PENDING))
    if message.state != Message.APPROVAL or not pending:
        raise ValueError("nothing is waiting for approval on this message")
    for act in pending:
        if approve:
            _execute(act, user.username, user)
        else:
            act.state, act.decided_by, act.decided_at = ProposedAction.REJECTED, user, timezone.now()
            act.save()
            AuditLog.objects.create(actor=user.username, event="rejected " + act.kind, message=message)
    message.state = Message.DONE
    message.save(update_fields=["state"])
