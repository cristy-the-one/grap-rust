//! Python bindings for the grap lifecycle subsystem.

use std::collections::BTreeMap;

use grap::lifecycle as lc;
use pyo3::prelude::*;
use pyo3::types::PyDict;

use crate::errors::GraphError;

fn to_py(err: grap::LifecycleError) -> PyErr {
    GraphError::new_err(err.to_string())
}

/// Python wrapper for a stored version.
#[pyclass(get_all)]
#[derive(Clone)]
pub struct Version {
    /// Content hash (hex).
    pub hash: String,
    /// "pg" or "ontology".
    pub kind: String,
    /// Creation time (unix millis).
    pub created_at: u64,
}

#[pymethods]
impl Version {
    fn __repr__(&self) -> String {
        format!(
            "Version(hash={}, kind={}, created_at={})",
            self.hash, self.kind, self.created_at
        )
    }
}

impl From<lc::Version> for Version {
    fn from(v: lc::Version) -> Self {
        let kind = match v.kind {
            lc::VersionKind::Pg => "pg",
            lc::VersionKind::Ontology => "ontology",
            lc::VersionKind::Explanation => "explanation",
            lc::VersionKind::Validation => "validation",
        };
        Version {
            hash: v.hash.to_string(),
            kind: kind.to_string(),
            created_at: v.created_at,
        }
    }
}

/// Python wrapper for an activation.
#[pyclass(get_all)]
#[derive(Clone)]
pub struct Activation {
    /// Activation id.
    pub activation_id: String,
    /// Stable PG logic id.
    pub pg_logic_id: String,
    /// Frozen PG version hash.
    pub pg_version: String,
    /// Optional frozen KG/ontology version hash.
    pub kg_version: Option<String>,
    /// Whether this is the production activation.
    pub is_production: bool,
    /// Frozen prioritization weights.
    pub frozen_weights: std::collections::BTreeMap<String, f64>,
}

#[pymethods]
impl Activation {
    fn __repr__(&self) -> String {
        format!(
            "Activation(id={}, logic={}, pg_version={}, is_production={})",
            self.activation_id, self.pg_logic_id, self.pg_version, self.is_production
        )
    }
}

impl From<lc::Activation> for Activation {
    fn from(a: lc::Activation) -> Self {
        Activation {
            activation_id: a.activation_id,
            pg_logic_id: a.pg_logic_id,
            pg_version: a.pg_version.to_string(),
            kg_version: a.kg_version.map(|h| h.to_string()),
            is_production: a.is_production,
            frozen_weights: a.frozen_weights,
        }
    }
}

/// Python wrapper for an audit event.
#[pyclass(get_all)]
#[derive(Clone)]
pub struct AuditEvent {
    /// Event time (unix millis).
    pub ts: u64,
    /// Event type.
    pub event_type: String,
    /// Subject id (hash or activation/run id).
    pub subject: String,
    /// Optional rationale.
    pub rationale: Option<String>,
    /// Typed links as (relation, kind, id) tuples.
    pub links: Vec<(String, String, String)>,
}

#[pymethods]
impl AuditEvent {
    fn __repr__(&self) -> String {
        format!(
            "AuditEvent(ts={}, type={}, subject={}, links={})",
            self.ts,
            self.event_type,
            self.subject,
            self.links.len()
        )
    }
}

impl From<lc::AuditEvent> for AuditEvent {
    fn from(e: lc::AuditEvent) -> Self {
        AuditEvent {
            ts: e.ts,
            event_type: e.event_type,
            subject: e.subject,
            rationale: e.rationale,
            links: e
                .links
                .into_iter()
                .map(|l| (l.relation, l.kind, l.id))
                .collect(),
        }
    }
}

/// Python wrapper for a run record.
#[pyclass(get_all)]
#[derive(Clone)]
pub struct RunRecord {
    /// Run id.
    pub run_id: String,
    /// "production" or "experiment".
    pub kind: String,
    /// "running", "completed", or "failed".
    pub status: String,
    /// Number of gaps found.
    pub gaps_found: u32,
    /// Start time (unix millis) — the chronological sort key of list_runs.
    pub started_at: u64,
    /// Completion time (unix millis), once finished.
    pub completed_at: Option<u64>,
    /// Frozen PG version hash, if any.
    pub pg_version: Option<String>,
    /// Frozen KG/ontology version hash, if any.
    pub kg_version: Option<String>,
    /// Explanation record hashes.
    pub explanation_hashes: Vec<String>,
}

#[pymethods]
impl RunRecord {
    fn __repr__(&self) -> String {
        format!(
            "RunRecord(id={}, kind={}, status={}, gaps_found={})",
            self.run_id, self.kind, self.status, self.gaps_found
        )
    }
}

