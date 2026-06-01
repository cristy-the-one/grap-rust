//! Append-only JSONL audit log. Forward-compatible with the full Temporal
//! Graph (Task 03).

use std::collections::BTreeMap;
use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};

use super::LifecycleError;

/// A typed link from an event to an entity (run, version, KG node, PG step, ...).
/// Together these turn the append-only log into a relational event graph.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct EventLink {
    /// The relationship name, e.g. "produced", "used", "touched".
    pub relation: String,
    /// The target entity kind, e.g. "run", "pg_version", "kg_node".
    pub kind: String,
    /// The target entity id (a run id, hash, node id, ...).
    pub id: String,
}

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
    /// Typed links to related entities (relational event graph).
    #[serde(default)]
    pub links: Vec<EventLink>,
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

    /// Filtered timeline query: by event type and/or time window.
    pub fn query(
        &self,
        event_type: Option<&str>,
        since: Option<u64>,
        until: Option<u64>,
    ) -> Result<Vec<AuditEvent>, LifecycleError> {
        Ok(self
            .read_all()?
            .into_iter()
            .filter(|e| match event_type {
                Some(t) => e.event_type == t,
                None => true,
            })
            .filter(|e| match since {
                Some(s) => e.ts >= s,
                None => true,
            })
            .filter(|e| match until {
                Some(u) => e.ts <= u,
                None => true,
            })
            .collect())
    }

    /// All events linked to (or whose subject is) a run.
    pub fn events_for_run(&self, run_id: &str) -> Result<Vec<AuditEvent>, LifecycleError> {
        Ok(self
            .read_all()?
            .into_iter()
            .filter(|e| {
                e.subject == run_id || e.links.iter().any(|l| l.kind == "run" && l.id == run_id)
            })
            .collect())
    }

    /// All events carrying a link to the given entity (kind + id).
    pub fn events_linked_to(
        &self,
        kind: &str,
        id: &str,
    ) -> Result<Vec<AuditEvent>, LifecycleError> {
        Ok(self
            .read_all()?
            .into_iter()
            .filter(|e| e.links.iter().any(|l| l.kind == kind && l.id == id))
            .collect())
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
            links: vec![],
        }
    }

    #[test]
    fn old_event_without_links_deserializes() {
        let json =
            r#"{"ts":1,"event_type":"x","actor":null,"subject":"s","details":{},"rationale":null}"#;
        let e: AuditEvent = serde_json::from_str(json).unwrap();
        assert!(e.links.is_empty());
    }

    #[test]
    fn events_for_run_and_linked_to() {
        let dir = tempfile::tempdir().unwrap();
        let log = AuditLog::open(dir.path()).unwrap();
        let mut e = ev("run_started", "run-1");
        e.links = vec![EventLink {
            relation: "used".into(),
            kind: "pg_version".into(),
            id: "h1".into(),
        }];
        log.append(&e).unwrap();
        log.append(&ev("other", "x")).unwrap();
        assert_eq!(log.events_for_run("run-1").unwrap().len(), 1);
        assert_eq!(log.events_linked_to("pg_version", "h1").unwrap().len(), 1);
        assert_eq!(log.query(Some("other"), None, None).unwrap().len(), 1);
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
