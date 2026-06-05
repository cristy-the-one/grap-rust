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

const PHASE_LABELS = {
  prepare: "Initializing model + embedder…",
  reconcile: "Reconciling concepts (embedding & dedup)…",
  adopt: "Adopting the proposed ontology…",
  ingest_evidence: "Ingesting project evidence…",
  detect_gaps: "Detecting gaps…",
};

// Live progress panel driven by REAL stream events (chunk completions + token
// deltas), not a timer. The token counter only advances when the model emits.
function keeProgressUI(container) {
  const phase = el("div", { class: "kee-phase" }, "Starting…");
  const fill = el("div", { class: "meter-fill med", style: "width:0%" });
  const counts = el("div", { class: "muted small" }, "");
  const live = el("div", { class: "kee-live" },
    el("span", { class: "pulse" }), el("span", { class: "kee-live-text muted small" }, ""));
  container.append(el("div", { class: "kee-progress" },
    phase, el("div", { class: "meter" }, fill), counts, live));
  const t0 = Date.now();
  let n = 0, done = 0;
  return {
    on(e) {
      const secs = ((Date.now() - t0) / 1000).toFixed(0);
      if (e.type === "step") phase.textContent = "Knowledge graph ready";
      else if (e.type === "phase") phase.textContent = PHASE_LABELS[e.name] || e.name;
      else if (e.type === "extract_begin") {
        n = e.chunks;
        phase.textContent = `Extracting with ${e.model || e.provider}`;
        counts.textContent = `0 / ${n} chunks`;
      } else if (e.type === "chunk_start") {
        live.querySelector(".kee-live-text").textContent =
          `chunk ${e.i}/${e.n} · generating…`;
      } else if (e.type === "token") {
        // advances ONLY as real tokens arrive from the model
        live.querySelector(".kee-live-text").textContent =
          `chunk ${e.i}/${e.n} · ${e.tokens} tokens… (${secs}s)`;
      } else if (e.type === "chunk_done") {
        done = e.i;
        fill.style.width = `${Math.round((done / n) * 100)}%`;
        counts.textContent = `${done} / ${n} chunks · +${e.outcomes} outcome(s) from chunk ${e.i}`;
      } else if (e.type === "chunk_error") {
        live.querySelector(".kee-live-text").textContent =
          `chunk ${e.i} failed: ${e.message}`;
      }
    },
  };
}

$("#btn-kee-demo").onclick = async () => {
  const box = clear($("#kee-demo-result"));
  const prog = keeProgressUI(box);
  const btn = $("#btn-kee-demo");
  btn.disabled = true;
  try {
    const resp = await fetch("/api/wizard/extract/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: null, model: $("#kee-demo-model").value.trim() || null }),
    });
    if (!resp.ok || !resp.body) throw new Error(`stream failed: ${resp.status}`);
    const reader = resp.body.getReader();
    const dec = new TextDecoder();
    let buf = "", result = null, errored = null;
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let nl;
      while ((nl = buf.indexOf("\n")) >= 0) {
        const line = buf.slice(0, nl).trim();
        buf = buf.slice(nl + 1);
        if (!line) continue;
        let e;
        try { e = JSON.parse(line); } catch { continue; }
        showRaw(e);
        prog.on(e);
        if (e.type === "done") result = e.result;
        if (e.type === "error") errored = e.message;
      }
    }
    if (errored) { toast(errored, "err"); return; }
    if (result) {
      renderKeeDemo(result, clear($("#kee-demo-result")));
      await refreshStatus();
      toast(`Extraction demo complete — ${(result.gaps && result.gaps.gaps_found) ?? 0} gaps`, "ok");
    }
  } catch (err) {
    toast(String(err.message || err), "err");
  } finally {
    btn.disabled = false;
  }
};

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

  // proposed relations (knowledge-graph edges between concepts)
  const rels = r.relations || [];
  if (rels.length) {
    container.append(el("div", { class: "card-head" },
      el("h4", { class: "kee-h" }, `Proposed relations (${rels.length})`),
      el("button", {
        class: "btn btn-ghost small",
        onclick: () => run(async () => {
          const pr = await post("/api/relations/project", { relations: rels });
          await refreshStatus();
          if (pr && pr.status !== "error")
            toast(`Projected ${pr.edges_added} edges / ${pr.nodes_added} nodes ` +
                  `(KG now ${pr.node_count} nodes, ${pr.edge_count} edges)`, "ok");
          return pr;
        }),
      }, "Project as graph edges")));
    const list = el("div", { class: "relations" });
    rels.slice(0, 40).forEach((rel) => {
      list.append(el("div", { class: "relation" },
        el("span", { class: "rel-node" }, rel.from),
        el("span", { class: "rel-type" }, `—${rel.type}→`),
        el("span", { class: "rel-node" }, rel.to)));
    });
    container.append(list);
  }

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

