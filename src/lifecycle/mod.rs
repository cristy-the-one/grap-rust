//! Versioning, activation, and lifecycle for Procedure Graphs and KG/ontology
//! snapshots, backed by a filesystem workspace store with an append-only audit
//! log.

mod activation;
mod audit;
mod hash;
mod publish;
mod run;
mod version;
mod workspace;

pub use activation::{Activation, ActivationState, ActivationStore};
pub use audit::{AuditEvent, AuditLog, EventLink};
pub use hash::ContentHash;
pub use publish::{PublishLevel, PublishRecord, PublishStore};
pub use run::{RunKind, RunRecord, RunStatus, RunStore};
pub use version::{Version, VersionKind, VersionStore};
pub use workspace::Workspace;

use std::time::{SystemTime, UNIX_EPOCH};

/// Current unix time in milliseconds.
pub(crate) fn now_millis() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis() as u64)
        .unwrap_or(0)
}

/// Result of comparing current PG bytes against the production activation.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ChangeStatus {
    /// True when the current content hash differs from the production version.
    pub changed: bool,
    /// Hash of the bytes supplied to `detect_change`.
    pub current_hash: ContentHash,
    /// Hash frozen in the current production activation, if any.
    pub production_hash: Option<ContentHash>,
}

/// Errors produced by the lifecycle subsystem.
#[derive(thiserror::Error, Debug)]
pub enum LifecycleError {
    /// A requested version, activation, or record was not found.
    #[error("not found: {0}")]
    NotFound(String),

    /// Activation was attempted against a version that is not promoted.
    #[error("version {0} is not promoted")]
    NotPromoted(String),

    /// A required rationale was missing (e.g. promoting a version).
    #[error("a rationale is required for this operation")]
    MissingRationale,

    /// Filesystem I/O failure.
    #[error("I/O error: {0}")]
    Io(String),

    /// (De)serialization failure.
    #[error("serialization error: {0}")]
    Serde(String),
}

impl From<std::io::Error> for LifecycleError {
    fn from(e: std::io::Error) -> Self {
        LifecycleError::Io(e.to_string())
    }
}

impl From<serde_json::Error> for LifecycleError {
    fn from(e: serde_json::Error) -> Self {
        LifecycleError::Serde(e.to_string())
    }
}
