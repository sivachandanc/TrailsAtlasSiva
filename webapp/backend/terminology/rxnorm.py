"""RxNorm (RxNav / RxClass) client: drug names -> ingredients -> ATC classes -> NDCs.

RxNav is the US National Library of Medicine's free drug API; it needs no key.
"""

import asyncio
import re

import httpx

from webapp.backend.services.cache import TTLCache

RXNAV = "https://rxnav.nlm.nih.gov/REST"
ATC_ID = re.compile(r"^[A-Z]\d{2}([A-Z]{1,2}(\d{2})?)?$")
# Product term types whose NDCs make up an ingredient's packages
PRODUCT_TTYS = "SCD SBD GPCK BPCK"


class RxNormError(Exception):
    pass


class RxNormClient:
    def __init__(self, client: httpx.AsyncClient, cache_ttl: float = 24 * 60 * 60, max_concurrent: int = 8):
        self.client = client
        self._cache: TTLCache = TTLCache(cache_ttl, max_entries=20_000)
        self._limit = asyncio.Semaphore(max_concurrent)

    async def _json(self, path: str, params: dict | None = None) -> dict:
        key = ("get", path, tuple(sorted((params or {}).items())))
        if (hit := self._cache.get(key)) is not None:
            return hit
        async with self._limit:
            try:
                response = await self.client.get(f"{RXNAV}{path}", params=params)
            except httpx.HTTPError as exc:
                raise RxNormError(f"RxNav couldn't be reached: {exc.__class__.__name__}") from exc
        if response.status_code == 404:
            data = {}
        elif response.status_code >= 400:
            raise RxNormError(f"RxNav returned an error (HTTP {response.status_code}).")
        else:
            data = response.json() or {}
        self._cache.set(key, data)
        return data

    async def properties(self, rxcui: str) -> dict | None:
        p = (await self._json(f"/rxcui/{rxcui}/properties.json")).get("properties")
        return {"rxcui": p["rxcui"], "name": p["name"], "tty": p["tty"]} if p else None

    async def ingredients_of(self, rxcui: str) -> list[dict]:
        """Ingredients (IN) of any concept: itself if it's an ingredient, else the related ones."""
        props = await self.properties(rxcui)
        if props and props["tty"] == "IN":
            return [{"rxcui": props["rxcui"], "name": props["name"]}]
        groups = (await self._json(f"/rxcui/{rxcui}/related.json", {"tty": "IN"})).get("relatedGroup", {}).get("conceptGroup", [])
        return [{"rxcui": c["rxcui"], "name": c["name"]} for g in groups for c in g.get("conceptProperties", [])]

    async def atc_classes(self, rxcui: str) -> list[dict]:
        data = await self._json("/rxclass/class/byRxcui.json", {"rxcui": rxcui, "relaSource": "ATC"})
        infos = data.get("rxclassDrugInfoList", {}).get("rxclassDrugInfo", [])
        seen = {}
        for i in infos:
            c = i["rxclassMinConceptItem"]
            seen[c["classId"]] = c["className"]
        return [{"atc": k, "name": v} for k, v in sorted(seen.items())]

    async def find_drug(self, name: str, limit: int = 3) -> list[dict]:
        """Best matches for a drug name (ingredient, brand or product), each resolved to its
        ingredients and their ATC classes."""
        data = await self._json("/approximateTerm.json", {"term": name, "maxEntries": 10})
        candidates = data.get("approximateGroup", {}).get("candidate", [])
        rxcuis = list(dict.fromkeys(c["rxcui"] for c in candidates))[:limit]
        results = []
        for rxcui in rxcuis:
            props = await self.properties(rxcui)
            if not props:
                continue
            ingredients = await self.ingredients_of(rxcui)
            for ing in ingredients:
                ing["atc_classes"] = await self.atc_classes(ing["rxcui"])
            results.append({"rxcui": rxcui, "name": props["name"], "term_type": props["tty"], "ingredients": ingredients})
        return results

    async def all_atc_classes(self) -> list[dict]:
        data = await self._json("/rxclass/allClasses.json", {"classTypes": "ATC1-4"})
        items = data.get("rxclassMinConceptList", {}).get("rxclassMinConcept", [])
        return [{"atc": c["classId"], "name": c["className"]} for c in items]

    async def class_members(self, atc: str) -> list[dict]:
        data = await self._json("/rxclass/classMembers.json", {"classId": atc, "relaSource": "ATC", "ttys": "IN"})
        members = data.get("drugMemberGroup", {}).get("drugMember", [])
        return [{"rxcui": m["minConcept"]["rxcui"], "name": m["minConcept"]["name"]} for m in members]

    async def drug_class(self, query: str, limit: int = 5) -> list[dict]:
        """ATC classes matching an ATC ID ("A10BK") or words in a class name ("SGLT2"), with members."""
        q = query.strip()
        classes = await self.all_atc_classes()
        if ATC_ID.match(q.upper()):
            matches = [c for c in classes if c["atc"] == q.upper()]
        else:
            words = [w for w in re.split(r"\W+", q.lower()) if w]
            matches = [c for c in classes if all(w in c["name"].lower() for w in words)]
            matches.sort(key=lambda c: len(c["atc"]), reverse=True)  # most specific level first
        out = []
        for c in matches[:limit]:
            out.append({**c, "members": await self.class_members(c["atc"])})
        return out

    async def products_for_ingredient(self, rxcui: str) -> list[str]:
        """RxCUIs of every product (generic and branded drugs and packs) containing the ingredient."""
        groups = (await self._json(f"/rxcui/{rxcui}/related.json", {"tty": PRODUCT_TTYS})).get("relatedGroup", {}).get("conceptGroup", [])
        return list(dict.fromkeys(c["rxcui"] for g in groups for c in g.get("conceptProperties", [])))

    async def historical_ndcs(self, product_rxcui: str) -> list[str]:
        """Every NDC a product has ever had, including discontinued ones that older claims carry."""
        data = await self._json(f"/rxcui/{product_rxcui}/allhistoricalndcs.json")
        times = (data.get("historicalNdcConcept") or {}).get("historicalNdcTime", []) or []
        ndcs = set()
        for t in times:
            for n in t.get("ndcTime", []) or []:
                ndcs.update(n["ndc"] if isinstance(n.get("ndc"), list) else [n.get("ndc")])
        return sorted(n for n in ndcs if n)

    async def ndcs_for_ingredient(self, rxcui: str) -> list[str]:
        """Every NDC, current and historical, of every product containing the ingredient. Not capped:
        the list goes into the filter, so a partial list would silently miss members."""
        key = ("ndcs", rxcui)
        if (hit := self._cache.get(key)) is not None:
            return hit
        products = await self.products_for_ingredient(rxcui)
        current = await asyncio.gather(*(self._json(f"/rxcui/{p}/ndcs.json") for p in products))
        historical = await asyncio.gather(*(self.historical_ndcs(p) for p in products))
        ndcs = {n for d in current for n in (d.get("ndcGroup", {}).get("ndcList", {}) or {}).get("ndc", []) or []}
        ndcs.update(n for lst in historical for n in lst)
        ndcs = sorted(ndcs)
        self._cache.set(key, ndcs)
        return ndcs

    async def brand_names(self, rxcui: str) -> list[str]:
        """Brand names of products containing the ingredient, e.g. empagliflozin -> Jardiance, Synjardy."""
        groups = (await self._json(f"/rxcui/{rxcui}/related.json", {"tty": "BN"})).get("relatedGroup", {}).get("conceptGroup", [])
        return sorted({c["name"] for g in groups for c in g.get("conceptProperties", [])})
