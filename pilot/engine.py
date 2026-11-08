"""The decision engine: pure functions, no database, easy to test.

Rules-based on purpose: it is fast, free, and every decision can be
explained line by line. A language model can be plugged in later as a
better classifier (see classify()), but the safety policy in decide()
stays in plain code, where a prompt injection cannot change it.
"""
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Dict, List, Optional

INTENTS = ["order_status", "refund", "lead", "complaint", "spam", "other"]

_KEYWORDS = {
    "order_status": ["where is my order", "where is order", "where is my", "order status", "track", "tracking", "not arrived", "delivery", "shipped", "when will"],
    "refund": ["refund", "money back", "return", "cancel my order", "chargeback"],
    "lead": ["quote", "pricing", "price for", "proposal", "interested in", "partnership", "bulk order", "demo"],
    "complaint": ["angry", "terrible", "worst", "complain", "broken", "damaged", "unacceptable", "disappointed"],
    "spam": ["crypto giveaway", "click here", "you have won", "seo services", "lottery", "100% free"],
}
_INJECTION = re.compile(
    r"(ignore (all |the |your )?(previous |above )?(instructions|rules)|system prompt|you are now|"
    r"assistant\s*:|developer mode|approve (this|it) automatically|no (approval|confirmation) needed)", re.I)
_ORDER = re.compile(r"\b(?:order\s*(?:no\.?|number|#)?\s*)?#?(ORD-?\d{3,6})\b", re.I)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_AMOUNT = re.compile(r"(?:₹|rs\.?|inr|\$|usd)\s*([\d,]+(?:\.\d{1,2})?)", re.I)
_NAME = re.compile(r"\b(?i:i am|i'm|this is|my name is)\s+([A-Z][a-z]+(?:\s[A-Z][a-z]+)?)")


@dataclass
class Analysis:
    intent: str
    confidence: float
    extracted: Dict[str, str]
    injection: bool
    clean_text: str


@dataclass
class Plan:
    actions: List[Dict] = field(default_factory=list)   # {"kind", "risk", "params"}
    reasons: List[str] = field(default_factory=list)
    auto: bool = False     # True when everything can run without a person
    ignore: bool = False


def sanitize(text: str):
    """Split out lines that try to give the system instructions. They are kept
    for the human reviewer but never used to decide anything."""
    kept, flagged = [], False
    for line in (text or "").splitlines():
        if _INJECTION.search(line):
            flagged = True
            continue
        kept.append(line)
    return "\n".join(kept).strip(), flagged


def classify(text: str):
    low = text.lower()
    scores = {k: sum(1 for kw in kws if kw in low) for k, kws in _KEYWORDS.items()}
    best = max(scores, key=lambda k: (scores[k], -INTENTS.index(k)))
    total = sum(scores.values())
    if scores[best] == 0:
        return "other", 0.0
    return best, round(scores[best] / total, 2)


