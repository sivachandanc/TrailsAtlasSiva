"""Regenerate zip_centroids.csv from the US Census Gazetteer ZCTA file.

    uv run python -m webapp.backend.geo.build_zip_centroids [year]

A ZCTA (ZIP Code Tabulation Area) is the Census Bureau's area for a 5-digit ZIP code; its internal
point is a good stand-in for "where people with this ZIP live". PO-box-only ZIPs have no ZCTA.
"""

import csv
import io
import sys
import zipfile
from pathlib import Path

import httpx

OUT = Path(__file__).with_name("zip_centroids.csv")
URL = "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/{year}_Gazetteer/{year}_Gaz_zcta_national.zip"


def main(year: str = "2024") -> None:
    response = httpx.get(URL.format(year=year), timeout=120, follow_redirects=True)
    response.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(response.content)) as z:
        text = z.read(z.namelist()[0]).decode("utf-8")
    rows = list(csv.reader(io.StringIO(text), delimiter="\t"))
    header = [h.strip() for h in rows[0]]
    zip_i, lat_i, lon_i = header.index("GEOID"), header.index("INTPTLAT"), header.index("INTPTLONG")
    points = sorted((r[zip_i].strip(), round(float(r[lat_i]), 5), round(float(r[lon_i]), 5)) for r in rows[1:] if r)
    with OUT.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["zip", "lat", "lon"])
        w.writerows(points)
    print(f"Wrote {len(points):,} ZIP centroids ({year} Census ZCTAs) to {OUT}")


if __name__ == "__main__":
    main(*sys.argv[1:2])
