//! The `Workspace` facade ties the version, publish, activation, and audit
//! stores to a single root directory and enforces the lifecycle rules.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use super::{
    now_millis, Activation, ActivationState, ActivationStore, AuditEvent, AuditLog, ChangeStatus,
    ContentHash, EventLink, LifecycleError, PublishLevel, PublishRecord, PublishStore,
    ReconstructionReport, RunKind, RunRecord, RunStatus, RunStore, Version, VersionKind,
    VersionStore,
};

/// A lifecycle workspace rooted at a directory.
#[derive(Debug, Clone)]
pub struct Workspace {
    root: PathBuf,
    versions: VersionStore,
    publishes: PublishStore,
    activations: ActivationStore,
    runs: RunStore,
    audit: AuditLog,
}

impl Workspace {
    /// Open (creating if needed) a workspace at `root`.
    pub fn open(root: impl AsRef<Path>) -> Result<Self, LifecycleError> {
        let root = root.as_ref().to_path_buf();
        Ok(Workspace {
            versions: VersionStore::open(root.join("versions"))?,
            publishes: PublishStore::open(root.join("published"))?,
            activations: ActivationStore::open(&root)?,
            runs: RunStore::open(root.join("runs"))?,
            audit: AuditLog::open(&root)?,
            root,
        })
    }

    /// The workspace root directory.
    pub fn root(&self) -> &Path {
        &self.root
    }

    /// Publish PG `content` at `level`. Promoted publishes require a rationale.
    pub fn publish_pg(
        &self,
        content: &[u8],
        level: PublishLevel,
        rationale: Option<String>,
        metadata: BTreeMap<String, String>,
    ) -> Result<Version, LifecycleError> {
        if level == PublishLevel::Promoted && rationale.as_deref().unwrap_or("").is_empty() {
            return Err(LifecycleError::MissingRationale);
        }
        let version = self.versions.put(VersionKind::Pg, content, metadata)?;
        self.publishes.put(&PublishRecord {
            hash: version.hash.clone(),
            level,
            rationale: rationale.clone(),
            ts: now_millis(),
        })?;
        self.audit.append(&AuditEvent {
            ts: now_millis(),
            event_type: "version_published".to_string(),
            actor: None,
            subject: version.hash.to_string(),
            details: BTreeMap::from([("level".to_string(), format!("{:?}", level))]),
            rationale,
            links: vec![],
        })?;
        Ok(version)
    }

    /// Publish a KG/ontology snapshot as an immutable `Ontology` version.
    /// Lighter than PG publishing: no Draft/Promoted record, just a versioned
    /// artifact plus an audit event.
    pub fn publish_ontology(
        &self,
        content: &[u8],
        metadata: BTreeMap<String, String>,
    ) -> Result<Version, LifecycleError> {
        let version = self
            .versions
            .put(VersionKind::Ontology, content, metadata)?;
        self.audit.append(&AuditEvent {
            ts: now_millis(),
            event_type: "version_published".to_string(),
            actor: None,
            subject: version.hash.to_string(),
            details: BTreeMap::from([("kind".to_string(), "ontology".to_string())]),
            rationale: None,
            links: vec![],
        })?;
        Ok(version)
    }

    /// Append an arbitrary audit event (used by higher-level/MCP capabilities
    /// that mutate state outside the core lifecycle operations).
    pub fn record_event(
        &self,
        event_type: &str,
        subject: &str,
        details: BTreeMap<String, String>,
        rationale: Option<String>,
    ) -> Result<(), LifecycleError> {
        self.audit.append(&AuditEvent {
            ts: now_millis(),
            event_type: event_type.to_string(),
            actor: None,
            subject: subject.to_string(),
            details,
            rationale,
            links: vec![],
        })
    }

    /// Promote a previously published version. Requires a non-empty rationale.
    pub fn promote(&self, hash: &ContentHash, rationale: String) -> Result<(), LifecycleError> {
        if rationale.is_empty() {
            return Err(LifecycleError::MissingRationale);
        }
        let mut record = self
            .publishes
            .get(hash)?
            .ok_or_else(|| LifecycleError::NotFound(hash.to_string()))?;
        record.level = PublishLevel::Promoted;
        record.rationale = Some(rationale.clone());
        record.ts = now_millis();
        self.publishes.put(&record)?;
        self.audit.append(&AuditEvent {
            ts: now_millis(),
            event_type: "version_promoted".to_string(),
            actor: None,
            subject: hash.to_string(),
            details: BTreeMap::new(),
            rationale: Some(rationale),
            links: vec![],
        })?;
        Ok(())
    }

