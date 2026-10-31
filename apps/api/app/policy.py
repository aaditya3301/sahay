"""Policy gate: the LLM proposes, code decides.

Owner writes rules in plain language → compiled ONCE into this DSL (Gemini, schema-constrained,
fields restricted to FACTS) → owner confirms the deterministic rendering → every action proposal
is evaluated here in pure Python against facts computed server-side.
"""

from typing import Literal

from pydantic import BaseModel, Field

from app import llm
from app.config import settings

ACTIONS: dict[str, str] = {
    "create_replacement": "Send a free replacement for items in an order",
    "issue_refund": "Refund money for an order (full or partial)",
    "cancel_order": "Cancel an order that has not shipped",
    "update_shipping_address": "Change the delivery address of an unshipped order",
    "issue_coupon": "Give the customer a single-use discount coupon",
    "confirm_cod": "Mark a cash-on-delivery order as confirmed by the customer",
    "reschedule_delivery": "Move the delivery to another date",
    "create_return": "Start a return / reverse pickup",
}

FACTS: dict[str, tuple[str, str]] = {
    # field: (type, description)
    "ticket.intent": ("text", "classified intent, e.g. damaged_item, wrong_item, cancel_order, refund_request"),
    "ticket.sensitive": ("bool", "ticket touches a sensitive category (legal threat, chargeback, safety...)"),
    "ticket.injection_suspected": ("bool", "message looks like a prompt-injection attempt"),
    "action.value_inr": ("number", "money value of the action in rupees, computed from the order"),
    "order.total_inr": ("number", "order total in rupees"),
    "order.payment_method": ("text", "cod | prepaid"),
    "order.status": ("text", "processing | shipped | in_transit | delivered | cancelled | rto"),
    "order.days_since_delivery": ("number", "days since the order was delivered (absent if not delivered)"),
    "order.is_shipped": ("bool", "order has left the warehouse"),
    "order.belongs_to_customer": ("bool", "the order belongs to the customer in this conversation"),
    "customer.verified_for_order": ("bool", "customer identity matches the order (phone/email)"),
    "customer.vip": ("bool", "customer is marked VIP"),
    "customer.fraud_score": ("number", "0..1, higher means more suspicious"),
    "customer.frustration": ("number", "0..100 frustration score"),
    "customer.auto_actions_30d": ("number", "automatic money actions for this customer in 30 days"),
    "customer.claims_90d": ("number", "damage/missing claims by this customer in 90 days"),
    "evidence.photo_verified": ("bool", "photo shows damage on the ordered product"),
    "evidence.reused_image": ("bool", "photo was seen before or is a catalog image"),
    "product.category": ("text", "product category of the affected item, e.g. perishable, jewellery"),
}

Decision = Literal["allow", "require_approval", "deny"]


class Condition(BaseModel):
    field: str = Field(description="one of the allowed fact fields")
    op: Literal["eq", "neq", "in", "not_in", "lt", "lte", "gt", "gte", "exists"]
    value: float | str | bool | list[str] | None = None


class Rule(BaseModel):
    id: str
    description: str = Field(description="the owner's sentence this rule comes from")
    action: str
    conditions: list[Condition] = []
    decision: Decision
    deny_hint: str | None = Field(None, description="what to offer instead when denied")


class Limits(BaseModel):
    max_auto_value_inr: float = 1000
    max_auto_actions_per_customer_30d: int = 3


class PolicyDoc(BaseModel):
    unmatched_decision: Decision = "require_approval"
    limits: Limits = Limits()
    rules: list[Rule] = []


class CompileResult(BaseModel):
    policy: PolicyDoc
    questions: list[str] = Field(default=[], description="ambiguities the owner should clarify")


class GateResult(BaseModel):
    decision: Decision
    reasons: list[str]
    matched_rules: list[str] = []
    invariant_hits: list[str] = []
    deny_hint: str | None = None


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------
def _match(c: Condition, facts: dict) -> bool:
    v = facts.get(c.field)
    if c.op == "exists":
        return v is not None
    if v is None:
        return False
    try:
        if c.op in ("eq", "neq"):
            if isinstance(v, bool) or isinstance(c.value, bool):
                same = _bool(v) == _bool(c.value)
            elif isinstance(v, (int, float)) and isinstance(c.value, (int, float)):
                same = float(v) == float(c.value)
            else:
                same = str(v).lower() == str(c.value).lower()
            return same if c.op == "eq" else not same
        if c.op == "in":
            return str(v).lower() in [str(x).lower() for x in (c.value or [])]
        if c.op == "not_in":
            return str(v).lower() not in [str(x).lower() for x in (c.value or [])]
        fv, cv = float(v), float(c.value)
        return {"lt": fv < cv, "lte": fv <= cv, "gt": fv > cv, "gte": fv >= cv}[c.op]
    except (TypeError, ValueError):
        return False


def _bool(v) -> bool:
    return v is True or str(v).lower() == "true"


