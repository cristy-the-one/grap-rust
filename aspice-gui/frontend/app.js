"use strict";

/* ---------- tiny DOM helpers ---------- */
const $ = (sel) => document.querySelector(sel);

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === "class") node.className = v;
    else if (k === "html") node.innerHTML = v;
    else if (k === "dataset") Object.assign(node.dataset, v);
    else if (k.startsWith("on") && typeof v === "function") node[k] = v;
    else node.setAttribute(k, v);
  }
  for (const c of children.flat()) {
    if (c == null || c === false) continue;
    node.append(c.nodeType ? c : document.createTextNode(String(c)));
  }
  return node;
}

function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
  return node;
}

/* ---------- API ---------- */
async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const body = await res.json().catch(() => ({}));
  showRaw(body);
  if (!res.ok) {
    const msg = body.detail || body.message || res.statusText;
    throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return body;
}

const post = (path, payload) =>
  api(path, { method: "POST", body: JSON.stringify(payload ?? {}) });

function showRaw(data) {
  $("#output").textContent =
    typeof data === "string" ? data : JSON.stringify(data, null, 2);
}

let toastTimer = null;
function toast(message, kind = "ok") {
  const t = $("#toast");
  t.textContent = message;
  t.className = `toast ${kind}`;
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.hidden = true), 4200);
}

// Run an async action: surface errors as a toast, never throw to the console.
async function run(fn, okMsg) {
  try {
    const r = await fn();
    if (r && r.status === "error") {
      toast(r.message || "error", "err");
    } else if (okMsg) {
      toast(okMsg, "ok");
    }
    return r;
  } catch (err) {
    toast(String(err.message || err), "err");
    return null;
  }
}

/* ---------- status bar ---------- */
function pill(label, value, kind = "") {
  return el("span", { class: `pill ${kind}` },
    el("span", { class: "pill-k" }, label),
    el("span", { class: "pill-v" }, value)
  );
}

async function refreshStatus() {
  let st, role;
  try {
    [st, role] = await Promise.all([api("/api/status"), api("/api/role")]);
  } catch {
    clear($("#statusbar-pills")).append(pill("server", "unreachable", "err"));
    return null;
  }
  const pills = clear($("#statusbar-pills"));
  const kg = st.kg || {};
  const pg = st.pg || {};
  const lc = st.lifecycle || {};
  pills.append(
    pill("KG", kg.loaded ? `${kg.node_count} nodes` : "none", kg.loaded ? "ok" : ""),
    pill("PG", pg.loaded ? `${pg.name || "?"} · ${pg.step_count} steps` : "none", pg.loaded ? "ok" : ""),
    pill("Production", lc.production_id || "—", lc.production_id ? "ok" : ""),
    pill("Role", (role && role.role) || "?", "accent")
  );
  if (role && role.role) $("#role-select").value = role.role;
  return st;
}

/* ---------- gap cards ---------- */
function confidenceClass(c) {
  if (c >= 0.66) return "high";
  if (c >= 0.34) return "med";
  return "low";
}

function bestSignal(gap) {
  const lex = (gap.signals && gap.signals.lexical) || [];
  return lex.slice().sort((a, b) => (b.coverage || 0) - (a.coverage || 0))[0] || null;
}

