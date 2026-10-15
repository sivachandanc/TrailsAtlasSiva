"""UMLS client for the ICD step: MeSH -> CUI -> ICD-10-CM.

An async port of internal_core/get_icd_codes.py (copied, not imported, so the app doesn't
depend on the sandbox). Every call returns enough detail for the page to show how each
ICD code was reached.
"""

import asyncio

import httpx

from webapp.backend import config
from webapp.backend.models import CuiRef
from webapp.backend.services.cache import TTLCache


class UmlsNotConfigured(Exception):
    """No UMLS API key is set."""


class UmlsError(Exception):
    """UMLS couldn't be reached, rejected the key, or returned something unusable."""


class UmlsClient:
    def __init__(self, client: httpx.AsyncClient, api_key: str | None):
        self.client = client
        self.api_key = api_key
        self._cache: TTLCache = TTLCache(config.UMLS_CACHE_TTL_SECONDS, max_entries=20_000)
        self._limit = asyncio.Semaphore(config.UMLS_MAX_CONCURRENT)

    async def _get(self, path: str, params: dict | None = None) -> httpx.Response | None:
        """GET with the key; None for 404 (UMLS's answer for "nothing linked")."""
        if not self.api_key:
            raise UmlsNotConfigured("ICD lookups need a UMLS API key. Add UMLS_API_KEY to the .env file.")
        async with self._limit:
            try:
                response = await self.client.get(f"{config.UMLS_BASE_URL}{path}",
                                                 params={**(params or {}), "apiKey": self.api_key})
            except httpx.HTTPError as exc:
                raise UmlsError(f"UMLS couldn't be reached: {exc.__class__.__name__}") from exc
        if response.status_code == 404:
            return None
        if response.status_code in (401, 403):
            raise UmlsError("UMLS rejected the API key. Check UMLS_API_KEY in the .env file.")
        if response.status_code >= 400:
            raise UmlsError(f"UMLS returned an error (HTTP {response.status_code}).")
        return response

    async def _cached(self, key: tuple, fetch):
        if (hit := self._cache.get(key)) is not None:
            return hit
        value = await fetch()
        self._cache.set(key, value)
        return value

    async def search(self, string: str, input_type: str, search_type: str = "exact",
                     sabs: str | None = "MSH") -> list[CuiRef]:
        """Concepts (CUIs) for a string. input_type "sourceUi" = a vocabulary's own ID (a MeSH ID),
        "atom" = a term. sabs limits matches to one vocabulary; None searches all of UMLS."""
        async def fetch():
            params = {"string": string, "inputType": input_type, "searchType": search_type,
                      "returnIdType": "concept"}
            if sabs:
                params["sabs"] = sabs
            response = await self._get("/search/current", params)
            results = response.json().get("result", {}).get("results", []) if response else []
            # UMLS returns a single ui "NONE" result when nothing matches
            return [CuiRef(cui=r["ui"], name=r.get("name")) for r in results if r.get("ui") != "NONE"]
        return await self._cached(("search", string, input_type, search_type, sabs), fetch)

    async def mesh_lookup(self, mesh_id: str, term: str) -> dict:
        """CUIs from the MeSH ID and from the MeSH term (exact, MeSH only), and which are kept:
        the CUIs both lookups agree on; if the term finds nothing in common, the ID's CUIs."""
        by_id, by_term = await asyncio.gather(self.search(mesh_id, "sourceUi"), self.search(term, "atom"))
        term_cuis = {c.cui for c in by_term}
        both = [c for c in by_id if c.cui in term_cuis]
        if both:
            return {"by_id": by_id, "by_term": by_term, "kept": both, "match": "id+term"}
        if by_id:
            return {"by_id": by_id, "by_term": by_term, "kept": by_id, "match": "id only"}
        return {"by_id": by_id, "by_term": by_term, "kept": [], "match": "none"}

    async def condition_lookup(self, text: str) -> list[CuiRef]:
        """Sponsor's condition text: normalizedString tolerates case, plurals and word order;
        "words" was too loose (it matched e.g. "No evidence of prenatal infection")."""
        return await self.search(text, "atom", search_type="normalizedString", sabs=None)

    async def icd_codes(self, cui: str) -> list[str]:
        """ICD-10-CM codes linked to a concept (its ICD-10-CM atoms), e.g. ["O24.91", "O24.919"]."""
        async def fetch():
            response = await self._get(f"/content/current/CUI/{cui}/atoms",
                                       {"sabs": config.ICD_SOURCE, "pageSize": 100})
            atoms = response.json().get("result", []) if response else []
            # atom["code"] is a URL like .../source/ICD10CM/E11.9 -> the code is the last segment
            return list(dict.fromkeys(a["code"].rsplit("/", 1)[-1] for a in atoms))
        return await self._cached(("icd", cui), fetch)

    async def icd_name(self, code: str) -> str | None:
        """Official ICD-10-CM name for a code or category ("O24.91", "O24"); None if unknown."""
        async def fetch():
            response = await self._get(f"/content/current/source/{config.ICD_SOURCE}/{code}")
            return response.json().get("result", {}).get("name") if response else ""
        name = await self._cached(("icd_name", code), fetch)
        return name or None

    # ---- ICD-10-CM directly (used by the criteria agent's tools) ----

    async def search_icd10cm(self, text: str, limit: int = 12) -> list[dict]:
        """ICD-10-CM codes whose names match the words in `text`, e.g. "pre-existing type 2
        diabetes in pregnancy" -> O24.11, O24.119, ... Returns [{code, name}]."""
        async def fetch():
            response = await self._get("/search/current", {
                "string": text, "sabs": config.ICD_SOURCE, "returnIdType": "code",
                "searchType": "words", "pageSize": limit,
            })
            results = response.json().get("result", {}).get("results", []) if response else []
            return [{"code": r["ui"], "name": r.get("name")} for r in results if r.get("ui") != "NONE"]
        return await self._cached(("search_icd", text.lower(), limit), fetch)

    async def icd10cm_code(self, code: str) -> dict | None:
        """{code, name, has_children} for a real ICD-10-CM code, None if it doesn't exist."""
        async def fetch():
            response = await self._get(f"/content/current/source/{config.ICD_SOURCE}/{code}")
            if not response:
                return {}
            r = response.json().get("result", {})
            return {"code": r.get("ui", code), "name": r.get("name"), "has_children": r.get("children") not in (None, "NONE")}
        found = await self._cached(("icd_code", code.upper()), fetch)
        return found or None

    async def icd10cm_children(self, code: str) -> list[dict]:
        """The next level of more specific codes under `code`; [] for a code with none."""
        async def fetch():
            response = await self._get(f"/content/current/source/{config.ICD_SOURCE}/{code}/children")
            results = response.json().get("result", []) if response else []
            return [{"code": r["ui"], "name": r.get("name")} for r in results]
        return await self._cached(("icd_children", code.upper()), fetch)