def invariants(action: str, facts: dict, limits: Limits) -> list[tuple[Decision, str]]:
    """Platform rules tenants cannot override."""
    hits: list[tuple[Decision, str]] = []
    money = action in ("issue_refund", "create_replacement", "issue_coupon", "create_return")
    if "order.belongs_to_customer" in facts and not _bool(facts["order.belongs_to_customer"]):
        hits.append(("deny", "Order does not belong to this customer"))
    if facts.get("action.value_inr") is not None and facts.get("order.total_inr") is not None:
        if float(facts["action.value_inr"]) > float(facts["order.total_inr"]) + 0.5:
            hits.append(("deny", f"Requested ₹{facts['action.value_inr']:.0f} exceeds the order total ₹{facts['order.total_inr']:.0f}"))
    if "customer.verified_for_order" in facts and not _bool(facts["customer.verified_for_order"]):
        hits.append(("require_approval", "Customer identity not verified for this order"))
    if _bool(facts.get("ticket.injection_suspected")):
        hits.append(("require_approval", "Prompt-injection suspected: no automatic actions"))
    if _bool(facts.get("ticket.sensitive")):
        hits.append(("require_approval", "Sensitive ticket: a human must decide"))
    if money and float(facts.get("action.value_inr") or 0) > limits.max_auto_value_inr:
        hits.append(("require_approval", f"Above the automatic limit of ₹{limits.max_auto_value_inr:.0f}"))
    if money and float(facts.get("customer.auto_actions_30d") or 0) >= limits.max_auto_actions_per_customer_30d:
        hits.append(("require_approval", "Customer reached the automatic action limit for 30 days"))
    if _bool(facts.get("evidence.reused_image")):
        hits.append(("require_approval", "Photo was seen before or matches a catalog image"))
    if money and _bool(facts.get("evidence.document_suspicious")):
        hits.append(("require_approval", "A photo or document the customer sent failed the authenticity check"))
    return hits


def evaluate(action: str, facts: dict, policy: PolicyDoc) -> GateResult:
    inv = invariants(action, facts, policy.limits)
    denies = [r for d, r in inv if d == "deny"]
    if denies:
        return GateResult(decision="deny", reasons=denies, invariant_hits=denies)
    matching = [r for r in policy.rules if r.action == action and all(_match(c, facts) for c in r.conditions)]
    ids = [r.id for r in matching]
    deny_rules = [r for r in matching if r.decision == "deny"]
    if deny_rules:
        return GateResult(decision="deny", reasons=[r.description for r in deny_rules], matched_rules=ids,
                          deny_hint=deny_rules[0].deny_hint, invariant_hits=[r for _, r in inv])
    approvals = [r for d, r in inv if d == "require_approval"]
    if approvals:
        return GateResult(decision="require_approval", reasons=approvals, matched_rules=ids, invariant_hits=approvals)
    appr_rules = [r for r in matching if r.decision == "require_approval"]
    if appr_rules:
        return GateResult(decision="require_approval", reasons=[r.description for r in appr_rules], matched_rules=ids)
    allow = [r for r in matching if r.decision == "allow"]
    if allow:
        return GateResult(decision="allow", reasons=[r.description for r in allow], matched_rules=ids)
    return GateResult(decision=policy.unmatched_decision, reasons=["No rule covers this case (fail-closed default)"])


# ---------------------------------------------------------------------------
# Deterministic rendering (what the owner reads is what gets enforced)
# ---------------------------------------------------------------------------
_OPS = {"eq": "is", "neq": "is not", "in": "is one of", "not_in": "is not one of", "lt": "<", "lte": "≤",
        "gt": ">", "gte": "≥", "exists": "is known"}
_DEC = {"allow": "✅ Automatically", "require_approval": "🟡 Needs approval", "deny": "⛔ Never"}


def render_rule(r: Rule) -> dict:
    conds = []
    for c in r.conditions:
        label = FACTS.get(c.field, ("", c.field))[1].split(",")[0]
        val = ", ".join(c.value) if isinstance(c.value, list) else ("" if c.value is None else str(c.value))
        if c.field.endswith("_inr") and isinstance(c.value, (int, float)):
            val = f"₹{c.value:,.0f}"
        conds.append(f"{c.field} {_OPS[c.op]} {val}".strip())
    return {
        "id": r.id,
        "headline": f"{_DEC[r.decision]}: {ACTIONS.get(r.action, r.action).lower()}",
        "decision": r.decision,
        "action": r.action,
        "when": conds or ["always"],
        "source": r.description,
        "deny_hint": r.deny_hint,
    }


def validate(doc: PolicyDoc) -> list[str]:
    problems = []
    for r in doc.rules:
        if r.action not in ACTIONS:
            problems.append(f"Rule {r.id}: unknown action '{r.action}'")
        for c in r.conditions:
            if c.field not in FACTS:
                problems.append(f"Rule {r.id}: unknown field '{c.field}'")
    return problems


async def compile_policy(source_text: str, tenant_id: str, business: str = "") -> CompileResult:
    facts = "\n".join(f"- {k} ({t}): {d}" for k, (t, d) in FACTS.items())
    actions = "\n".join(f"- {k}: {d}" for k, d in ACTIONS.items())
    system = (
        "You compile a shop owner's plain-language support rules into a strict JSON policy. "
        "Use ONLY these fact fields:\n" + facts + "\n\nand ONLY these actions:\n" + actions +
        "\n\nRules: one rule per distinct (action, conditions, decision). Use snake_case ids. "
        "decision is allow (AI may do it alone), require_approval (a human must approve) or deny (never). "
        "Put the owner's exact sentence in description. Convert money to rupees. 'within N days of delivery' → "
        "order.days_since_delivery lte N. For intents use ticket.intent with op 'in'. "
        "Set limits.max_auto_value_inr to the highest automatic money amount the owner allows (default 1000). "
        "Anything the owner did not mention stays out (the default is require_approval). "
        "If something is ambiguous, still compile your best reading and add a short question."
    )
    res = await llm.structured(
        CompileResult, model=settings.model_reasoning, system=system,
        contents=f"Business: {business}\n\nOwner's rules:\n{source_text}", temperature=0,
        purpose="policy_compile", tenant_id=tenant_id,
    )
    res.policy.rules = [r for r in res.policy.rules if r.action in ACTIONS]
    for r in res.policy.rules:
        r.conditions = [c for c in r.conditions if c.field in FACTS]
    return res