function gapCard(gap) {
  const conf = gap.confidence ?? 0;
  const pct = Math.round(conf * 100);
  const cov = Math.round((1 - conf) * 100);
  const cls = confidenceClass(conf);
  const target = `${gap.outcome_id}:${gap.missing_characteristic}`;
  const top = bestSignal(gap);

  const detail = el("div", { class: "gap-detail", hidden: true });
  let loaded = false;

  const expandBtn = el("button", {
    class: "btn btn-ghost small",
    onclick: async () => {
      const open = detail.hidden;
      detail.hidden = !open;
      expandBtn.textContent = open ? "Hide explanation" : "Explain";
      if (open && !loaded) {
        loaded = true;
        detail.append(el("p", { class: "muted small" }, "loading explanation…"));
        const r = await run(() => post("/api/gaps/explain", { target }));
        clear(detail);
        if (r && r.explanation) renderExplanation(r.explanation, detail);
        else detail.append(el("p", { class: "muted small" }, "No stored explanation. Run detection first."));
      }
    },
  }, "Explain");

  return el("div", { class: `gap-card ${cls}` },
    el("div", { class: "gap-head" },
      el("div", {},
        el("div", { class: "gap-char" }, gap.missing_characteristic),
        el("div", { class: "gap-outcome" }, `${gap.outcome_id} · ${gap.outcome_name || ""}`)
      ),
      el("div", { class: "gap-conf" },
        el("span", { class: `conf-num ${cls}` }, `${pct}%`),
        el("span", { class: "muted small" }, "gap confidence")
      )
    ),
    el("div", { class: "meter", title: `coverage ${cov}%` },
      el("div", { class: `meter-fill ${cls}`, style: `width:${pct}%` })
    ),
    top
      ? el("div", { class: "gap-evidence" },
          el("span", { class: "muted small" }, `closest evidence · coverage ${Math.round((top.coverage || 0) * 100)}%`),
          el("p", { class: "snippet" }, top.snippet || "(no text)"))
      : el("p", { class: "muted small" }, "no supporting evidence found"),
    el("div", { class: "gap-actions" }, expandBtn),
    detail
  );
}

function renderExplanation(exp, container) {
  const s = exp.sections || {};
  const structured = s.structured || {};
  const stat = s.statistical || {};
  container.append(
    el("div", { class: "explain" },
      el("p", { class: "explain-rendered" }, s.rendered || ""),
      el("dl", { class: "kv" },
        el("dt", {}, "Outcome"), el("dd", {}, structured.outcome || "—"),
        el("dt", {}, "Characteristic"), el("dd", {}, structured.characteristic || "—"),
        el("dt", {}, "Expected"), el("dd", {}, structured.expected || "see ontology"),
        el("dt", {}, "Threshold"), el("dd", {}, String(stat.threshold ?? "—"))
      ),
      (stat.queries && stat.queries.length)
        ? el("div", {},
            el("span", { class: "muted small" }, "queries"),
            el("div", { class: "chips" }, stat.queries.map((q) => el("span", { class: "chip" }, q))))
        : null,
      (stat.top_hits && stat.top_hits.length)
        ? el("table", { class: "hits" },
            el("thead", {}, el("tr", {},
              el("th", {}, "node"), el("th", {}, "score"), el("th", {}, "snippet"))),
            el("tbody", {}, stat.top_hits.map((h) => el("tr", {},
              el("td", {}, String(h.node ?? "—")),
              el("td", {}, (h.score ?? 0).toFixed ? h.score.toFixed(2) : String(h.score)),
              el("td", {}, h.snippet || "")))))
        : el("p", { class: "muted small" }, "no candidate evidence above threshold")
    )
  );
}

function renderGaps(result, container, metaEl) {
  clear(container);
  const gaps = (result && result.gaps) || [];
  if (metaEl && result) {
    metaEl.textContent = result.run_id
      ? `run ${result.run_id} · ${result.kind || "production"} · scope "${result.scope || "all"}"`
      : "";
  }
  if (!gaps.length) {
    container.append(el("div", { class: "empty-ok" },
      el("strong", {}, "No gaps detected."),
      el("p", { class: "muted small" }, "Every required characteristic in scope has supporting evidence.")));
    return;
  }
  container.append(el("div", { class: "gaps-summary" },
    el("span", { class: "badge err" }, `${gaps.length} gap${gaps.length === 1 ? "" : "s"}`)));
  gaps.forEach((g) => container.append(gapCard(g)));
}

/* ---------- bootstrap steps ---------- */
const STEP_LABELS = {
  knowledge_graph: "Create knowledge graph",
  ingest: "Ingest sample documents",
  ontology: "Load & project ontology",
  procedure_graph: "Load procedure graph",
};

function stepSummary(key, payload) {
  if (!payload) return "";
  if (payload.status === "error") return payload.message || "error";
  switch (key) {
    case "knowledge_graph": return `empty store ready (${payload.node_count ?? 0} nodes)`;
    case "ingest": return `${payload.nodes_added ?? 0} nodes from ${payload.files ?? 0} file(s)`;
    case "ontology": return `${payload.outcomes_projected ?? 0} outcomes · ${payload.characteristics_projected ?? 0} characteristics`;
    case "procedure_graph": return `${payload.name || "PG"} · ${payload.step_count ?? 0} steps`;
    default: return payload.message || "ok";
  }
}

