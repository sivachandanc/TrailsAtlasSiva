"use strict";
// Hero page.

const params = new URLSearchParams(location.search);
const lookup = params.get("mode") === "nct";
mountNav({ active: lookup ? "lookup" : "" });
const box = mountSearchBox($("#hero-search"), { mode: lookup ? "nct" : "condition" });
if (lookup) box.input.focus();

// Decorative map: every state as one quiet tile, revealed once from west to east.
// It carries no data, so it's hidden from assistive tech and uses a single tone.
(function heroMap() {
  const el = $("#hero-map");
  for (const [code, , col, row] of window.US_TILES) {
    const t = document.createElement("span");
    t.className = "tile hero-tile";
    t.style.gridColumn = col + 1;
    t.style.gridRow = row + 1;
    t.style.setProperty("--d", `${col * 70 + row * 25}ms`);
    t.textContent = code;
    el.appendChild(t);
  }
})();

(async function liveCount() {
  const text = $("#live-count-text");
  try {
    const res = await fetch("/api/stats/open-trials");
    if (!res.ok) throw new Error();
    const { count } = await res.json();
    text.innerHTML = `<strong>${fmt.format(count)}</strong> trials are open to participants in the US right now`;
  } catch {
    $("#live-count").hidden = true; // the page works without it
  }
})();
