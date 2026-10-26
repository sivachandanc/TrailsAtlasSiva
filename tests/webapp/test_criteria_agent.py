from datetime import date

import anthropic

import pytest
from langchain_core.messages import AIMessage

from tests.webapp.fakes_agent import CONTEXT, FakeIcd, FakeRx, ScriptedChatModel, call, scripted_turns
from webapp.backend.agents.criteria_graph import build_criteria_graph
from webapp.backend.services.criteria_filter import quote_found

pytestmark = pytest.mark.anyio
REF = date(2026, 10, 14)


async def run(turns):
    llm = ScriptedChatModel(turns=turns)
    graph = build_criteria_graph(llm, FakeIcd(), FakeRx(), model_name="fake", reference_date=REF)
    state = await graph.ainvoke({"context": CONTEXT}, {"recursion_limit": 100})
    return state, llm


async def test_full_run_groups_validates_and_builds_sql():
    state, llm = await run(scripted_turns())
    r = state["result"]
    by_text = {c.source_text: c for c in r.criteria}

    assert by_text["Adults 18 years or older"].group == "covered"
    dx = by_text["Pre-existing type 2 diabetes in pregnancy"]
    assert dx.group == "codable" and [(c.code, c.valid) for c in dx.codes] == [("O24.11", True), ("O24.999", False)]
    assert "O24.999 isn't a real ICD-10-CM code" in dx.problems[0]
    assert "UPPER(REPLACE(primary_diagnosis_cd, '.', '')) LIKE 'O2411%'" in dx.sql and "O24999" not in dx.sql
    assert "member_id IN" in dx.sql and "inferred_member_id IS NOT NULL" in dx.sql
    assert dx.tool_call_ids == ["t1", "t3"]  # the search that returned O24.11 and the check that confirmed it

    lab = by_text["HbA1c between 7% and 10%"]
    assert lab.group == "needs_review" and lab.reason_not_codable

    med = by_text["Use of an SGLT2 inhibitor in the past 3 months"]
    assert med.group == "codable" and med.drugs[0].ndc_count == 3 and med.drugs[0].ingredients == ["empagliflozin", "dapagliflozin"]
    assert med.drugs[0].ndcs == ["00310620530", "00597015207", "00597015230"]
    assert med.drugs[0].names == ["empagliflozin", "dapagliflozin", "Jardiance", "Synjardy", "Farxiga"]
    assert "member_id NOT IN" in med.sql and "inferred_member_id IS NOT NULL" in med.sql
    # Matched by NDC (leading zeros restored on the numeric product_id) or by whole-word drug name
    assert "LPAD(CAST(product_id AS STRING), 11, '0') IN (\n        '00310620530', '00597015207', '00597015230')" in med.sql
    assert "UPPER(brand_nme) RLIKE '\\\\b(EMPAGLIFLOZIN|DAPAGLIFLOZIN|JARDIANCE|SYNJARDY|FARXIGA)\\\\b'" in med.sql
    assert "serviced_dte >= DATE '2026-07-16'" in med.sql  # a fill within 90 days of the reference date

    fake = by_text["Takes made-up drug daily"]
    assert fake.group == "flagged" and not fake.quote_found and any("couldn't be confirmed" in p for p in fake.problems)

    assert "member_id IN" in r.sql and "member_id NOT IN" in r.sql and "HbA1c" not in r.sql
    assert [t.name for t in r.tool_calls] == ["search_icd10cm", "drug_class", "icd10cm_code"]
    assert r.notes == ["Looking up the diabetes codes and the drug class."]
    assert r.usage["input_tokens"] == 3700 and r.usage["llm_calls"] == 3
    assert r.agent_summary.startswith("Pregnant women")
    # The model saw the trial text and the age/sex step, never member data or condition-derived ICD prefixes
    first_user = llm.seen[0][1].content
    assert "Pre-existing type 2 diabetes in pregnancy" in first_user and "Step 1, age and sex: age 18 years and older" in first_user
    assert "diagnoses" not in first_user.lower()