function renderBootstrapSteps(steps) {
  const list = clear($("#bootstrap-steps"));
  $("#bootstrap-steps").hidden = false;
  steps.forEach((entry) => {
    const [key, payload] = Object.entries(entry)[0];
    const ok = payload && payload.status !== "error";
    list.append(el("li", { class: `step ${ok ? "ok" : "err"}` },
      el("span", { class: "step-mark" }, ok ? "✓" : "✕"),
      el("div", {},
        el("div", { class: "step-title" }, STEP_LABELS[key] || key),
        el("div", { class: "muted small" }, stepSummary(key, payload)))));
  });
}

/* ---------- runs timeline ---------- */
function runRow(r) {
  const kindCls = r.kind === "experiment" ? "warn" : "ok";
  const reconWrap = el("div", { class: "recon", hidden: true });
  const reconBtn = el("button", {
    class: "btn btn-ghost small",
    onclick: async () => {
      const open = reconWrap.hidden;
      reconWrap.hidden = !open;
      if (open && !reconWrap.dataset.loaded) {
        reconWrap.dataset.loaded = "1";
        const rep = await run(() => api(`/api/runs/${encodeURIComponent(r.run_id)}/reconstruct`));
        if (rep) renderReconstruct(rep, clear(reconWrap));
      }
    },
  }, "Reconstruct");

  return el("div", { class: "trow" },
    el("div", { class: "tdot " + kindCls }),
    el("div", { class: "tbody" },
      el("div", { class: "trow-head" },
        el("code", { class: "run-id" }, r.run_id),
        el("span", { class: `badge ${kindCls}` }, r.kind),
        el("span", { class: "badge" }, r.status),
        el("span", { class: "gaps-pill" }, `${r.gaps_found ?? 0} gaps`)
      ),
      el("div", { class: "trow-actions" }, reconBtn),
      reconWrap
    )
  );
}

function renderReconstruct(rep, container) {
  const ok = rep.complete;
  container.append(el("div", { class: `recon-box ${ok ? "ok" : "warn"}` },
    el("div", { class: "recon-head" },
      el("span", { class: `badge ${ok ? "ok" : "warn"}` }, ok ? "complete" : "incomplete"),
      el("span", { class: "muted small" }, `${rep.explanations_present}/${rep.explanations_total} explanations present`)),
    el("dl", { class: "kv" },
      el("dt", {}, "PG version"), el("dd", {}, rep.pg_version_present ? "present" : "missing"),
      el("dt", {}, "KG version"), el("dd", {}, rep.kg_version_present ? "present" : "missing")),
    (rep.missing && rep.missing.length)
      ? el("div", {}, el("span", { class: "muted small" }, "missing artifacts"),
          el("div", { class: "chips" }, rep.missing.map((m) => el("span", { class: "chip err" }, m))))
      : null
  ));
}

function renderRuns(runs) {
  const t = clear($("#runs-timeline"));
  if (!runs.length) {
    t.append(el("p", { class: "muted" }, "No runs recorded yet."));
    return;
  }
  runs.slice().reverse().forEach((r) => t.append(runRow(r)));
}

/* ---------- events / audit feeds ---------- */
function fmtTs(ts) {
  if (!ts) return "";
  const n = Number(ts);
  if (Number.isFinite(n) && n > 1e11) return new Date(n).toLocaleString();
  return String(ts);
}

function renderAudit(events) {
  const f = clear($("#audit-feed"));
  if (!events.length) { f.append(el("p", { class: "muted" }, "No audit events.")); return; }
  events.slice().reverse().forEach((e) =>
    f.append(el("div", { class: "feed-row" },
      el("span", { class: "feed-type" }, e.type),
      el("span", { class: "feed-subject" }, e.subject || ""),
      e.rationale ? el("span", { class: "feed-rationale" }, `“${e.rationale}”`) : null,
      el("span", { class: "feed-ts muted small" }, fmtTs(e.ts)))));
}

