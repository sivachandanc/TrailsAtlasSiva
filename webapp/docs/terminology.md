# Terminology clients: UMLS and RxNorm

Both are async clients on the shared `httpx` client, with an in-memory 24-hour cache (20,000
entries) and at most 8 calls in flight. They're used by the agent's tools (during step 2) and by
the checks after the agent submits.

## UMLS: ICD-10-CM (`terminology/umls.py`)

Needs `UMLS_API_KEY`. The key is sent as the `apiKey` query parameter and never to the browser.

| Method | UMLS call | Returns |
|---|---|---|
| `search_icd10cm(text, limit=12)` | `GET /search/current?string=…&sabs=ICD10CM&returnIdType=code&searchType=words` | `[{code, name}]` (UMLS's `NONE` result is dropped) |
| `icd10cm_code(code)` | `GET /content/current/source/ICD10CM/{code}` | `{code, name, has_children}` or `None` |
| `icd10cm_children(code)` | `GET /content/current/source/ICD10CM/{code}/children` | `[{code, name}]` |

**Internals:**
- `_get()`: no key → `UmlsNotConfigured`; 404 → `None` (UMLS's "nothing here"); 401/403 → "rejected
  the key"; other errors and network failures → `UmlsError`.
- `_cached()`: caches each answer by its key, e.g. `("icd_code", "E11")`.

## RxNorm: drugs and NDCs (`terminology/rxnorm.py`)

RxNav (NLM's free drug API), no key. `_json()` caches every GET by URL and parameters; 404 → `{}`;
other errors → `RxNormError`.

| Method | RxNav call | Returns |
|---|---|---|
| `properties(rxcui)` | `/rxcui/{id}/properties.json` | `{rxcui, name, tty}` or `None` |
| `ingredients_of(rxcui)` | itself if it's an ingredient (`tty IN`), else `/rxcui/{id}/related.json?tty=IN` | `[{rxcui, name}]` |
| `atc_classes(rxcui)` | `/rxclass/class/byRxcui.json?relaSource=ATC` | `[{atc, name}]` |
| `find_drug(name, limit=3)` | `/approximateTerm.json`, then properties, ingredients and ATC classes for the top 3 | matches with their ingredients and classes |
| `all_atc_classes()` | `/rxclass/allClasses.json?classTypes=ATC1-4` | every ATC class (cached) |
| `class_members(atc)` | `/rxclass/classMembers.json?relaSource=ATC&ttys=IN` | member ingredients |
| `drug_class(query, limit=5)` | an exact ATC ID (regex `^[A-Z]\d{2}([A-Z]{1,2}(\d{2})?)?$`), or every word in a class name; most specific level first | classes with members |
| `products_for_ingredient(rxcui)` | `/related.json?tty=SCD SBD GPCK BPCK` | product RxCUIs (generic and brand drugs and packs) |
| `historical_ndcs(product)` | `/rxcui/{id}/allhistoricalndcs.json` | every NDC the product ever had, including discontinued ones |
| `ndcs_for_ingredient(rxcui)` | current `/ndcs.json` plus historical NDCs for every product; **no cap** | sorted NDCs (cached per ingredient) |
| `brand_names(rxcui)` | `/related.json?tty=BN` | brand names, e.g. empagliflozin → Glyxambi, Jardiance, Synjardy, Trijardy |

**Why historical NDCs:** older claims carry discontinued NDCs. On one Jardiance product, the
current list has 11 NDCs and the historical list 13.

**Why no cap:** the NDC list goes into the filter, so a partial list would silently miss members.
The SGLT2 class (A10BK) resolves to 6 ingredients, about 390 NDCs and 21 names in about 3 seconds.

### The drug hierarchy
```
ATC class     A10BK  SGLT2 inhibitors          ← drug_class
Ingredient    empagliflozin (RxCUI 1545653)    ← find_drug / class_members
Brand         Jardiance, Synjardy, …           ← brand_names        → matched against brand_nme
Product       empagliflozin 10 MG Oral Tablet [Jardiance]
Package       NDC 00597015207                  ← ndcs_for_ingredient → matched against product_id
```