    /// Create an activation for a promoted PG version with frozen weights.
    pub fn activate(
        &self,
        activation_id: String,
        pg_logic_id: String,
        pg_version: ContentHash,
        weights: BTreeMap<String, f64>,
        kg_version: Option<ContentHash>,
    ) -> Result<Activation, LifecycleError> {
        match self.publishes.get(&pg_version)? {
            Some(r) if r.level == PublishLevel::Promoted => {}
            _ => return Err(LifecycleError::NotPromoted(pg_version.to_string())),
        }
        let activation = Activation {
            activation_id: activation_id.clone(),
            pg_logic_id,
            pg_version,
            frozen_weights: weights,
            kg_version,
            created_at: now_millis(),
            state: ActivationState::Created,
            is_production: false,
        };
        self.activations.put(&activation)?;
        self.audit.append(&AuditEvent {
            ts: now_millis(),
            event_type: "activation_created".to_string(),
            actor: None,
            subject: activation_id,
            details: BTreeMap::new(),
            rationale: None,
            links: vec![],
        })?;
        Ok(activation)
    }

    /// Move the production flag to `activation_id`.
    pub fn set_production(&self, activation_id: &str) -> Result<Activation, LifecycleError> {
        let activation = self.activations.set_production(activation_id)?;
        self.audit.append(&AuditEvent {
            ts: now_millis(),
            event_type: "production_flag_moved".to_string(),
            actor: None,
            subject: activation_id.to_string(),
            details: BTreeMap::from([("pg_logic_id".to_string(), activation.pg_logic_id.clone())]),
            rationale: None,
            links: vec![],
        })?;
        Ok(activation)
    }

    /// Start a run, freezing the PG/KG versions for reproducibility.
    #[allow(clippy::too_many_arguments)]
    pub fn start_run(
        &self,
        run_id: String,
        kind: RunKind,
        activation_id: Option<String>,
        pg_version: Option<ContentHash>,
        kg_version: Option<ContentHash>,
        scope: String,
    ) -> Result<RunRecord, LifecycleError> {
        let run = RunRecord {
            run_id: run_id.clone(),
            kind,
            status: RunStatus::Running,
            started_at: now_millis(),
            completed_at: None,
            activation_id: activation_id.clone(),
            pg_version: pg_version.clone(),
            kg_version: kg_version.clone(),
            scope,
            gaps_found: 0,
            explanation_hashes: vec![],
            summary: BTreeMap::new(),
        };
        self.runs.put(&run)?;

        let mut links = vec![EventLink {
            relation: "produced".to_string(),
            kind: "run".to_string(),
            id: run_id.clone(),
        }];
        if let Some(a) = &activation_id {
            links.push(EventLink {
                relation: "under".to_string(),
                kind: "activation".to_string(),
                id: a.clone(),
            });
        }
        if let Some(h) = &pg_version {
            links.push(EventLink {
                relation: "used".to_string(),
                kind: "pg_version".to_string(),
                id: h.to_string(),
            });
        }
        if let Some(h) = &kg_version {
            links.push(EventLink {
                relation: "used".to_string(),
                kind: "kg_version".to_string(),
                id: h.to_string(),
            });
        }
        self.audit.append(&AuditEvent {
            ts: now_millis(),
            event_type: "run_started".to_string(),
            actor: None,
            subject: run_id,
            details: BTreeMap::new(),
            rationale: None,
            links,
        })?;
        Ok(run)
    }

    /// Store an immutable explanation record; returns its content-addressed version.
    pub fn record_explanation(
        &self,
        content: &[u8],
        metadata: BTreeMap<String, String>,
    ) -> Result<Version, LifecycleError> {
        self.versions
            .put(VersionKind::Explanation, content, metadata)
    }

    /// Complete a run, recording gaps + explanation references.
    pub fn complete_run(
        &self,
        run_id: &str,
        status: RunStatus,
        gaps_found: u32,
        explanation_hashes: Vec<ContentHash>,
        summary: BTreeMap<String, String>,
    ) -> Result<RunRecord, LifecycleError> {
        let mut run = self
            .runs
            .get(run_id)?
            .ok_or_else(|| LifecycleError::NotFound(run_id.to_string()))?;
        run.status = status;
        run.completed_at = Some(now_millis());
        run.gaps_found = gaps_found;
        run.explanation_hashes = explanation_hashes.clone();
        run.summary = summary;
        self.runs.put(&run)?;

        let mut links = vec![EventLink {
            relation: "completed".to_string(),
            kind: "run".to_string(),
            id: run_id.to_string(),
        }];
        for h in &explanation_hashes {
            links.push(EventLink {
                relation: "produced".to_string(),
                kind: "explanation".to_string(),
                id: h.to_string(),
            });
        }
        self.audit.append(&AuditEvent {
            ts: now_millis(),
            event_type: "run_completed".to_string(),
            actor: None,
            subject: run_id.to_string(),
            details: BTreeMap::from([("gaps_found".to_string(), gaps_found.to_string())]),
            rationale: None,
            links,
        })?;
        Ok(run)
    }