function renderEvents(events) {
  const f = clear($("#events-feed"));
  if (!events.length) { f.append(el("p", { class: "muted small" }, "No matching events.")); return; }
  events.slice().reverse().forEach((e) => {
    const links = (e.links || []).map((l) => el("span", { class: "chip" }, `${l.relation}:${l.kind}`));
    f.append(el("div", { class: "feed-row" },
      el("span", { class: "feed-type" }, e.type),
      el("span", { class: "feed-subject" }, e.subject || ""),
      links.length ? el("div", { class: "chips" }, links) : null,
      el("span", { class: "feed-ts muted small" }, fmtTs(e.ts))));
  });
}

/* ---------- quality tiles ---------- */
const QUALITY_TILES = [
  ["production_runs", "Production runs"],
  ["production_completed", "Completed"],
  ["latest_gaps_found", "Latest gaps"],
  ["experiments_excluded", "Experiments (quarantined)"],
];

function renderQuality(m) {
  const wrap = clear($("#quality-tiles"));
  QUALITY_TILES.forEach(([key, label]) => {
    const v = m[key];
    wrap.append(el("div", { class: "tile" },
      el("div", { class: "tile-val" }, v == null ? "—" : String(v)),
      el("div", { class: "tile-label muted small" }, label)));
  });
  wrap.append(el("div", { class: "tile wide" },
    el("div", { class: "tile-val small" }, m.last_production_run || "—"),
    el("div", { class: "tile-label muted small" }, "Last production run")));
}

/* ---------- versions / config ---------- */
function renderVersions(versions) {
  const c = clear($("#versions-list"));
  if (!versions.length) { c.append(el("p", { class: "muted" }, "No versions published.")); return; }
  versions.forEach((v) => c.append(el("div", { class: "feed-row" },
    el("code", {}, v.hash), el("span", { class: "badge" }, v.kind))));
}

function renderConfig(cfg) {
  const c = clear($("#config-view"));
  const weights = cfg.weights || {};
  const retention = cfg.retention || {};
  c.append(
    el("div", { class: "config-cols" },
      el("div", {},
        el("h4", {}, "Weights"),
        Object.keys(weights).length
          ? el("dl", { class: "kv" }, Object.entries(weights).flatMap(([k, v]) =>
              [el("dt", {}, k), el("dd", {}, String(v))]))
          : el("p", { class: "muted small" }, "defaults (none overridden)")),
      el("div", {},
        el("h4", {}, "Retention (days)"),
        el("dl", { class: "kv" }, Object.entries(retention).flatMap(([k, v]) =>
          [el("dt", {}, k), el("dd", {}, String(v))])))));
}

/* ---------- navigation ---------- */
document.querySelectorAll(".nav").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".nav").forEach((b) => b.classList.remove("active"));
    document.querySelectorAll(".panel").forEach((p) => p.classList.remove("active"));
    btn.classList.add("active");
    $(`#panel-${btn.dataset.panel}`).classList.add("active");
  });
});

$("#role-select").addEventListener("change", async (e) => {
  await run(() => post("/api/role", { role: e.target.value }), `role → ${e.target.value}`);
  await refreshStatus();
});

$("#btn-refresh-status").onclick = () => refreshStatus();

/* ---------- dashboard / bootstrap ---------- */
$("#btn-bootstrap").onclick = () =>
  run(async () => {
    const r = await post("/api/wizard/bootstrap");
    if (r && r.steps) renderBootstrapSteps(r.steps);
    if (r && r.gaps) {
      $("#dashboard-gaps-card").hidden = false;
      $("#dashboard-gap-count").textContent = `${r.gaps.gaps_found ?? 0} gaps`;
      renderGaps(r.gaps, $("#dashboard-gaps"));
    }
    await refreshStatus();
    if (r && r.status !== "error") toast("Bootstrap complete", "ok");
    return r;
  });

/* ---------- knowledge extraction demo (one-click, mirrors demo_kee) ---------- */
const KEE_STEP_LABELS = {
  knowledge_graph: "Create fresh knowledge graph",
  extract: "Extract ontology from documents (LLM)",
  adopt_ontology: "Adopt: load the proposed ontology",
  ingest_evidence: "Ingest project evidence",
};

