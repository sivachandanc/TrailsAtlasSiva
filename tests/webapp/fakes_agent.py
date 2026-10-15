"""Scripted stand-ins for the chat model and the terminology clients (no network, no API key)."""

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

ELIGIBILITY = """Inclusion Criteria:

* Adults 18 years or older
* Pre-existing type 2 diabetes in pregnancy
* HbA1c between 7% and 10%

Exclusion Criteria:

* Use of an SGLT2 inhibitor in the past 3 months
* Unable to give informed consent"""

CONTEXT = {
    "trial_id": "NCT00000001", "title": "A test trial", "conditions": ["Type 2 Diabetes"],
    "diagnosis_prefixes": ["E11", "O24"], "age_rule": "18 years and older", "sex_rule": "Any sex",
    "study_population": None, "eligibility_text": ELIGIBILITY,
}


def call(name, args, id_):
    return {"name": name, "args": args, "id": id_, "type": "tool_call"}


SUBMISSION = {
    "summary": "Pregnant women with pre-existing type 2 diabetes, not on SGLT2 inhibitors.",
    "criteria": [
        {"source_text": "Adults 18 years or older", "section": "inclusion", "requirement": "must_have", "type": "age_sex",
         "codable": True, "window": {"type": "not_stated"}, "covered_by_step": "age_sex",
         "rationale": "Same as the age limit in step 2."},
        {"source_text": "Pre-existing type 2 diabetes in pregnancy", "section": "inclusion", "requirement": "must_have",
         "type": "diagnosis", "codable": True, "icd10cm_codes": ["O24.11", "O24.999"], "window": {"type": "ever"},
         "rationale": "O24.11 is pre-existing type 2 diabetes in pregnancy."},
        {"source_text": "HbA1c between 7% and 10%", "section": "inclusion", "requirement": "must_have", "type": "lab",
         "codable": False, "window": {"type": "not_stated"}, "reason_not_codable": "Lab results aren't in claims.",
         "rationale": "Needs a lab value."},
        {"source_text": "Use of an SGLT2 inhibitor in the past 3 months", "section": "exclusion",
         "requirement": "must_not_have", "type": "medication", "codable": True,
         "drugs": [{"name": "SGLT2 inhibitors", "atc_class": "A10BK"}], "window": {"type": "days_ago", "max_days_ago": 90},
         "rationale": "Any SGLT2 inhibitor fill in the last 90 days excludes the member."},
        {"source_text": "Takes made-up drug daily", "section": "inclusion", "requirement": "must_have",
         "type": "medication", "codable": True, "drugs": [{"name": "nothing", "rxcui": "999"}],
         "window": {"type": "ever"}, "rationale": "Invented to test validation."},
    ],
}


def scripted_turns():
    return [
        AIMessage(content=[{"type": "thinking", "thinking": "Looking up the diabetes codes and the drug class."}],
                  tool_calls=[call("search_icd10cm", {"text": "pre-existing type 2 diabetes in pregnancy"}, "t1"),
                              call("drug_class", {"query": "SGLT2"}, "t2")],
                  usage_metadata={"input_tokens": 1000, "output_tokens": 100, "total_tokens": 1100}),
        AIMessage(content="", tool_calls=[call("icd10cm_code", {"code": "O24.11"}, "t3")],
                  usage_metadata={"input_tokens": 1200, "output_tokens": 50, "total_tokens": 1250}),
        AIMessage(content="", tool_calls=[call("submit_criteria", SUBMISSION, "t4")],
                  usage_metadata={"input_tokens": 1500, "output_tokens": 800, "total_tokens": 2300}),
    ]


class ScriptedChatModel(BaseChatModel):
    """Returns the scripted turns in order; bind_tools is a no-op."""
    turns: list
    seen: list = []

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        raise NotImplementedError

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        self.seen.append(list(messages))
        return ChatResult(generations=[ChatGeneration(message=self.turns.pop(0))])


class FakeIcd:
    CODES = {"O24.11": "Pre-existing type 2 diabetes mellitus, in pregnancy", "E11": "Type 2 diabetes mellitus"}

    async def search_icd10cm(self, text):
        return [{"code": "O24.11", "name": self.CODES["O24.11"]}]

    async def icd10cm_code(self, code):
        return {"code": code, "name": self.CODES[code], "has_children": True} if code in self.CODES else None

    async def icd10cm_children(self, code):
        return []


class FakeRx:
    async def properties(self, rxcui):
        return {"rxcui": "1545653", "name": "empagliflozin", "tty": "IN"} if rxcui == "1545653" else None

    async def ingredients_of(self, rxcui):
        return [{"rxcui": "1545653", "name": "empagliflozin"}] if rxcui == "1545653" else []

    async def find_drug(self, name):
        return []

    async def drug_class(self, query):
        if query.upper() in ("SGLT2", "A10BK"):
            return [{"atc": "A10BK", "name": "SGLT2 inhibitors",
                     "members": [{"rxcui": "1545653", "name": "empagliflozin"}, {"rxcui": "1488564", "name": "dapagliflozin"}]}]
        return []

    async def ndcs_for_ingredient(self, rxcui):
        return {"1545653": ["00597015207", "00597015230"], "1488564": ["00310620530"]}.get(rxcui, [])