$("#btn-extract").onclick = async () => {
  const box = clear($("#extract-result"));
  const prog = keeProgressUI(box);
  const btn = $("#btn-extract");
  btn.disabled = true;
  try {
    const mf = parseInt($("#max-files-extract").value, 10);
    const resp = await fetch("/api/extract/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        path: $("#path-extract").value,
        model: $("#model-extract").value.trim() || null,
        max_files: Number.isFinite(mf) && mf > 0 ? mf : null,
      }),
    });
    if (!resp.ok || !resp.body) throw new Error(`stream failed: ${resp.status}`);
    const reader = resp.body.getReader();
    const dec = new TextDecoder();
    let buf = "", extracted = null, errored = null;
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let nl;
      while ((nl = buf.indexOf("\n")) >= 0) {
        const line = buf.slice(0, nl).trim();
        buf = buf.slice(nl + 1);
        if (!line) continue;
        let e;
        try { e = JSON.parse(line); } catch { continue; }
        showRaw(e);
        prog.on(e);
        if (e.type === "extracted") extracted = e.extracted;
        if (e.type === "error") errored = e.message;
      }
    }
    if (errored) { toast(errored, "err"); return; }
    if (extracted) {
      renderExtract(extracted, clear($("#extract-result")));
      await refreshStatus();
      toast(`Proposed ${(extracted.process_outcomes || []).length} outcomes, ` +
            `${(extracted.relations || []).length} relations`,
            extracted.validation && extracted.validation.ok ? "ok" : "warn");
    }
  } catch (err) {
    toast(String(err.message || err), "err");
  } finally {
    btn.disabled = false;
  }
};

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

/* ---------- legal knowledge graph (SVG force-directed, self-contained) ---------- */
const SVGNS = "http://www.w3.org/2000/svg";
function svgEl(tag, attrs = {}) {
  const n = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
  return n;
}
function edgeColor(type) {
  if (type === "cites") return "#5b8def";       // citation network
  if (type === "mentions") return "#8b9bb8";     // entity ← act link
  return "#e8a838";                              // extracted relation
}
function nodeColor(kind) { return kind === "entity" ? "#3ecf8e" : "#e8a838"; }

// persistent, growable graph state (expand-on-click adds to it)
const GW = 880, GH = 620, GPAD = 30;
let legalGraph = { nodes: [], edges: [], types: [], expanded: new Set() };

function setLegalGraph(data) {
  legalGraph = {
    nodes: (data.nodes || []).map((n) => ({ ...n })),  // x,y assigned by layout
    edges: (data.edges || []).map((e) => ({ ...e })),
    types: data.edge_types || ["cites", "mentions"],
    expanded: new Set(),
  };
}