impl From<lc::RunRecord> for RunRecord {
    fn from(r: lc::RunRecord) -> Self {
        let kind = match r.kind {
            lc::RunKind::Production => "production",
            lc::RunKind::Experiment => "experiment",
        };
        let status = match r.status {
            lc::RunStatus::Running => "running",
            lc::RunStatus::Completed => "completed",
            lc::RunStatus::Failed => "failed",
        };
        RunRecord {
            run_id: r.run_id,
            kind: kind.to_string(),
            status: status.to_string(),
            gaps_found: r.gaps_found,
            started_at: r.started_at,
            completed_at: r.completed_at,
            pg_version: r.pg_version.map(|h| h.to_string()),
            kg_version: r.kg_version.map(|h| h.to_string()),
            explanation_hashes: r
                .explanation_hashes
                .into_iter()
                .map(|h| h.to_string())
                .collect(),
        }
    }
}

/// Python wrapper for a reconstruction report.
#[pyclass(get_all)]
#[derive(Clone)]
pub struct ReconstructionReport {
    /// The run id.
    pub run_id: String,
    /// True when every referenced artifact is present.
    pub complete: bool,
    /// Whether the PG version is present.
    pub pg_version_present: bool,
    /// Whether the KG version is present.
    pub kg_version_present: bool,
    /// Explanations present.
    pub explanations_present: usize,
    /// Explanations referenced.
    pub explanations_total: usize,
    /// Human-readable missing-artifact descriptions.
    pub missing: Vec<String>,
}

#[pymethods]
impl ReconstructionReport {
    fn __repr__(&self) -> String {
        format!(
            "ReconstructionReport(run={}, complete={}, missing={})",
            self.run_id,
            self.complete,
            self.missing.len()
        )
    }
}

impl From<lc::ReconstructionReport> for ReconstructionReport {
    fn from(r: lc::ReconstructionReport) -> Self {
        ReconstructionReport {
            run_id: r.run_id,
            complete: r.complete,
            pg_version_present: r.pg_version_present,
            kg_version_present: r.kg_version_present,
            explanations_present: r.explanations_present,
            explanations_total: r.explanations_total,
            missing: r.missing,
        }
    }
}

/// Python wrapper for a retention sweep report.
#[pyclass(get_all)]
#[derive(Clone)]
pub struct RetentionReport {
    /// Number of run records deleted.
    pub runs_deleted: usize,
    /// Number of explanation records deleted.
    pub explanations_deleted: usize,
    /// Ids of the deleted runs.
    pub run_ids: Vec<String>,
}

#[pymethods]
impl RetentionReport {
    fn __repr__(&self) -> String {
        format!(
            "RetentionReport(runs_deleted={}, explanations_deleted={})",
            self.runs_deleted, self.explanations_deleted
        )
    }
}

impl From<lc::RetentionReport> for RetentionReport {
    fn from(r: lc::RetentionReport) -> Self {
        RetentionReport {
            runs_deleted: r.runs_deleted,
            explanations_deleted: r.explanations_deleted,
            run_ids: r.run_ids,
        }
    }
}

fn parse_run_kind(kind: &str) -> lc::RunKind {
    match kind {
        "experiment" => lc::RunKind::Experiment,
        _ => lc::RunKind::Production,
    }
}

fn parse_run_status(status: &str) -> lc::RunStatus {
    match status {
        "failed" => lc::RunStatus::Failed,
        "running" => lc::RunStatus::Running,
        _ => lc::RunStatus::Completed,
    }
}

fn weights_from_dict(weights: Option<&Bound<'_, PyDict>>) -> PyResult<BTreeMap<String, f64>> {
    let mut out = BTreeMap::new();
    if let Some(d) = weights {
        for (k, v) in d.iter() {
            out.insert(k.extract::<String>()?, v.extract::<f64>()?);
        }
    }
    Ok(out)
}

/// Python wrapper for a lifecycle workspace.
#[pyclass]
pub struct Workspace {
    inner: lc::Workspace,
}

#[pymethods]
impl Workspace {
    /// Open (creating if needed) a workspace at `root`.
    #[staticmethod]
    fn open(root: String) -> PyResult<Self> {
        Ok(Workspace {
            inner: lc::Workspace::open(root).map_err(to_py)?,
        })
    }

    /// Publish PG content. `level` is "draft" or "promoted".
    #[pyo3(signature = (content, level="draft", rationale=None, metadata=None))]
    fn publish_pg(
        &self,
        content: String,
        level: &str,
        rationale: Option<String>,
        metadata: Option<&Bound<'_, PyDict>>,
    ) -> PyResult<Version> {
        let level = match level {
            "promoted" => lc::PublishLevel::Promoted,
            _ => lc::PublishLevel::Draft,
        };
        let mut meta = BTreeMap::new();
        if let Some(d) = metadata {
            for (k, v) in d.iter() {
                meta.insert(k.extract::<String>()?, v.extract::<String>()?);
            }
        }
        let v = self
            .inner
            .publish_pg(content.as_bytes(), level, rationale, meta)
            .map_err(to_py)?;
        Ok(v.into())
    }

