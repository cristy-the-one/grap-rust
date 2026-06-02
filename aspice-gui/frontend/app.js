const $ = (sel) => document.querySelector(sel);
const output = $("#output");

function show(data) {
  output.textContent =
    typeof data === "string" ? data : JSON.stringify(data, null, 2);
}

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    const msg = body.detail || body.message || res.statusText;
    throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return body;
}

function post(path, payload) {
  return api(path, { method: "POST", body: JSON.stringify(payload ?? {}) });
}

async function loadExamples() {
  const ex = await api("/api/examples");
  $("#path-onto").value = ex.ontology;
  $("#path-pg").value = ex.procedure_graph;
  $("#path-validate").value = ex.procedure_graph;
  return ex;
}

async function refreshStatus() {
  const st = await api("/api/status");
  $("#status-preview").textContent = JSON.stringify(st, null, 2);
  return st;
}

document.querySelectorAll(".nav").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".nav").forEach((b) => b.classList.remove("active"));
    document.querySelectorAll(".panel").forEach((p) => p.classList.remove("active"));
    btn.classList.add("active");
    $(`#panel-${btn.dataset.panel}`).classList.add("active");
  });
});

$("#role-select").addEventListener("change", async (e) => {
  try {
    const r = await post("/api/role", { role: e.target.value });
    show(r);
  } catch (err) {
    show(String(err));
  }
});

async function run(fn) {
  try {
    const r = await fn();
    show(r);
    return r;
  } catch (err) {
    show(String(err));
    throw err;
  }
}

$("#btn-bootstrap").onclick = () =>
  run(async () => {
    const r = await post("/api/wizard/bootstrap");
    await refreshStatus();
    return r;
  });

$("#btn-refresh-status").onclick = () => run(refreshStatus);

$("#btn-detect-quick").onclick = () =>
  run(() => post("/api/gaps/detect", { scope: "all" }));

document.querySelectorAll("[data-action]").forEach((btn) => {
  btn.onclick = () => {
    const action = btn.dataset.action;
    const paths = {
      load_kg: $("#path-kg").value,
      load_ontology: $("#path-onto").value,
      load_pg: $("#path-pg").value,
      validate_pg: $("#path-validate").value,
    };
    const map = {
      load_kg: ["/api/load/kg", { path: paths.load_kg }],
      load_ontology: ["/api/load/ontology", { path: paths.load_ontology }],
      load_pg: ["/api/load/pg", { path: paths.load_pg }],
      validate_pg: ["/api/validate/pg", { path: paths.validate_pg }],
    };
    const [url, body] = map[action];
    run(() => post(url, body));
  };
});

$("#btn-wizard-publish").onclick = () =>
  run(() =>
    post("/api/wizard/publish-activate", {
      level: "draft",
      rationale: "ASPICE GUI wizard",
    })
  );

$("#btn-publish").onclick = () =>
  run(() =>
    post("/api/publish", {
      level: $("#publish-level").value,
      rationale: $("#publish-rationale").value || null,
    })
  );

$("#btn-promote").onclick = () =>
  run(() =>
    post("/api/promote", {
      version_hash: $("#promote-hash").value,
      rationale: $("#promote-rationale").value,
    })
  );

$("#btn-list-versions").onclick = () => run(() => api("/api/versions"));
$("#btn-get-production").onclick = () => run(() => api("/api/production"));

$("#btn-detect-gaps").onclick = () =>
  run(() => post("/api/gaps/detect", { scope: $("#gap-scope").value }));

$("#btn-run-experiment").onclick = () =>
  run(() =>
    post("/api/gaps/experiment", {
      scope: $("#gap-scope").value,
      hypothesis: "GUI experiment",
    })
  );

$("#btn-run-procedure").onclick = () =>
  run(() => post("/api/procedure/run", { scope: $("#gap-scope").value }));

$("#btn-explain").onclick = () =>
  run(() => post("/api/gaps/explain", { target: $("#gap-target").value }));

$("#btn-audit").onclick = () => run(() => api("/api/audit"));
$("#btn-runs-prod").onclick = () => run(() => api("/api/runs?kind=production"));
$("#btn-runs-exp").onclick = () => run(() => api("/api/runs?kind=experiment"));
$("#btn-quality").onclick = () => run(() => api("/api/quality"));

$("#btn-reconstruct").onclick = () => {
  const id = $("#run-id").value.trim();
  if (!id) return show("Enter a run ID");
  run(() => api(`/api/runs/${encodeURIComponent(id)}/reconstruct`));
};

$("#btn-set-workspace").onclick = () =>
  run(() => post("/api/workspace", { path: $("#workspace-path").value || null }));

$("#btn-set-weight").onclick = () =>
  run(() =>
    post("/api/config/weight", {
      name: $("#weight-name").value,
      value: parseFloat($("#weight-value").value),
      rationale: $("#weight-rationale").value,
    })
  );

$("#btn-set-retention").onclick = () =>
  run(() =>
    post("/api/config/retention", {
      key: $("#retention-key").value,
      days: parseInt($("#retention-days").value, 10),
      rationale: $("#retention-rationale").value,
    })
  );

$("#btn-enforce-retention").onclick = () => run(() => post("/api/retention/enforce"));
$("#btn-get-config").onclick = () => run(() => api("/api/config"));

(async function init() {
  try {
    const health = await api("/api/health");
    const ex = await loadExamples();
    $("#workspace-path").value = health.repo_root
      ? `${health.repo_root.replace(/\\/g, "/")}/aspice-gui/grap-workspace`
      : "./grap-workspace";
    const role = await api("/api/role");
    if (role.role) $("#role-select").value = role.role;
    await refreshStatus();
    show({ ready: true, examples: ex });
  } catch (err) {
    show(`Startup error: ${err}\n\nIs the server running?  python -m uvicorn backend.main:app --app-dir aspice-gui`);
  }
})();