async def test_text_answer_gets_one_reminder_then_error():
    state, _ = await run([AIMessage(content="Here are the rules..."), AIMessage(content="As I said...")])
    assert state["error"].startswith("The model answered in text twice") and "result" not in state


async def test_text_then_submit_after_reminder():
    turns = scripted_turns()
    state, llm = await run([AIMessage(content="Done reading.")] + turns)
    assert state["result"].criteria and "Please finish by calling submit_criteria" in llm.seen[1][-1].content


async def test_invalid_submission_is_sent_back_to_the_model():
    bad = AIMessage(content="", tool_calls=[call("submit_criteria", {"criteria": [{"source_text": "x"}]}, "bad")])
    state, llm = await run([bad] + scripted_turns())
    # The validation error went back as a tool result and the model tried again
    assert any(getattr(m, "tool_call_id", None) == "bad" and m.status == "error" for m in llm.seen[1])
    assert state["result"].criteria


async def test_refusal_stops_with_a_message():
    refused = AIMessage(content="", response_metadata={"stop_reason": "refusal"})
    state, _ = await run([refused])
    assert state["error"] == "The model declined to review these criteria."


def test_quote_matching():
    text = "Inclusion Criteria:\n\n* Male or non-pregnant female \\> / = 18 years of age"
    assert quote_found("male or non-pregnant female > / = 18 years of age", text)
    assert quote_found("Male or non pregnant female 18 years of age at enrollment", text) is False
    assert not quote_found("", text)


# ---------- member schema, windows and the prompt ----------

from webapp.backend.agents.member_schema import MX_CLAIMS, RX_CLAIMS  # noqa: E402
from webapp.backend.agents.prompts import SYSTEM_PROMPT, user_message  # noqa: E402
from webapp.backend.services.criteria_filter import build_criteria_filter  # noqa: E402

ELIG = "Inclusion Criteria:\n* Type 2 diabetes diagnosed at least 6 months ago\n* On metformin for at least 3 months"


async def finalize(criteria):
    ctx = {**CONTEXT, "eligibility_text": ELIG}
    return await build_criteria_filter(submitted={"criteria": criteria}, context=ctx, messages=[],
                                       umls=FakeIcd(), rxnorm=FakeRx(), model="fake", reference_date=REF)


def rule(text, window, **kw):
    return {"source_text": text, "section": "inclusion", "requirement": "must_have", "type": "diagnosis",
            "codable": True, "icd10cm_codes": ["E11"], "window": window, "rationale": "x", **kw}


async def test_at_least_n_days_ago_is_a_code_on_or_before_the_cutoff():
    r = await finalize([rule("Type 2 diabetes diagnosed at least 6 months ago",
                             {"type": "days_ago", "min_days_ago": 180})])
    c = r.criteria[0]
    assert c.group == "codable" and "AND first_serviced_dt <= DATE '2026-04-17'" in c.sql
    assert f"FROM {MX_CLAIMS}" in c.sql and c.window_text.startswith("Dated at least 180 days ago")


async def test_a_range_window_bounds_both_ends():
    r = await finalize([rule("Type 2 diabetes", {"type": "days_ago", "min_days_ago": 365, "max_days_ago": 730})])
    c = r.criteria[0]
    assert "first_serviced_dt >= DATE '2024-10-14'" in c.sql and "first_serviced_dt <= DATE '2025-10-14'" in c.sql
    assert c.window_text == "Dated between 365 and 730 days ago (2024-10-14 to 2025-10-14)" and not c.approximate


async def test_an_approximated_event_is_applied_and_flagged_for_review():
    r = await finalize([rule("Type 2 diabetes", {"type": "event", "event": "this pregnancy", "max_days_ago": 365,
                                                 "approximation": "This pregnancy = last 365 days."})])
    c = r.criteria[0]
    assert c.group == "codable" and c.approximate and "first_serviced_dt >= DATE '2025-10-14'" in r.sql
    assert "this pregnancy" in c.window_text and "reviewer confirms" in c.window_text


