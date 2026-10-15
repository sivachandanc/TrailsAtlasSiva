"""Turn the agent's submitted rules into checked filters. No LLM here: every code is validated
against UMLS, every drug is resolved and expanded to NDCs through RxNorm, every quote is checked
against the trial's text, and the SQL is written by code, not by the model."""

import asyncio
import re
from datetime import date, timedelta

from langchain_core.messages import AIMessage, ToolMessage

from webapp.backend.agents.member_schema import DIAGNOSES, MEMBERS, NDC_LOOKUP, PRESCRIPTIONS
from webapp.backend.models import (
    AgentCriterion, CriteriaFilter, CriterionCode, CriterionDrugOut, CriterionOut, TimeWindow, ToolCallLog,
)
from webapp.backend.services.trial_summary import unescape

MAX_RESULT_CHARS = 600


def normalize(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", unescape(text).lower()).split())


def quote_found(quote: str, source: str) -> bool:
    """The quote appears in the criteria text (ignoring case, punctuation and line breaks), or
    almost every word of it does (the model sometimes trims a bullet or fixes a typo)."""
    q, s = normalize(quote), normalize(source)
    if not q:
        return False
    if q in s:
        return True
    words = q.split()
    text_words = set(s.split())
    return len(words) >= 4 and sum(w in text_words for w in words) / len(words) >= 0.9


def tool_logs(messages: list) -> list[ToolCallLog]:
    results = {m.tool_call_id: m for m in messages if isinstance(m, ToolMessage)}
    logs = []
    for m in messages:
        if not isinstance(m, AIMessage):
            continue
        for call in m.tool_calls:
            if call["name"] == "submit_criteria":
                continue
            r = results.get(call["id"])
            text = str(r.content) if r else "(no result)"
            logs.append(ToolCallLog(id=call["id"], name=call["name"], args=call["args"],
                                    result=text if len(text) <= MAX_RESULT_CHARS else text[:MAX_RESULT_CHARS] + "…",
                                    is_error=bool(r and getattr(r, "status", "") == "error")))
    return logs


def progress_notes(messages: list) -> list[str]:
    """The agent's visible progress notes and any text between tool calls, in order."""
    notes = []
    for m in messages:
        if not isinstance(m, AIMessage):
            continue
        blocks = m.content if isinstance(m.content, list) else [{"type": "text", "text": m.content}]
        for b in blocks:
            if not isinstance(b, dict):
                continue
            text = (b.get("thinking") if b.get("type") == "thinking" else b.get("text") if b.get("type") == "text" else None) or ""
            if text.strip():
                notes.append(text.strip())
    return notes


def usage_totals(messages: list) -> dict:
    totals = {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0, "llm_calls": 0}
    for m in messages:
        if isinstance(m, AIMessage) and m.usage_metadata:
            totals["llm_calls"] += 1
            totals["input_tokens"] += m.usage_metadata.get("input_tokens", 0)
            totals["output_tokens"] += m.usage_metadata.get("output_tokens", 0)
            totals["cache_read_input_tokens"] += (m.usage_metadata.get("input_token_details") or {}).get("cache_read", 0) or 0
    return totals


def range_text(w: TimeWindow, ref: date, medication: bool) -> str:
    """ "In the last 90 days (on or after 2026-07-16)", "Between 365 and 730 days ago (...)" and so on."""
    lo, hi = w.min_days_ago, w.max_days_ago
    day = lambda n: (ref - timedelta(days=n)).isoformat()
    what = "Last fill" if medication else "Dated"
    if lo is not None and hi is not None:
        return f"{what} between {lo} and {hi} days ago ({day(hi)} to {day(lo)})"
    if hi is not None:
        return f"{what} in the last {hi} days (on or after {day(hi)})"
    return f"{what} at least {lo} days ago (on or before {day(lo)})"


def window_text(c: AgentCriterion, ref: date) -> str:
    w = c.window
    medication = c.type == "medication"
    if medication and w.min_days_ago is not None:
        return ("Needs a start date or how long the drug has been taken. Only each drug's last fill date is "
                "stored, so this can't be checked.")
    if w.type == "event" and w.bounded:
        return (f"Tied to “{w.event or 'an event'}”, approximated as: {range_text(w, ref, medication).lower()}. "
                f"{w.approximation or ''} A reviewer confirms the timing.").replace("  ", " ")
    if w.type == "event":
        return f"Tied to “{w.event or 'an event'}”, which claims can't place, so a reviewer checks it."
    if w.type == "days_ago" and w.bounded:
        return range_text(w, ref, medication)
    if w.type == "ever":
        return "Any time in the member's history"
    return "No time window stated, so any time in the member's history"


def date_condition(column: str, w: TimeWindow, ref: date) -> str:
    """Every window is a range of dates; either end may be open."""
    parts = []
    if w.max_days_ago is not None:
        parts.append(f"{column} >= DATE '{(ref - timedelta(days=w.max_days_ago)).isoformat()}'")
    if w.min_days_ago is not None:
        parts.append(f"{column} <= DATE '{(ref - timedelta(days=w.min_days_ago)).isoformat()}'")
    return "".join(f"\n    AND {p}" for p in parts)


def diagnosis_sql(c: AgentCriterion, codes: list[str], ref: date) -> str:
    likes = " OR ".join(f"icd_code LIKE '{code.replace('.', '').upper()}%'" for code in codes)
    op = "IN" if c.requirement == "must_have" else "NOT IN"
    # Every diagnosis date is kept, so any window is "a matching code dated inside the range"
    # ("first diagnosed at least N days ago" = some matching code on or before the cutoff)
    return (f"member_id {op} (\n  SELECT member_id FROM {DIAGNOSES}\n  WHERE code_system = 'ICD10CM'\n"
            f"    AND ({likes}){date_condition('diagnosis_date', c.window, ref)}\n)")


def medication_sql(c: AgentCriterion, drugs: list[CriterionDrugOut], ref: date) -> str:
    parts = []
    rxcuis = [d.rxcui for d in drugs if d.rxcui]
    if rxcuis:
        parts.append("n.ingredient_rxcui IN (" + ", ".join(f"'{r}'" for r in rxcuis) + ")")
    parts += [f"n.atc_class LIKE '{d.atc_class}%'" for d in drugs if not d.rxcui and d.atc_class]
    op = "IN" if c.requirement == "must_have" else "NOT IN"
    # One row per member and NDC with the most recent fill, so "in the last N days" checks that fill
    return (f"member_id {op} (\n  SELECT p.member_id FROM {PRESCRIPTIONS} p\n"
            f"  JOIN {NDC_LOOKUP} n ON n.ndc = p.ndc\n  WHERE ({' OR '.join(parts)})"
            f"{date_condition('p.last_fill_date', c.window, ref)}\n)")


async def resolve_drug(d, rxnorm) -> CriterionDrugOut:
    """An ingredient (rxcui) or a whole ATC class, expanded to every NDC it covers."""
    if d.rxcui:
        props = await rxnorm.properties(d.rxcui)
        if not props:
            return CriterionDrugOut(name=d.name, rxcui=d.rxcui, atc_class=d.atc_class, atc_name=None,
                                    ingredients=[], ndc_count=0, ndc_samples=[], valid=False)
        ingredients = await rxnorm.ingredients_of(d.rxcui)
        ndc_lists = await asyncio.gather(*(rxnorm.ndcs_for_ingredient(i["rxcui"]) for i in ingredients))
        ndcs = sorted({n for lst in ndc_lists for n in lst})
        return CriterionDrugOut(name=props["name"], rxcui=d.rxcui, atc_class=None, atc_name=None,
                                ingredients=[i["name"] for i in ingredients], ndc_count=len(ndcs),
                                ndc_samples=ndcs[:5], valid=bool(ingredients))
    classes = await rxnorm.drug_class(d.atc_class) if d.atc_class else []
    exact = next((c for c in classes if c["atc"] == (d.atc_class or "").upper()), None)
    if not exact:
        return CriterionDrugOut(name=d.name, rxcui=None, atc_class=d.atc_class, atc_name=None,
                                ingredients=[], ndc_count=0, ndc_samples=[], valid=False)
    ndc_lists = await asyncio.gather(*(rxnorm.ndcs_for_ingredient(m["rxcui"]) for m in exact["members"]))
    ndcs = sorted({n for lst in ndc_lists for n in lst})
    return CriterionDrugOut(name=d.name, rxcui=None, atc_class=exact["atc"], atc_name=exact["name"],
                            ingredients=[m["name"] for m in exact["members"]], ndc_count=len(ndcs),
                            ndc_samples=ndcs[:5], valid=True)


async def build_criteria_filter(*, submitted: dict, context: dict, messages: list, umls, rxnorm,
                                model: str, reference_date: date | None = None) -> CriteriaFilter:
    ref = reference_date or date.today()
    rules = [AgentCriterion.model_validate(c) for c in submitted.get("criteria", [])]
    logs = tool_logs(messages)

    # Validate every code once, and resolve every drug once
    codes = sorted({code.strip().upper() for r in rules for code in r.icd10cm_codes})
    found = dict(zip(codes, await asyncio.gather(*(umls.icd10cm_code(c) for c in codes))))
    drug_keys = {(d.rxcui, (d.atc_class or "").upper() or None): d for r in rules for d in r.drugs}
    resolved = dict(zip(drug_keys, await asyncio.gather(*(resolve_drug(d, rxnorm) for d in drug_keys.values()))))

    out = []
    for i, r in enumerate(rules, start=1):
        problems = []
        ok_quote = quote_found(r.source_text, context["eligibility_text"])
        if not ok_quote:
            problems.append("This quote doesn't appear in the trial's criteria text, so it may not be a real rule.")
        cc = [CriterionCode(code=c, name=(found.get(c.strip().upper()) or {}).get("name"),
                            valid=bool(found.get(c.strip().upper()))) for c in r.icd10cm_codes]
        for c in cc:
            if not c.valid:
                problems.append(f"{c.code} isn't a real ICD-10-CM code, so it was left out of the filter.")
        drugs = [resolved[(d.rxcui, (d.atc_class or "").upper() or None)] for d in r.drugs]
        for d in drugs:
            if not d.valid:
                problems.append(f"The drug “{d.name}” couldn't be confirmed in RxNorm, so it was left out of the filter.")
        valid_codes = [c.code for c in cc if c.valid]
        valid_drugs = [d for d in drugs if d.valid]
        w = r.window
        backwards = w.min_days_ago is not None and w.max_days_ago is not None and w.min_days_ago > w.max_days_ago
        if backwards:
            problems.append(f"The window runs backwards ({w.min_days_ago} to {w.max_days_ago} days ago).")
        if r.codable and not r.covered_by_step and not (valid_codes or valid_drugs):
            problems.append("Marked as answerable from claims, but it has no confirmed codes or drugs.")

        sql = None
        if valid_codes:
            sql = diagnosis_sql(r, valid_codes, ref)
        elif valid_drugs:
            sql = medication_sql(r, valid_drugs, ref)

        reason = r.reason_not_codable
        if r.covered_by_step:
            group = "covered"
        elif not ok_quote or (r.codable and not sql) or backwards:
            group = "flagged"
        elif not r.codable:
            group = "needs_review"
        elif r.window.type == "event" and not r.window.bounded:
            group, reason = "needs_review", (f"The codes are known, but the time window depends on “{r.window.event}”, "
                                             "which claims can't place reliably.")
        elif r.type == "medication" and r.window.min_days_ago is not None:
            group, reason = "needs_review", ("Only each drug's most recent fill date is stored, so how long a member "
                                             "has taken it, or when they started, can't be checked.")
        else:
            group = "codable"

        keys = [c.code for c in cc] + [d.rxcui for d in r.drugs if d.rxcui] + [d.atc_class for d in r.drugs if d.atc_class]
        backing = [log.id for log in logs if any(k and k in log.result for k in keys)]
        out.append(CriterionOut(
            id=f"C{i}", group=group, source_text=r.source_text, quote_found=ok_quote, section=r.section,
            requirement=r.requirement, type=r.type, codes=cc, drugs=drugs, window=r.window,
            window_text=window_text(r, ref), approximate=r.window.type == "event" and r.window.bounded,
            covered_by_step=r.covered_by_step, reason_not_codable=reason,
            rationale=r.rationale, problems=problems, tool_call_ids=backing, sql=sql,
        ))

    applied = [c.sql for c in out if c.group == "codable" and c.sql]
    sql = (f"SELECT member_id\nFROM {MEMBERS}\nWHERE " + "\n  AND ".join(applied)) if applied else \
        f"SELECT member_id\nFROM {MEMBERS}  -- no rule here can be checked from claims: everyone passes this step"
    definition = {
        "step": "criteria",
        "trial_id": context["trial_id"],
        "reference_date": ref.isoformat(),
        "model": model,
        "rules": [{"id": c.id, "group": c.group, "requirement": c.requirement, "type": c.type,
                   "icd10cm": [x.code for x in c.codes if x.valid],
                   "drugs": [{"rxcui": d.rxcui, "atc_class": d.atc_class} for d in c.drugs if d.valid],
                   "window": c.window.model_dump()} for c in out],
    }
    return CriteriaFilter(
        trial_id=context["trial_id"], model=model, reference_date=ref, criteria=out, tool_calls=logs,
        notes=progress_notes(messages), agent_summary=submitted.get("summary"), sql=sql,
        definition=definition, usage=usage_totals(messages),
    )
