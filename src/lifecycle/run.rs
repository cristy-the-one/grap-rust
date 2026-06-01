//! Execution run records and their filesystem store.

use std::collections::BTreeMap;
use std::fs;
use std::path::{Path, PathBuf};

use super::{ContentHash, LifecycleError};

/// Whether a run is a production run or a quarantined experiment.
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub enum RunKind {
    /// A production run; counts toward quality / last-known-good views.
    Production,
    /// An experiment; excluded from production quality views (Task 07).
    Experiment,
}

/// Lifecycle status of a run.
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub enum RunStatus {
    /// Started, not yet finished.
    Running,
    /// Finished successfully.
    Completed,
    /// Finished with an error.
    Failed,
}

/// A medium-rich record of a single execution run.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct RunRecord {
    /// Unique run identifier.
    pub run_id: String,
    /// Production or experiment.
    pub kind: RunKind,
    /// Current status.
    pub status: RunStatus,
    /// Start time (unix millis).
    pub started_at: u64,
    /// Completion time (unix millis), once finished.
    pub completed_at: Option<u64>,
    /// The activation this run executed under, if any.
    pub activation_id: Option<String>,
    /// Frozen PG version (reproducibility).
    pub pg_version: Option<ContentHash>,
    /// Frozen KG/ontology version (reproducibility).
    pub kg_version: Option<ContentHash>,
    /// Execution scope label.
    pub scope: String,
    /// Number of gaps found.
    pub gaps_found: u32,
    /// Content hashes of explanation records produced by this run.
    pub explanation_hashes: Vec<ContentHash>,
    /// Free-form summary fields.
    pub summary: BTreeMap<String, String>,
}

/// Filesystem store of run records (one JSON file per run).
#[derive(Debug, Clone)]
pub struct RunStore {
    base: PathBuf,
}

impl RunStore {
    /// Open (creating if needed) the run store rooted at `base`.
    pub fn open(base: impl AsRef<Path>) -> Result<Self, LifecycleError> {
        let base = base.as_ref().to_path_buf();
        fs::create_dir_all(&base)?;
        Ok(RunStore { base })
    }

    fn path(&self, run_id: &str) -> PathBuf {
        self.base.join(format!("{}.json", run_id))
    }

    /// Write (or overwrite) a run record.
    pub fn put(&self, run: &RunRecord) -> Result<(), LifecycleError> {
        fs::write(self.path(&run.run_id), serde_json::to_vec_pretty(run)?)?;
        Ok(())
    }

    /// Read a run record by id, if present.
    pub fn get(&self, run_id: &str) -> Result<Option<RunRecord>, LifecycleError> {
        let path = self.path(run_id);
        if !path.exists() {
            return Ok(None);
        }
        Ok(Some(serde_json::from_slice(&fs::read(path)?)?))
    }

    /// List all run records.
    pub fn list(&self) -> Result<Vec<RunRecord>, LifecycleError> {
        let mut out = Vec::new();
        for entry in fs::read_dir(&self.base)? {
            let path = entry?.path();
            if path.extension().and_then(|e| e.to_str()) != Some("json") {
                continue;
            }
            out.push(serde_json::from_slice(&fs::read(&path)?)?);
        }
        Ok(out)
    }

    /// List run records of a given kind.
    pub fn list_by_kind(&self, kind: RunKind) -> Result<Vec<RunRecord>, LifecycleError> {
        Ok(self
            .list()?
            .into_iter()
            .filter(|r| r.kind == kind)
            .collect())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn mk(id: &str, kind: RunKind) -> RunRecord {
        RunRecord {
            run_id: id.to_string(),
            kind,
            status: RunStatus::Running,
            started_at: 1,
            completed_at: None,
            activation_id: None,
            pg_version: None,
            kg_version: None,
            scope: "all".to_string(),
            gaps_found: 0,
            explanation_hashes: vec![],
            summary: BTreeMap::new(),
        }
    }

    #[test]
    fn put_get_round_trip() {
        let dir = tempfile::tempdir().unwrap();
        let store = RunStore::open(dir.path()).unwrap();
        let r = mk("run-1", RunKind::Production);
        store.put(&r).unwrap();
        assert_eq!(store.get("run-1").unwrap().unwrap(), r);
        assert!(store.get("missing").unwrap().is_none());
    }

    #[test]
    fn list_by_kind_filters() {
        let dir = tempfile::tempdir().unwrap();
        let store = RunStore::open(dir.path()).unwrap();
        store.put(&mk("run-1", RunKind::Production)).unwrap();
        store.put(&mk("run-2", RunKind::Experiment)).unwrap();
        assert_eq!(store.list().unwrap().len(), 2);
        assert_eq!(store.list_by_kind(RunKind::Experiment).unwrap().len(), 1);
    }
}
