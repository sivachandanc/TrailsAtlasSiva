"""Tools the criteria agent can call. Each returns compact JSON the model reads; the same text is
shown on the page, so what the agent saw is exactly what a reviewer sees."""

import json

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from webapp.backend.models import AgentCriterion

SUBMIT = "submit_criteria"


class SearchIcdArgs(BaseModel):
    text: str = Field(description="Words describing the condition, e.g. 'pre-existing type 2 diabetes in pregnancy'")


class IcdCodeArgs(BaseModel):
    code: str = Field(description="An ICD-10-CM code with its dot, e.g. 'O24.11'")


class FindDrugArgs(BaseModel):
    name: str = Field(description="A drug ingredient, brand or product name, e.g. 'Jardiance' or 'metformin'")


class DrugClassArgs(BaseModel):
    query: str = Field(description="An ATC class ID like 'A10BK', or words from a class name like 'SGLT2' or 'insulin'")


class SubmitArgs(BaseModel):
    criteria: list[AgentCriterion] = Field(description="Every atomic rule, in the order they appear in the text")
    summary: str = Field(description="Two or three sentences: what the trial is looking for and what claims can't check")


def dumps(value) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def build_tools(umls, rxnorm) -> list[StructuredTool]:
    async def search_icd10cm(text: str) -> str:
        results = await umls.search_icd10cm(text)
        return dumps(results or {"result": "No ICD-10-CM codes matched. Try other words or a broader term."})

    async def icd10cm_code(code: str) -> str:
        found = await umls.icd10cm_code(code.strip().upper())
        return dumps(found or {"result": f"{code} is not an ICD-10-CM code."})

    async def icd10cm_children(code: str) -> str:
        children = await umls.icd10cm_children(code.strip().upper())
        return dumps(children or {"result": f"{code} has no more specific codes (or doesn't exist)."})

    async def find_drug(name: str) -> str:
        results = await rxnorm.find_drug(name)
        return dumps(results or {"result": f"No RxNorm drug matched '{name}'."})

    async def drug_class(query: str) -> str:
        results = await rxnorm.drug_class(query)
        return dumps(results or {"result": f"No ATC class matched '{query}'. Try an ATC ID or different words."})

    async def submit_criteria(criteria: list[AgentCriterion], summary: str) -> str:
        return f"Received {len(criteria)} rules. They'll be validated and turned into filters."

    def make(fn, schema, description):
        return StructuredTool.from_function(coroutine=fn, name=fn.__name__, description=description, args_schema=schema)

    return [
        make(search_icd10cm, SearchIcdArgs,
             "Search ICD-10-CM code names. Returns up to 12 {code, name} matches, most relevant first."),
        make(icd10cm_code, IcdCodeArgs,
             "Check that an ICD-10-CM code exists. Returns its official name and whether it has more specific codes."),
        make(icd10cm_children, IcdCodeArgs,
             "List the next level of more specific ICD-10-CM codes under a code."),
        make(find_drug, FindDrugArgs,
             "Find a drug in RxNorm by ingredient, brand or product name. Returns matches with their "
             "ingredients (RxCUI) and each ingredient's ATC classes."),
        make(drug_class, DrugClassArgs,
             "Find ATC drug classes by ID or name words. Returns each class with its member ingredients."),
        make(submit_criteria, SubmitArgs,
             "Submit the final list of atomic rules. Call once, after every criterion is covered."),
    ]