function keeStepSummary(key, p) {
  if (!p) return "";
  if (p.status === "error") return p.message || "error";
  switch (key) {
    case "knowledge_graph": return "empty store ready";
    case "extract": return `${p.provider}/${p.model || "?"} · ${p.chunks} chunks → ` +
      `${p.fragments} fragments → ${(p.process_outcomes || []).length} outcomes`;
    case "adopt_ontology": return `${p.outcomes_projected ?? 0} outcomes projected`;
    case "ingest_evidence": return `${p.nodes_added ?? 0} nodes from ${p.files ?? 0} file(s)`;
    default: return p.message || "ok";
  }
}

function renderKeeDemo(r, container) {
  clear(container);
  // 1. step checklist
  const list = el("ol", { class: "steps" });
  (r.steps || []).forEach((entry) => {
    const [key, p] = Object.entries(entry)[0];
    const ok = p && p.status !== "error";
    list.append(el("li", { class: `step ${ok ? "ok" : "err"}` },
      el("span", { class: "step-mark" }, ok ? "✓" : "✕"),
      el("div", {},
        el("div", { class: "step-title" }, KEE_STEP_LABELS[key] || key),
        el("div", { class: "muted small" }, keeStepSummary(key, p)))));
  });
  container.append(list);

  // 2. proposed (and now adopted) ontology
  const ex = r.extracted || {};
  const v = ex.validation || {};
  container.append(el("h4", { class: "kee-h" }, "Proposed ontology (adopted)"));
  container.append(el("div", { class: "extract-meta" },
    el("span", { class: "badge" }, `provider: ${ex.provider}`),
    ex.model ? el("span", { class: "badge" }, `model: ${ex.model}`) : null,
    el("span", { class: "badge" }, `embedder: ${ex.embedder}`),
    el("span", { class: `badge ${v.ok ? "ok" : "err"}` }, v.ok ? "valid" : "has errors")));
  (ex.process_outcomes || []).forEach((o) => {
    const prov = (ex.provenance && ex.provenance[o.id]) || [];
    container.append(el("div", { class: "proposed" },
      el("div", { class: "proposed-name" }, o.name),
      el("div", { class: "chips" }, (o.characteristics || []).map((c) => el("span", { class: "chip" }, c))),
      prov.length ? el("div", { class: "muted small" }, `from: ${prov.join(", ")}`) : null));
  });
  if (!(ex.process_outcomes || []).length) {
    container.append(el("p", { class: "muted small" },
      "No outcomes proposed — is the LLM (GRAP_LLM_BASE_URL / Ollama) reachable and the model pulled?"));
  }

  // 3. gaps after adopting (held-out evidence)
  container.append(el("h4", { class: "kee-h" },
    `Gaps vs. evidence (${(r.evidence_files || []).join(", ") || "—"})`));
  const gapsBox = el("div", {});
  container.append(gapsBox);
  if (r.gaps) renderGaps(r.gaps, gapsBox);
}

$("#btn-kee-demo").onclick = () =>
  run(async () => {
    toast("Running extraction demo (calling the model)…", "warn");
    const r = await post("/api/wizard/extract", { path: null, model: $("#kee-demo-model").value.trim() || null });
    if (r) renderKeeDemo(r, $("#kee-demo-result"));
    await refreshStatus();
    if (r && r.status !== "error")
      toast(`Extraction demo complete — ${(r.gaps && r.gaps.gaps_found) ?? 0} gaps`, "ok");
    return r;
  });

/* ---------- knowledge ---------- */
$("#btn-create-kg").onclick = () =>
  run(async () => {
    const r = await post("/api/kg/create", { path: $("#path-create-kg").value });
    await refreshStatus();
    return r;
  }, "Knowledge graph created");

$("#btn-ingest").onclick = () =>
  run(async () => {
    const r = await post("/api/ingest", { path: $("#path-ingest").value });
    await refreshStatus();
    if (r && r.status !== "error") toast(`Ingested ${r.nodes_added} nodes`, "ok");
    return r;
  });