    /// Publish a KG/ontology snapshot as an immutable Ontology version.
    #[pyo3(signature = (content, metadata=None))]
    fn publish_ontology(
        &self,
        content: String,
        metadata: Option<&Bound<'_, PyDict>>,
    ) -> PyResult<Version> {
        let mut meta = BTreeMap::new();
        if let Some(d) = metadata {
            for (k, v) in d.iter() {
                meta.insert(k.extract::<String>()?, v.extract::<String>()?);
            }
        }
        let v = self
            .inner
            .publish_ontology(content.as_bytes(), meta)
            .map_err(to_py)?;
        Ok(v.into())
    }

    /// Append an arbitrary audit event.
    #[pyo3(signature = (event_type, subject, details=None, rationale=None))]
    fn record_event(
        &self,
        event_type: String,
        subject: String,
        details: Option<&Bound<'_, PyDict>>,
        rationale: Option<String>,
    ) -> PyResult<()> {
        let mut d = BTreeMap::new();
        if let Some(dict) = details {
            for (k, v) in dict.iter() {
                d.insert(k.extract::<String>()?, v.extract::<String>()?);
            }
        }
        self.inner
            .record_event(&event_type, &subject, d, rationale)
            .map_err(to_py)
    }

    /// Promote a published version (rationale required).
    fn promote(&self, version_hash: String, rationale: String) -> PyResult<()> {
        self.inner
            .promote(&grap::ContentHash(version_hash), rationale)
            .map_err(to_py)
    }

    /// Create an activation for a promoted version with frozen weights.
    #[pyo3(signature = (activation_id, pg_logic_id, pg_version, weights=None, kg_version=None))]
    fn activate(
        &self,
        activation_id: String,
        pg_logic_id: String,
        pg_version: String,
        weights: Option<&Bound<'_, PyDict>>,
        kg_version: Option<String>,
    ) -> PyResult<Activation> {
        let act = self
            .inner
            .activate(
                activation_id,
                pg_logic_id,
                grap::ContentHash(pg_version),
                weights_from_dict(weights)?,
                kg_version.map(grap::ContentHash),
            )
            .map_err(to_py)?;
        Ok(act.into())
    }

    /// Move the production flag to `activation_id`.
    fn set_production(&self, activation_id: String) -> PyResult<Activation> {
        Ok(self
            .inner
            .set_production(&activation_id)
            .map_err(to_py)?
            .into())
    }

    /// Current production activation for a logic, or None.
    fn current_production(&self, pg_logic_id: String) -> PyResult<Option<Activation>> {
        Ok(self
            .inner
            .current_production(&pg_logic_id)
            .map_err(to_py)?
            .map(Into::into))
    }

    /// Whether `current` content differs from the production version.
    fn detect_change(&self, pg_logic_id: String, current: String) -> PyResult<bool> {
        Ok(self
            .inner
            .detect_change(&pg_logic_id, current.as_bytes())
            .map_err(to_py)?
            .changed)
    }

    /// List PG versions.
    fn list_versions(&self) -> PyResult<Vec<Version>> {
        Ok(self
            .inner
            .list_versions(Some(grap::VersionKind::Pg))
            .map_err(to_py)?
            .into_iter()
            .map(Into::into)
            .collect())
    }

    /// Read the full audit log.
    fn read_audit(&self) -> PyResult<Vec<AuditEvent>> {
        Ok(self
            .inner
            .read_audit()
            .map_err(to_py)?
            .into_iter()
            .map(Into::into)
            .collect())
    }

    /// Start a run; freezes the given versions.
    #[pyo3(signature = (run_id, kind="production", activation_id=None, pg_version=None, kg_version=None, scope="all"))]
    fn start_run(
        &self,
        run_id: String,
        kind: &str,
        activation_id: Option<String>,
        pg_version: Option<String>,
        kg_version: Option<String>,
        scope: &str,
    ) -> PyResult<RunRecord> {
        let run = self
            .inner
            .start_run(
                run_id,
                parse_run_kind(kind),
                activation_id,
                pg_version.map(grap::ContentHash),
                kg_version.map(grap::ContentHash),
                scope.to_string(),
            )
            .map_err(to_py)?;
        Ok(run.into())
    }