async def test_an_open_ended_event_goes_to_review():
    r = await finalize([rule("Type 2 diabetes", {"type": "event", "event": "since surgery"})])
    assert r.criteria[0].group == "needs_review" and "WHERE" not in r.sql


async def test_a_backwards_window_is_flagged():
    r = await finalize([rule("Type 2 diabetes", {"type": "days_ago", "min_days_ago": 730, "max_days_ago": 365})])
    assert r.criteria[0].group == "flagged" and "backwards" in r.criteria[0].problems[0]


async def test_a_medication_window_can_reach_back_because_every_fill_is_kept():
    r = await finalize([{"source_text": "On metformin for at least 3 months", "section": "inclusion",
                         "requirement": "must_have", "type": "medication", "codable": True,
                         "drugs": [{"name": "empagliflozin", "rxcui": "1545653"}],
                         "window": {"type": "days_ago", "min_days_ago": 90}, "rationale": "x"}])
    c = r.criteria[0]
    assert c.group == "codable" and f"FROM {RX_CLAIMS}" in c.sql and "AND serviced_dte <= DATE '2026-07-16'" in c.sql
    assert c.window_text.startswith("Filled at least 90 days ago")


def test_prompt_describes_the_member_tables_and_background():
    assert f"{RX_CLAIMS}: one row per pharmacy claim (every fill kept)" in SYSTEM_PROMPT
    assert "brand_nme" in SYSTEM_PROMPT and "primary_diagnosis_cd" in SYSTEM_PROMPT and "min_days_ago" in SYSTEM_PROMPT
    assert "only the last fill" not in SYSTEM_PROMPT and "Secondary diagnoses" in SYSTEM_PROMPT
    assert "This pregnancy" in SYSTEM_PROMPT and "max_days_ago 365" in SYSTEM_PROMPT
    msg = user_message({**CONTEXT, "official_title": "A Polypill Strategy", "brief_summary": "Six months of therapy.",
                        "interventions": [{"type": "DRUG", "name": "Polypill", "other_names": ["Finerenone, Empagliflozin"],
                                           "description": "A combination capsule"}],
                        "arms": [{"label": "Polypill", "type": "EXPERIMENTAL", "description": None,
                                  "interventions": ["Drug: Polypill"]}]})
    assert "Official title: A Polypill Strategy" in msg and "- DRUG: Polypill (also called Finerenone, Empagliflozin)" in msg
    assert "- Polypill (EXPERIMENTAL). Receives: Drug: Polypill." in msg
    # Background comes before the criteria, which are marked as the only source of rules
    assert msg.index("Background") < msg.index("<<<") and "the only source of rules" in msg


def test_member_schema_ddl_runs_and_the_prompt_leaves_out_unused_tables():
    import duckdb
    from webapp.backend.agents import member_schema
    duckdb.connect().execute(member_schema.ddl())
    text = member_schema.prompt_text()
    assert "rx_claims" in text and "mx_claims" in text and "ndc_lookup" not in text


def test_prompt_asks_for_progress_updates_the_page_can_show():
    # Claude Opus 5.5 rarely narrates between tool calls unless asked; without this the notes panel is empty
    assert "## Progress updates" in SYSTEM_PROMPT and "Before each round of lookups" in SYSTEM_PROMPT


def test_drug_names_become_a_whole_word_pattern_with_only_letters_and_digits():
    from webapp.backend.services.criteria_filter import name_pattern
    # Multi-word names match any separator; short names are dropped; quotes never reach the SQL
    assert name_pattern(["insulin glargine", "Jardiance", "O'Neil", "XY", "jardiance"]) == \
        "INSULIN[^A-Z0-9]+GLARGINE|JARDIANCE|O[^A-Z0-9]+NEIL"


def drug_rule(text, requirement, **kw):
    return {"source_text": text, "section": "inclusion", "requirement": requirement, "type": "medication",
            "codable": False, "drugs": [{"name": "empagliflozin", "rxcui": "1545653"}],
            "window": {"type": "days_ago", "min_days_ago": 90}, "reason_not_codable": "Duration isn't in claims.",
            "rationale": "x", **kw}


