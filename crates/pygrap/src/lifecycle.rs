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
    /// Subject id (hash or activation id).
    pub subject: String,
    /// Optional rationale.
    pub rationale: Option<String>,
}

#[pymethods]
impl AuditEvent {
    fn __repr__(&self) -> String {
        format!(
            "AuditEvent(ts={}, type={}, subject={})",
            self.ts, self.event_type, self.subject
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
        }
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
}