def extract(text: str, sender: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    m = _ORDER.search(text)
    if m:
        out["order"] = m.group(1).upper().replace("ORD", "ORD-").replace("ORD--", "ORD-")
    emails = [e for e in _EMAIL.findall(text)]
    if emails:
        out["email"] = emails[0].lower()
    elif "@" in sender:
        out["email"] = sender.lower()
    a = _AMOUNT.search(text)
    if a:
        out["amount"] = a.group(1).replace(",", "")
    n = _NAME.search(text)
    if n:
        out["name"] = n.group(1)
    return out


def analyse(text: str, sender: str) -> Analysis:
    clean, injection = sanitize(text)
    intent, conf = classify(clean)
    return Analysis(intent, conf, extract(clean, sender), injection, clean)


def decide(a: Analysis, sender: str, order: Optional[Dict], max_refund: Decimal) -> Plan:
    """The safety policy. `order` is {"number", "customer_email", "total", "status"} or None."""
    p = Plan()
    if a.injection:
        p.reasons.append("Message contains instruction-like text aimed at the system; it was removed and a person must review.")
    if a.intent == "spam":
        p.ignore = True
        p.reasons.append("Classified as spam; no reply sent.")
        return p

    owner_ok = bool(order) and sender.lower() in (order["customer_email"].lower(), order.get("phone", "").lower())

    if a.intent == "order_status":
        if not order:
            p.actions.append({"kind": "reply", "risk": "low", "params": {"text": (
                "Thanks for reaching out. Please share your order number (for example ORD-1042) so we can check it.")}})
            p.reasons.append("No known order number in the message; asked the customer for it.")
        elif owner_ok:
            p.actions.append({"kind": "reply", "risk": "low", "params": {"text": (
                "Your order %s is currently: %s." % (order["number"], order["status"]))}})
            p.reasons.append("Sender matches the order's email, so the status can be shared automatically.")
        else:
            p.actions.append({"kind": "reply", "risk": "high", "params": {"text": (
                "For your security, please write to us from the email address used for order %s." % order["number"])}})
            p.reasons.append("Sender does not match the order's email: order details withheld, a person checks first.")

    elif a.intent == "refund":
        if not order:
            p.actions.append({"kind": "reply", "risk": "low", "params": {"text": (
                "We can help with a refund. Please share your order number.")}})
            p.reasons.append("Refund asked without a known order number; asked for it.")
        else:
            try:
                asked = Decimal(a.extracted.get("amount", order["total"]))
            except InvalidOperation:
                asked = Decimal(order["total"])
            total = Decimal(order["total"])
            if order["status"] == "refunded":
                p.actions.append({"kind": "reply", "risk": "low", "params": {"text": "Order %s has already been refunded." % order["number"]}})
                p.reasons.append("Order is already refunded; no second refund proposed.")
            elif not owner_ok:
                p.actions.append({"kind": "escalate", "risk": "high", "params": {"note": "Refund asked by someone who is not the order's customer."}})
                p.reasons.append("Sender is not the order's customer; refund not proposed.")
            elif asked > total or asked > max_refund:
                p.actions.append({"kind": "escalate", "risk": "high", "params": {"note": "Refund of %s exceeds the order total or the %s limit." % (asked, max_refund)}})
                p.reasons.append("Requested amount is above the order total or the auto limit.")
            else:
                p.actions.append({"kind": "refund", "risk": "high", "params": {"order": order["number"], "amount": str(asked)}})
                p.actions.append({"kind": "reply", "risk": "high", "params": {"text": (
                    "Your refund of %s for order %s has been approved and is on its way." % (asked, order["number"]))}})
                p.reasons.append("Refunds always need a person's approval before money moves.")

    elif a.intent == "lead":
        p.actions.append({"kind": "lead", "risk": "low", "params": {"name": a.extracted.get("name", ""),
                                                                    "email": a.extracted.get("email", sender),
                                                                    "need": a.clean_text[:500]}})
        p.actions.append({"kind": "reply", "risk": "low", "params": {"text": (
            "Thanks for your interest! A member of our team will send you details within one business day.")}})
        p.reasons.append("New business enquiry: saved as a lead and acknowledged.")

    elif a.intent == "complaint":
        p.actions.append({"kind": "escalate", "risk": "high", "params": {"note": "Unhappy customer; needs a personal reply."}})
        p.actions.append({"kind": "reply", "risk": "high", "params": {"text": (
            "We're sorry about this experience. A team member is looking into it and will reply personally today.")}})
        p.reasons.append("Complaints are answered by a person, with a drafted reply ready.")

    else:
        p.actions.append({"kind": "escalate", "risk": "high", "params": {"note": "Could not understand the request."}})
        p.reasons.append("Intent unclear; routed to a person.")

    if a.injection:
        for act in p.actions:
            act["risk"] = "high"
    p.auto = bool(p.actions) and all(x["risk"] == "low" for x in p.actions)
    return p
