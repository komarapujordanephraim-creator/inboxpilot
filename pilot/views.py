import hashlib
import hmac
import json

from django import forms
from django.conf import settings
from django.contrib import messages as flash
from django.contrib.admin.views.decorators import staff_member_required
from django.db.models import Count
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from . import services
from .models import AuditLog, Lead, Message, Order

staff = staff_member_required(login_url="login")


class SimulateForm(forms.Form):
    channel = forms.ChoiceField(choices=Message.CHANNELS)
    sender = forms.CharField(max_length=200, help_text="Email address or phone number")
    body = forms.CharField(widget=forms.Textarea(attrs={"rows": 5}), max_length=4000)


@staff
def dashboard(request):
    qs = Message.objects.all()
    total = qs.count()
    auto = qs.filter(state=Message.AUTO).count()
    by_intent = (qs.exclude(intent="").values("intent").annotate(n=Count("id")).order_by("-n"))
    return render(request, "pilot/dashboard.html", {
        "total": total, "auto": auto, "pending": qs.filter(state=Message.APPROVAL).count(),
        "flagged": qs.filter(flagged_injection=True).count(),
        "auto_pct": round(auto * 100 / total) if total else 0,
        "by_intent": by_intent, "max_intent": max([r["n"] for r in by_intent] or [1]),
        "recent": AuditLog.objects.select_related("message")[:12],
        "leads": Lead.objects.count(),
    })


@staff
def inbox(request):
    state = request.GET.get("state", "")
    qs = Message.objects.all()
    if state in dict(Message.STATES):
        qs = qs.filter(state=state)
    return render(request, "pilot/inbox.html", {"items": qs[:200], "state": state, "states": Message.STATES})


@staff
def message_detail(request, pk):
    m = get_object_or_404(Message, pk=pk)
    return render(request, "pilot/message.html", {"m": m, "actions": m.actions.all(),
                                                  "log": m.auditlog_set.all()})


@staff
@require_POST
def decide(request, pk):
    m = get_object_or_404(Message, pk=pk)
    approve = request.POST.get("decision") == "approve"
    try:
        services.decide_message(m, approve, request.user)
        flash.success(request, "Approved and executed." if approve else "Rejected. Nothing was sent.")
    except (ValueError, Order.DoesNotExist) as e:
        flash.error(request, "Could not complete: %s" % e)
    return redirect("message", pk=pk)


@staff
def simulate(request):
    form = SimulateForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        m = services.process(Message.objects.create(**form.cleaned_data))
        flash.info(request, "Processed as '%s' -> %s" % (m.intent, m.get_state_display()))
        return redirect("message", pk=m.pk)
    return render(request, "pilot/simulate.html", {"form": form})


@csrf_exempt
@require_POST
def inbound(request):
    """Webhook for WhatsApp / email bridges. The caller signs the raw body:
    header X-Pilot-Signature: sha256=<hex HMAC-SHA256 with PILOT_WEBHOOK_SECRET>."""
    sig = request.headers.get("X-Pilot-Signature", "")
    expected = "sha256=" + hmac.new(settings.PILOT_WEBHOOK_SECRET.encode(), request.body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return JsonResponse({"error": "bad signature"}, status=401)
    try:
        data = json.loads(request.body)
        form = SimulateForm(data)
    except (ValueError, TypeError):
        return JsonResponse({"error": "body must be JSON"}, status=400)
    if not form.is_valid():
        return JsonResponse({"error": form.errors}, status=400)
    m = services.process(Message.objects.create(**form.cleaned_data))
    return JsonResponse({"id": m.pk, "intent": m.intent, "state": m.state,
                         "reply": m.reply if m.state == Message.AUTO else None, "reasons": m.reasons})
