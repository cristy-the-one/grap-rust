//! Append-only JSONL audit log. Forward-compatible with the full Temporal
//! Graph (Task 03).

use std::collections::BTreeMap;
use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};

use super::LifecycleError;

/// A single auditable event.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct AuditEvent {
    /// Event time (unix millis).
    pub ts: u64,
    /// Event type, e.g. `version_published`, `production_flag_moved`.
    pub event_type: String,
    /// Optional actor (user/agent).
    pub actor: Option<String>,
    /// Subject identifier (a hash or activation id).
    pub subject: String,
    /// Arbitrary structured details.
    pub details: BTreeMap<String, String>,
    /// Optional rationale captured with the action.
    pub rationale: Option<String>,
}

/// Append-only log writing one JSON object per line.
#[derive(Debug, Clone)]
pub struct AuditLog {
    path: PathBuf,
}

impl AuditLog {
    /// Open (creating the `audit/` dir if needed) a log rooted at `base`.
    pub fn open(base: impl AsRef<Path>) -> Result<Self, LifecycleError> {
        let dir = base.as_ref().join("audit");
        fs::create_dir_all(&dir)?;
        Ok(AuditLog {
            path: dir.join("events.jsonl"),
        })
    }

    /// Append one event as a JSON line and flush.
    pub fn append(&self, event: &AuditEvent) -> Result<(), LifecycleError> {
        let mut line = serde_json::to_string(event)?;
        line.push('\n');
        let mut f = OpenOptions::new()
            .create(true)
            .append(true)
            .open(&self.path)?;
        f.write_all(line.as_bytes())?;
        f.flush()?;
        Ok(())
    }

    /// Read all events in order.
    pub fn read_all(&self) -> Result<Vec<AuditEvent>, LifecycleError> {
        if !self.path.exists() {
            return Ok(Vec::new());
        }
        let mut out = Vec::new();
        for line in fs::read_to_string(&self.path)?.lines() {
            if line.trim().is_empty() {
                continue;
            }
            out.push(serde_json::from_str(line)?);
        }
        Ok(out)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn ev(t: &str, subject: &str) -> AuditEvent {
        AuditEvent {
            ts: 1,
            event_type: t.to_string(),
            actor: None,
            subject: subject.to_string(),
            details: BTreeMap::new(),
            rationale: None,
        }
    }

    #[test]
    fn append_then_read_preserves_order() {
        let dir = tempfile::tempdir().unwrap();
        let log = AuditLog::open(dir.path()).unwrap();
        log.append(&ev("a", "1")).unwrap();
        log.append(&ev("b", "2")).unwrap();
        let all = log.read_all().unwrap();
        assert_eq!(all.len(), 2);
        assert_eq!(all[0].event_type, "a");
        assert_eq!(all[1].event_type, "b");
    }

    #[test]
    fn read_empty_log_is_empty() {
        let dir = tempfile::tempdir().unwrap();
        let log = AuditLog::open(dir.path()).unwrap();
        assert!(log.read_all().unwrap().is_empty());
    }
}