    /// Fetch a run record.
    pub fn get_run(&self, run_id: &str) -> Result<Option<RunRecord>, LifecycleError> {
        self.runs.get(run_id)
    }

    /// List runs, optionally filtered by kind.
    pub fn list_runs(&self, kind: Option<RunKind>) -> Result<Vec<RunRecord>, LifecycleError> {
        match kind {
            Some(k) => self.runs.list_by_kind(k),
            None => self.runs.list(),
        }
    }

    /// Filtered Temporal-Graph timeline query.
    pub fn temporal_query(
        &self,
        event_type: Option<&str>,
        since: Option<u64>,
        until: Option<u64>,
    ) -> Result<Vec<AuditEvent>, LifecycleError> {
        self.audit.query(event_type, since, until)
    }

    /// All events for a run.
    pub fn events_for_run(&self, run_id: &str) -> Result<Vec<AuditEvent>, LifecycleError> {
        self.audit.events_for_run(run_id)
    }

    /// All events linked to an entity.
    pub fn events_linked_to(
        &self,
        kind: &str,
        id: &str,
    ) -> Result<Vec<AuditEvent>, LifecycleError> {
        self.audit.events_linked_to(kind, id)
    }

    /// Reconstruct a run: resolve its frozen artifacts and report any that are
    /// missing (Task 09 — "reproduction incomplete" rather than silent failure).
    pub fn reconstruct(&self, run_id: &str) -> Result<ReconstructionReport, LifecycleError> {
        let run = self
            .runs
            .get(run_id)?
            .ok_or_else(|| LifecycleError::NotFound(run_id.to_string()))?;
        let mut missing = Vec::new();

        let pg_present = match &run.pg_version {
            Some(h) if self.versions.exists(h) => true,
            Some(h) => {
                missing.push(format!("pg_version {} missing", h));
                false
            }
            None => true,
        };
        let kg_present = match &run.kg_version {
            Some(h) if self.versions.exists(h) => true,
            Some(h) => {
                missing.push(format!("kg_version {} missing", h));
                false
            }
            None => true,
        };
        let explanations_total = run.explanation_hashes.len();
        let mut explanations_present = 0;
        for h in &run.explanation_hashes {
            if self.versions.exists(h) {
                explanations_present += 1;
            } else {
                missing.push(format!("explanation {} missing", h));
            }
        }

        Ok(ReconstructionReport {
            run_id: run_id.to_string(),
            complete: missing.is_empty() && pg_present && kg_present,
            pg_version_present: pg_present,
            kg_version_present: kg_present,
            explanations_present,
            explanations_total,
            missing,
        })
    }

    /// The current production activation for a logic, if any.
    pub fn current_production(
        &self,
        pg_logic_id: &str,
    ) -> Result<Option<Activation>, LifecycleError> {
        self.activations.production_for(pg_logic_id)
    }

    /// The last known good activation is, for the POC, the production one.
    pub fn last_known_good(&self, pg_logic_id: &str) -> Result<Option<Activation>, LifecycleError> {
        self.activations.production_for(pg_logic_id)
    }

    /// Detect whether `current` bytes differ from the production version.
    pub fn detect_change(
        &self,
        pg_logic_id: &str,
        current: &[u8],
    ) -> Result<ChangeStatus, LifecycleError> {
        let current_hash = ContentHash::of(current);
        let production_hash = self
            .activations
            .production_for(pg_logic_id)?
            .map(|a| a.pg_version);
        Ok(ChangeStatus {
            changed: production_hash.as_ref() != Some(&current_hash),
            current_hash,
            production_hash,
        })
    }

    /// Read the full audit log.
    pub fn read_audit(&self) -> Result<Vec<AuditEvent>, LifecycleError> {
        self.audit.read_all()
    }

