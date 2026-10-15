"use strict";
// Member data schema (/data-schema): the tables the filter SQL runs against, from /api/member-schema.

mountNav({ active: "schema" });

const STATUS_LABEL = { required: "Required", reference: "Reference", preferred: "Preferred" };
let schema = null;

function tableHtml(t) {
  const rows = t.columns.map((c) => `
    <tr>
      <td><code>${escapeHtml(c.name)}</code>${t.key.includes(c.name) ? ' <span class="key-mark" title="Part of the primary key">key</span>' : ""}</td>
      <td><code>${escapeHtml(c.type)}</code>${c.nullable ? ' <span class="muted">(optional)</span>' : ""}</td>
      <td>${escapeHtml(c.description)}</td>
    </tr>`).join("");
  return `
    <section class="panel schema-table st-${t.status}" aria-labelledby="t-${t.name}">
      <div class="panel-head">
        <div class="schema-name-row">
          <h3 id="t-${t.name}"><code>${escapeHtml(t.name)}</code></h3>
          <span class="pill st-${t.status}">${STATUS_LABEL[t.status]}</span>
        </div>
        <p class="muted">${escapeHtml(t.grain[0].toUpperCase() + t.grain.slice(1))}.</p>
      </div>
      <div class="table-scroll schema-scroll">
        <table class="trials">
          <thead><tr><th>Column</th><th>Type</th><th>Meaning</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
      <p class="small-note">${escapeHtml(t.used_for)}</p>
    </section>`;
}

async function load() {
  const status = $("#status");
  try {
    const res = await fetch("/api/member-schema");
    if (!res.ok) throw new Error("The schema couldn't be loaded. Reload the page to try again.");
    schema = await res.json();
  } catch (err) {
    status.hidden = false;
    status.className = "status-msg error";
    status.innerHTML = `<h2>Schema not loaded</h2><p>${escapeHtml(err.message)}</p>`;
    return;
  }
  $("#tables").innerHTML = schema.tables.map(tableHtml).join("");
  $("#ddl").textContent = schema.ddl;
  $("#page").hidden = false;
}

$("#copy-ddl").addEventListener("click", (e) => schema && copyText(schema.ddl, e.currentTarget));
load();
