//! The `Workspace` facade ties the version, publish, activation, and audit
//! stores to a single root directory and enforces the lifecycle rules.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use super::{
    now_millis, Activation, ActivationState, ActivationStore, AuditEvent, AuditLog, ChangeStatus,
    ContentHash, LifecycleError, PublishLevel, PublishRecord, PublishStore, Version, VersionKind,
    VersionStore,
};

/// A lifecycle workspace rooted at a directory.
#[derive(Debug, Clone)]
pub struct Workspace {
    root: PathBuf,
    versions: VersionStore,
    publishes: PublishStore,
    activations: ActivationStore,
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
        })?;
        Ok(version)
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
        })?;
        Ok(activation)
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