async def test_a_must_have_drug_duration_rule_filters_on_the_drug_and_flags_the_timing():
    r = await finalize([drug_rule("On metformin for at least 3 months", "must_have")])
    c = r.criteria[0]
    assert c.group == "codable" and c.partial and c.reason_not_codable == "Duration isn't in claims."
    assert "member_id IN" in c.sql and "serviced_dte" not in c.sql  # any fill, at any time
    assert c.sql in r.sql and "An expert checks" in c.window_text and r.definition["rules"][0]["partial"]


async def test_a_must_not_have_drug_duration_rule_stays_with_the_reviewer():
    r = await finalize([drug_rule("On metformin for at least 3 months", "must_not_have")])
    c = r.criteria[0]
    assert c.group == "needs_review" and not c.partial and "WHERE" not in r.sql  # removes no one


async def test_a_must_have_drug_rule_tied_to_an_open_event_filters_on_the_drug():
    r = await finalize([drug_rule("On metformin for at least 3 months", "must_have", codable=True,
                                  reason_not_codable=None, window={"type": "event", "event": "since surgery"})])
    c = r.criteria[0]
    assert c.group == "codable" and c.partial and "since surgery" in c.reason_not_codable


# ---------- retrying the model endpoint ----------

from tests.webapp.fakes_agent import api_error  # noqa: E402
from webapp.backend.agents import criteria_graph  # noqa: E402


async def run_with(turns, monkeypatch, retries=3, retry_seconds=5):
    waits = []

    async def fake_sleep(seconds):
        waits.append(seconds)
    monkeypatch.setattr(criteria_graph, "wait", fake_sleep)
    llm = ScriptedChatModel(turns=turns)
    graph = build_criteria_graph(llm, FakeIcd(), FakeRx(), model_name="fake", reference_date=REF,
                                 retries=retries, retry_seconds=retry_seconds)
    events = []
    async for mode, chunk in graph.astream({"context": CONTEXT}, {"recursion_limit": 100},
                                           stream_mode=["updates", "custom"]):
        if mode == "custom":
            events.append(chunk)
        elif "finalize" in chunk:
            events.append({"type": "done", "result": chunk["finalize"]["result"]})
    return events, waits, llm


async def test_a_refused_request_is_retried_after_the_wait(monkeypatch):
    events, waits, _ = await run_with([api_error(0), api_error(429)] + scripted_turns(), monkeypatch)
    assert waits == [5, 5] and events[-1]["type"] == "done" and events[-1]["result"].criteria
    assert events[0] == {"type": "status", "message": "The model endpoint didn't accept the request "
                         "(connection failed). Trying again in 5 s (retry 1 of 3)."}
    assert "(HTTP 429). Trying again in 5 s (retry 2 of 3)" in events[1]["message"]


async def test_server_errors_are_retried_but_a_bad_key_is_not(monkeypatch):
    events, waits, _ = await run_with([api_error(503)] + scripted_turns(), monkeypatch)
    assert waits == [5] and events[-1]["type"] == "done"
    with pytest.raises(anthropic.AuthenticationError):
        await run_with([api_error(401)] + scripted_turns(), monkeypatch)
    with pytest.raises(anthropic.BadRequestError):
        await run_with([api_error(400)] + scripted_turns(), monkeypatch)


async def test_retries_stop_after_the_limit(monkeypatch):
    with pytest.raises(anthropic.RateLimitError):
        await run_with([api_error(429)] * 3 + scripted_turns(), monkeypatch, retries=2)


def test_the_sdk_does_not_retry_on_its_own(monkeypatch):
    from webapp.backend import config
    from webapp.backend.agents.llm import make_criteria_llm
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY_SET", True)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    assert make_criteria_llm().max_retries == 0  # the graph's fixed-wait retries replace the SDK's quick ones