function renderExtract(r, container) {
  clear(container);
  const v = r.validation || {};
  const okCls = v.ok ? "ok" : "err";
  container.append(el("div", { class: "extract-meta" },
    el("span", { class: "badge" }, `provider: ${r.provider}`),
    r.model ? el("span", { class: "badge" }, `model: ${r.model}`) : null,
    el("span", { class: "badge" }, `embedder: ${r.embedder}`),
    el("span", { class: "badge" }, `${r.chunks} chunks → ${r.fragments} fragments`),
    el("span", { class: `badge ${okCls}` }, v.ok ? "valid" : "has errors")));
  (v.warnings || []).forEach((w) => container.append(el("p", { class: "small muted" }, `⚠ ${w}`)));
  (v.errors || []).forEach((e) => container.append(el("p", { class: "small err-text" }, `✕ ${e}`)));

  const outcomes = r.process_outcomes || [];
  if (!outcomes.length) {
    container.append(el("p", { class: "muted small" },
      "No outcomes proposed. Is the LLM (GRAP_LLM_BASE_URL / Ollama) reachable?"));
  }
  outcomes.forEach((o) => {
    const prov = (r.provenance && r.provenance[o.id]) || [];
    container.append(el("div", { class: "proposed" },
      el("div", { class: "proposed-name" }, o.name),
      el("div", { class: "chips" },
        (o.characteristics || []).map((c) => el("span", { class: "chip" }, c))),
      prov.length ? el("div", { class: "muted small" }, `from: ${prov.join(", ")}`) : null));
  });

  // human gate: adopt the proposal
  container.append(el("div", { class: "gap-actions" },
    el("button", {
      class: "btn",
      onclick: () => run(async () => {
        const lr = await post("/api/load/ontology", { path: r.out_path });
        await refreshStatus();
        return lr;
      }, "Proposed ontology loaded as active"),
    }, "Load this ontology (adopt proposal)")));
  container.append(el("p", { class: "muted small" }, `candidate written to ${r.out_path}`));
}

$("#btn-extract").onclick = () =>
  run(async () => {
    const r = await post("/api/extract", {
      path: $("#path-extract").value,
      model: $("#model-extract").value.trim() || null,
    });
    if (r) renderExtract(r, $("#extract-result"));
    if (r && r.status !== "error")
      toast(`Proposed ${(r.process_outcomes || []).length} outcomes`, r.validation && r.validation.ok ? "ok" : "warn");
    return r;
  });

$("#btn-load-onto").onclick = () =>
  run(async () => { const r = await post("/api/load/ontology", { path: $("#path-onto").value }); await refreshStatus(); return r; }, "Ontology loaded");

$("#btn-load-pg").onclick = () =>
  run(async () => { const r = await post("/api/load/pg", { path: $("#path-pg").value }); await refreshStatus(); return r; }, "Procedure graph loaded");

$("#btn-load-kg").onclick = () =>
  run(async () => { const r = await post("/api/load/kg", { path: $("#path-kg").value }); await refreshStatus(); return r; }, "Knowledge graph loaded");

$("#btn-validate").onclick = () =>
  run(async () => {
    const r = await post("/api/validate/pg", { path: $("#path-validate").value });
    const box = clear($("#validate-result"));
    if (r) {
      const ok = r.ok;
      box.append(el("div", { class: `badge ${ok ? "ok" : "err"}` }, ok ? "valid" : "invalid"));
      (r.errors || []).forEach((e) => box.append(el("p", { class: "small err-text" }, `✕ ${e}`)));
      (r.warnings || []).forEach((w) => box.append(el("p", { class: "small muted" }, `⚠ ${w}`)));
    }
    return r;
  });

/* ---------- gaps ---------- */
$("#btn-detect-gaps").onclick = () =>
  run(async () => {
    const r = await post("/api/gaps/detect", { scope: $("#gap-scope").value });
    if (r) renderGaps(r, $("#gaps-list"), $("#gaps-run-meta"));
    await refreshStatus();
    return r;
  });

$("#btn-run-experiment").onclick = () =>
  run(async () => {
    const r = await post("/api/gaps/experiment", { scope: $("#gap-scope").value, hypothesis: "GUI experiment" });
    if (r) renderGaps(r, $("#gaps-list"), $("#gaps-run-meta"));
    return r;
  });

$("#btn-run-procedure").onclick = () =>
  run(() => post("/api/procedure/run", { scope: $("#gap-scope").value }), "Procedure executed");