function _layout(nodes, edges, iters) {
  const idx = new Map(nodes.map((n, i) => [n.id, i]));
  const E = edges.filter((e) => idx.has(e.source) && idx.has(e.target))
    .map((e) => ({ s: idx.get(e.source), t: idx.get(e.target) }));
  const k = Math.sqrt((GW * GH) / Math.max(nodes.length, 1));
  let temp = GW / 10;
  for (let it = 0; it < iters; it++) {
    const disp = nodes.map(() => ({ x: 0, y: 0 }));
    for (let i = 0; i < nodes.length; i++) {
      for (let j = i + 1; j < nodes.length; j++) {
        let dx = nodes[i].x - nodes[j].x, dy = nodes[i].y - nodes[j].y;
        let d = Math.hypot(dx, dy) || 0.01;
        const f = (k * k) / d, ux = dx / d, uy = dy / d;
        disp[i].x += ux * f; disp[i].y += uy * f;
        disp[j].x -= ux * f; disp[j].y -= uy * f;
      }
    }
    for (const e of E) {
      let dx = nodes[e.s].x - nodes[e.t].x, dy = nodes[e.s].y - nodes[e.t].y;
      let d = Math.hypot(dx, dy) || 0.01;
      const f = (d * d) / k, ux = dx / d, uy = dy / d;
      disp[e.s].x -= ux * f; disp[e.s].y -= uy * f;
      disp[e.t].x += ux * f; disp[e.t].y += uy * f;
    }
    for (let i = 0; i < nodes.length; i++) {
      const d = Math.hypot(disp[i].x, disp[i].y) || 0.01;
      nodes[i].x += (disp[i].x / d) * Math.min(d, temp);
      nodes[i].y += (disp[i].y / d) * Math.min(d, temp);
      nodes[i].x = Math.max(GPAD, Math.min(GW - GPAD, nodes[i].x));
      nodes[i].y = Math.max(GPAD, Math.min(GH - GPAD, nodes[i].y));
    }
    temp *= 0.96;
  }
}

function layoutGraph(host, info, iters = 300) {
  host.innerHTML = "";
  const nodes = legalGraph.nodes;
  if (!nodes.length) {
    host.append(el("p", { class: "muted" },
      "No connected nodes — increase Acts, or enable extracted relations."));
    return;
  }
  for (const n of nodes) {                 // seed positions for new nodes only
    if (typeof n.x !== "number") {
      n.x = (n.seedX ?? GPAD + Math.random() * (GW - 2 * GPAD));
      n.y = (n.seedY ?? GPAD + Math.random() * (GH - 2 * GPAD));
    }
  }
  _layout(nodes, legalGraph.edges, iters);

  const byId = new Map(nodes.map((n) => [n.id, n]));
  const svg = svgEl("svg", { viewBox: `0 0 ${GW} ${GH}`, class: "graph-svg" });
  const gEdges = svgEl("g"), gNodes = svgEl("g");
  const lines = [];
  legalGraph.edges.forEach((e) => {
    const a = byId.get(e.source), b = byId.get(e.target);
    if (!a || !b) return;
    const ln = svgEl("line", {
      x1: a.x, y1: a.y, x2: b.x, y2: b.y, stroke: edgeColor(e.type),
      "stroke-width": e.type === "cites" ? 1.6 : 1.1, "stroke-opacity": 0.5,
    });
    gEdges.append(ln);
    lines.push({ e, ln });
  });
  nodes.forEach((n) => {
    const g = svgEl("g", { class: "gnode", style: "cursor:pointer" });
    const expanded = legalGraph.expanded.has(n.id);
    const c = svgEl("circle", {
      cx: n.x, cy: n.y, r: n.kind === "entity" ? 5 : 6.5,
      fill: nodeColor(n.kind), stroke: expanded ? "#e8edf5" : "#0c0f14",
      "stroke-width": expanded ? 2 : 1,
    });
    const title = svgEl("title");
    title.textContent = `${n.title || n.label} [${n.kind}] — click to expand`;
    g.append(c, title);
    g.onclick = () => {
      lines.forEach(({ e, ln }) => {
        const on = e.source === n.id || e.target === n.id;
        ln.setAttribute("stroke-opacity", on ? 0.95 : 0.1);
      });
      const inc = legalGraph.edges
        .filter((e) => e.source === n.id || e.target === n.id)
        .map((e) => {
          const o = byId.get(e.source === n.id ? e.target : e.source);
          return `${e.type} → ${o ? (o.title || o.label) : "?"}`;
        });
      clear(info).append(
        el("div", {}, el("strong", {}, n.title || n.label),
          el("span", { class: "badge", style: "margin-left:.5rem" }, n.kind)),
        el("div", { class: "muted small", style: "margin:.3rem 0" },
          `${inc.length} edge(s) · click again only expands once`),
        el("div", { class: "chips" }, inc.slice(0, 24).map((x) => el("span", { class: "chip" }, x))));
      expandNode(n, host, info);
    };
    gNodes.append(g);
  });
  svg.append(gEdges, gNodes);
  host.append(svg);
}