    /// Store an immutable explanation record; returns its hash via Version.
    #[pyo3(signature = (content, metadata=None))]
    fn record_explanation(
        &self,
        content: String,
        metadata: Option<&Bound<'_, PyDict>>,
    ) -> PyResult<Version> {
        let mut meta = BTreeMap::new();
        if let Some(d) = metadata {
            for (k, v) in d.iter() {
                meta.insert(k.extract::<String>()?, v.extract::<String>()?);
            }
        }
        let v = self
            .inner
            .record_explanation(content.as_bytes(), meta)
            .map_err(to_py)?;
        Ok(v.into())
    }

    /// Store a validation report for a PG version (content-addressed + linked event).
    #[pyo3(signature = (pg_hash, content, ok, metadata=None))]
    fn record_validation(
        &self,
        pg_hash: &str,
        content: String,
        ok: bool,
        metadata: Option<&Bound<'_, PyDict>>,
    ) -> PyResult<Version> {
        let mut meta = BTreeMap::new();
        if let Some(d) = metadata {
            for (k, v) in d.iter() {
                meta.insert(k.extract::<String>()?, v.extract::<String>()?);
            }
        }
        let v = self
            .inner
            .record_validation(pg_hash, content.as_bytes(), ok, meta)
            .map_err(to_py)?;
        Ok(v.into())
    }

    /// Fetch the text content of any stored record by hash, or None.
    fn get_record(&self, hash: String) -> PyResult<Option<String>> {
        Ok(self
            .inner
            .get_record(&grap::ContentHash(hash))
            .map_err(to_py)?
            .map(|bytes| String::from_utf8_lossy(&bytes).into_owned()))
    }

    /// Complete a run.
    #[pyo3(signature = (run_id, status="completed", gaps_found=0, explanation_hashes=None, summary=None))]
    fn complete_run(
        &self,
        run_id: String,
        status: &str,
        gaps_found: u32,
        explanation_hashes: Option<Vec<String>>,
        summary: Option<&Bound<'_, PyDict>>,
    ) -> PyResult<RunRecord> {
        let hashes = explanation_hashes
            .unwrap_or_default()
            .into_iter()
            .map(grap::ContentHash)
            .collect();
        let mut s = BTreeMap::new();
        if let Some(d) = summary {
            for (k, v) in d.iter() {
                s.insert(k.extract::<String>()?, v.extract::<String>()?);
            }
        }
        let run = self
            .inner
            .complete_run(&run_id, parse_run_status(status), gaps_found, hashes, s)
            .map_err(to_py)?;
        Ok(run.into())
    }

    /// Get a run record.
    fn get_run(&self, run_id: String) -> PyResult<Option<RunRecord>> {
        Ok(self.inner.get_run(&run_id).map_err(to_py)?.map(Into::into))
    }

    /// List runs, optionally filtered by kind ("production"/"experiment").
    #[pyo3(signature = (kind=None))]
    fn list_runs(&self, kind: Option<String>) -> PyResult<Vec<RunRecord>> {
        let k = kind.as_deref().map(parse_run_kind);
        Ok(self
            .inner
            .list_runs(k)
            .map_err(to_py)?
            .into_iter()
            .map(Into::into)
            .collect())
    }

    /// Reconstruct a run, reporting any missing artifacts.
    fn reconstruct(&self, run_id: String) -> PyResult<ReconstructionReport> {
        Ok(self.inner.reconstruct(&run_id).map_err(to_py)?.into())
    }

    /// Purge runs older than their kind's cutoff (+ orphaned explanations).
    fn enforce_retention(
        &self,
        production_cutoff_ms: u64,
        experiment_cutoff_ms: u64,
    ) -> PyResult<RetentionReport> {
        Ok(self
            .inner
            .enforce_retention(production_cutoff_ms, experiment_cutoff_ms)
            .map_err(to_py)?
            .into())
    }

    /// Filtered timeline query.
    #[pyo3(signature = (event_type=None, since=None, until=None))]
    fn query_events(
        &self,
        event_type: Option<String>,
        since: Option<u64>,
        until: Option<u64>,
    ) -> PyResult<Vec<AuditEvent>> {
        Ok(self
            .inner
            .temporal_query(event_type.as_deref(), since, until)
            .map_err(to_py)?
            .into_iter()
            .map(Into::into)
            .collect())
    }

    /// All events for a run.
    fn events_for_run(&self, run_id: String) -> PyResult<Vec<AuditEvent>> {
        Ok(self
            .inner
            .events_for_run(&run_id)
            .map_err(to_py)?
            .into_iter()
            .map(Into::into)
            .collect())
    }

    /// All events linked to an entity (kind + id).
    fn events_linked_to(&self, kind: String, id: String) -> PyResult<Vec<AuditEvent>> {
        Ok(self
            .inner
            .events_linked_to(&kind, &id)
            .map_err(to_py)?
            .into_iter()
            .map(Into::into)
            .collect())
    }
}