/* ---------- lifecycle ---------- */
$("#btn-wizard-publish").onclick = () =>
  run(async () => {
    const r = await post("/api/wizard/publish-activate", { level: "draft", rationale: "ASPICE GUI wizard" });
    const box = clear($("#lifecycle-result"));
    if (r && r.activate) box.append(el("div", { class: "badge ok" }, `production: ${r.activate.activation_id || "set"}`));
    await refreshStatus();
    return r;
  }, "Published, promoted & activated");

$("#btn-publish").onclick = () =>
  run(() => post("/api/publish", { level: $("#publish-level").value, rationale: $("#publish-rationale").value || null }), "Published");

$("#btn-promote").onclick = () =>
  run(() => post("/api/promote", { version_hash: $("#promote-hash").value, rationale: $("#promote-rationale").value }), "Promoted");

$("#btn-list-versions").onclick = () =>
  run(async () => { const r = await api("/api/versions"); if (r) renderVersions(r.versions || []); return r; });

$("#btn-get-production").onclick = () =>
  run(async () => {
    const r = await api("/api/production");
    if (r) toast(r.active ? `production: ${r.activation_id}` : (r.message || "no production"), r.active ? "ok" : "warn");
    return r;
  });

/* ---------- runs ---------- */
async function loadRuns(kind) {
  const r = await run(() => api(kind ? `/api/runs?kind=${kind}` : "/api/runs"));
  if (r) renderRuns(r.runs || []);
}
$("#btn-runs-all").onclick = () => loadRuns(null);
$("#btn-runs-prod").onclick = () => loadRuns("production");
$("#btn-runs-exp").onclick = () => loadRuns("experiment");

$("#btn-query-events").onclick = () =>
  run(async () => {
    const type = $("#event-type").value.trim();
    const r = await post("/api/events/query", { event_type: type || null });
    if (r) renderEvents(r.events || []);
    return r;
  });

/* ---------- quality / audit ---------- */
$("#btn-quality").onclick = () =>
  run(async () => { const r = await api("/api/quality"); if (r) renderQuality(r); return r; });

$("#btn-audit").onclick = () =>
  run(async () => { const r = await api("/api/audit"); if (r) renderAudit(r.events || []); return r; });

/* ---------- settings ---------- */
$("#btn-set-workspace").onclick = () =>
  run(async () => { const r = await post("/api/workspace", { path: $("#workspace-path").value || null }); await refreshStatus(); return r; }, "Workspace applied");

$("#btn-set-weight").onclick = () =>
  run(() => post("/api/config/weight", {
    name: $("#weight-name").value,
    value: parseFloat($("#weight-value").value),
    rationale: $("#weight-rationale").value,
  }), "Weight set");

$("#btn-set-retention").onclick = () =>
  run(() => post("/api/config/retention", {
    key: $("#retention-key").value,
    days: parseInt($("#retention-days").value, 10),
    rationale: $("#retention-rationale").value,
  }), "Retention set");

$("#btn-enforce-retention").onclick = () =>
  run(async () => { const r = await post("/api/retention/enforce"); if (r && r.status !== "error") toast(`Purged ${r.runs_deleted} runs`, "ok"); return r; });

$("#btn-get-config").onclick = () =>
  run(async () => { const r = await api("/api/config"); if (r) renderConfig(r); return r; });

/* ---------- init ---------- */
(async function init() {
  try {
    const [health, ex] = await Promise.all([api("/api/health"), api("/api/examples")]);
    const root = health.repo_root ? health.repo_root.replace(/\\/g, "/") : ".";
    const ws = `${root}/aspice-gui/grap-workspace`;
    $("#workspace-path").value = ws;
    $("#statusbar-ws").textContent = ws;
    $("#path-create-kg").value = `${ws}/kg`;
    $("#path-ingest").value = ex.sample_knowledge || "";
    $("#path-extract").value = ex.sample_knowledge || "";
    // populate the model picker from the configured LLM endpoint (non-fatal)
    api("/api/models")
      .then((m) => {
        const dl = $("#model-options");
        if (dl && m.models) m.models.forEach((id) => dl.append(el("option", { value: id })));
      })
      .catch(() => {});
    $("#path-onto").value = ex.ontology || "";
    $("#path-pg").value = ex.procedure_graph || "";
    $("#path-validate").value = ex.procedure_graph || "";
    await refreshStatus();
  } catch (err) {
    toast(`Startup error: ${err}. Is the server running?`, "err");
  }
})();