async function expandNode(node, host, info) {
  if (legalGraph.expanded.has(node.id)) return;     // already expanded
  legalGraph.expanded.add(node.id);
  const r = await run(() => post("/api/graph/neighbors",
    { node_id: node.id, types: legalGraph.types.length ? legalGraph.types : null }));
  if (!r || r.status === "error") return;
  const have = new Set(legalGraph.nodes.map((n) => n.id));
  let added = 0;
  for (const nb of r.neighbors || []) {
    if (!have.has(nb.id)) {
      have.add(nb.id);
      legalGraph.nodes.push({ ...nb, seedX: node.x + (Math.random() * 60 - 30),
                              seedY: node.y + (Math.random() * 60 - 30) });
      added++;
    }
  }
  const ek = new Set(legalGraph.edges.map((e) => `${e.source}|${e.target}|${e.type}`));
  let newEdges = 0;
  for (const e of r.edges || []) {
    const key = `${e.source}|${e.target}|${e.type}`;
    if (!ek.has(key)) { ek.add(key); legalGraph.edges.push(e); newEdges++; }
  }
  if (added || newEdges) {
    layoutGraph(host, info, 160);          // fewer iters: integrate, keep stable
    toast(`+${added} node(s), +${newEdges} edge(s)`, "ok");
  } else {
    toast("no further neighbors", "warn");
  }
}

function renderGraphLegend(host) {
  clear(host).append(
    el("span", { class: "leg" }, el("span", { class: "dot", style: "background:#e8a838" }), "act / entity"),
    el("span", { class: "leg" }, el("span", { class: "dot", style: "background:#3ecf8e" }), "entity"),
    el("span", { class: "leg" }, el("span", { class: "bar", style: "background:#5b8def" }), "cites"),
    el("span", { class: "leg" }, el("span", { class: "bar", style: "background:#8b9bb8" }), "mentions"),
    el("span", { class: "leg" }, el("span", { class: "bar", style: "background:#e8a838" }), "relation"));
}

$("#btn-legal-graph").onclick = () =>
  run(async () => {
    $("#lg-meta").textContent = "Building… (citations are fast; relations call the model)";
    renderGraphLegend($("#lg-legend"));
    const r = await post("/api/legal-graph/build", {
      path: $("#lg-path").value.trim() || null,
      max_files: parseInt($("#lg-max").value, 10) || 400,
      with_relations: $("#lg-relations").checked,
      model: $("#lg-model").value.trim() || null,
    });
    if (r && r.status !== "error") {
      const c = r.counts || {};
      $("#lg-meta").textContent =
        `${c.acts} acts · ${c.cites} citations · ${c.entities || 0} entities · ` +
        `${c.relations || 0} relations → showing ${c.shown_nodes} nodes / ${c.shown_edges} edges ` +
        `(KG: ${c.node_count} nodes, ${c.edge_count} edges) · click a node to expand`;
      setLegalGraph(r);
      layoutGraph($("#lg-graph"), $("#lg-info"));
      await refreshStatus();
    }
    return r;
  });

$("#btn-legal-graph-file").onclick = () =>
  run(async () => {
    const path = $("#lg-relfile").value.trim();
    if (!path) { toast("Enter the path to a relations JSON file", "warn"); return null; }
    $("#lg-meta").textContent = "Loading relations file…";
    renderGraphLegend($("#lg-legend"));
    const r = await post("/api/legal-graph/from-relations", {
      path, top: parseInt($("#lg-reltop").value, 10) || 200,
    });
    if (r && r.status !== "error") {
      const c = r.counts || {};
      $("#lg-meta").textContent =
        `${c.relations} relations · ${c.entities} entities → showing ` +
        `${c.shown_nodes} nodes / ${c.shown_edges} edges · click a node to expand`;
      setLegalGraph(r);
      layoutGraph($("#lg-graph"), $("#lg-info"));
      await refreshStatus();
    }
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
    $("#lg-relfile").value = `${root}/out/legal-ontology/relations.curated.json`;
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