    /// List versions (optionally filtered by kind).
    pub fn list_versions(&self, kind: Option<VersionKind>) -> Result<Vec<Version>, LifecycleError> {
        self.versions.list(kind)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn full_lifecycle_publish_promote_activate_set_production() {
        let dir = tempfile::tempdir().unwrap();
        let ws = Workspace::open(dir.path()).unwrap();

        let v = ws
            .publish_pg(b"pg yaml", PublishLevel::Draft, None, BTreeMap::new())
            .unwrap();

        // Cannot activate a draft.
        assert!(matches!(
            ws.activate(
                "act1".into(),
                "logic".into(),
                v.hash.clone(),
                BTreeMap::new(),
                None
            ),
            Err(LifecycleError::NotPromoted(_))
        ));

        ws.promote(&v.hash, "looks good".into()).unwrap();
        let act = ws
            .activate(
                "act1".into(),
                "logic".into(),
                v.hash.clone(),
                BTreeMap::from([("priority".into(), 0.8)]),
                None,
            )
            .unwrap();
        assert!(!act.is_production);

        let prod = ws.set_production("act1").unwrap();
        assert!(prod.is_production);
        assert_eq!(
            ws.current_production("logic")
                .unwrap()
                .unwrap()
                .activation_id,
            "act1"
        );

        // Audit captured the four mutations.
        let events: Vec<String> = ws
            .read_audit()
            .unwrap()
            .into_iter()
            .map(|e| e.event_type)
            .collect();
        assert!(events.contains(&"version_published".to_string()));
        assert!(events.contains(&"version_promoted".to_string()));
        assert!(events.contains(&"activation_created".to_string()));
        assert!(events.contains(&"production_flag_moved".to_string()));
    }

    #[test]
    fn publish_ontology_stores_an_ontology_version() {
        let dir = tempfile::tempdir().unwrap();
        let ws = Workspace::open(dir.path()).unwrap();
        let v = ws.publish_ontology(b"onto yaml", BTreeMap::new()).unwrap();
        let listed = ws.list_versions(Some(VersionKind::Ontology)).unwrap();
        assert_eq!(listed.len(), 1);
        assert_eq!(listed[0].hash, v.hash);
    }

    #[test]
    fn record_event_appends_to_audit() {
        let dir = tempfile::tempdir().unwrap();
        let ws = Workspace::open(dir.path()).unwrap();
        ws.record_event("kg_loaded", "subj", BTreeMap::new(), None)
            .unwrap();
        let events = ws.read_audit().unwrap();
        assert!(events
            .iter()
            .any(|e| e.event_type == "kg_loaded" && e.subject == "subj"));
    }

    #[test]
    fn run_lifecycle_and_reconstruct() {
        let dir = tempfile::tempdir().unwrap();
        let ws = Workspace::open(dir.path()).unwrap();

        let exp = ws
            .record_explanation(b"{\"gap\":1}", BTreeMap::new())
            .unwrap();
        ws.start_run(
            "run-1".into(),
            RunKind::Production,
            None,
            None,
            None,
            "all".into(),
        )
        .unwrap();
        let run = ws
            .complete_run(
                "run-1",
                RunStatus::Completed,
                1,
                vec![exp.hash.clone()],
                BTreeMap::new(),
            )
            .unwrap();
        assert_eq!(run.status, RunStatus::Completed);
        assert_eq!(run.gaps_found, 1);

        let report = ws.reconstruct("run-1").unwrap();
        assert!(report.complete);
        assert_eq!(report.explanations_present, 1);

        let events: Vec<String> = ws
            .events_for_run("run-1")
            .unwrap()
            .into_iter()
            .map(|e| e.event_type)
            .collect();
        assert!(events.contains(&"run_started".to_string()));
        assert!(events.contains(&"run_completed".to_string()));
    }

    #[test]
    fn reconstruct_reports_missing_explanation() {
        let dir = tempfile::tempdir().unwrap();
        let ws = Workspace::open(dir.path()).unwrap();
        ws.start_run(
            "run-2".into(),
            RunKind::Experiment,
            None,
            None,
            None,
            "all".into(),
        )
        .unwrap();
        let bogus = ContentHash::of(b"never stored");
        ws.complete_run(
            "run-2",
            RunStatus::Completed,
            0,
            vec![bogus],
            BTreeMap::new(),
        )
        .unwrap();
        let report = ws.reconstruct("run-2").unwrap();
        assert!(!report.complete);
        assert_eq!(report.explanations_present, 0);
        assert_eq!(report.explanations_total, 1);
        assert!(report.missing.iter().any(|m| m.contains("explanation")));
    }

    #[test]
    fn promote_requires_rationale() {
        let dir = tempfile::tempdir().unwrap();
        let ws = Workspace::open(dir.path()).unwrap();
        let v = ws
            .publish_pg(b"x", PublishLevel::Draft, None, BTreeMap::new())
            .unwrap();
        assert!(matches!(
            ws.promote(&v.hash, "".into()),
            Err(LifecycleError::MissingRationale)
        ));
    }

    #[test]
    fn detect_change_flags_drift_from_production() {
        let dir = tempfile::tempdir().unwrap();
        let ws = Workspace::open(dir.path()).unwrap();
        let v = ws
            .publish_pg(b"v1", PublishLevel::Draft, None, BTreeMap::new())
            .unwrap();
        ws.promote(&v.hash, "ok".into()).unwrap();
        ws.activate(
            "a".into(),
            "logic".into(),
            v.hash.clone(),
            BTreeMap::new(),
            None,
        )
        .unwrap();
        ws.set_production("a").unwrap();

        assert!(!ws.detect_change("logic", b"v1").unwrap().changed);
        let drift = ws.detect_change("logic", b"v2-edited").unwrap();
        assert!(drift.changed);
        assert_eq!(drift.production_hash, Some(v.hash));
    }
}
