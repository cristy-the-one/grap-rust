//! Activations and their store, including the single-production-per-logic rule.

use std::collections::BTreeMap;
use std::fs;
use std::path::{Path, PathBuf};

use super::{ContentHash, LifecycleError};

/// Lifecycle state of an activation.
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub enum ActivationState {
    /// Created but never made production.
    Created,
    /// Currently the production activation for its logic.
    Active,
    /// Was production, replaced by a newer activation.
    Superseded,
}

/// A (PG version + frozen weights + KG/ontology reference) bound together.
///
/// Note: holds `f64` weights, so this derives `PartialEq` only (not `Eq`).
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct Activation {
    /// Unique activation identifier.
    pub activation_id: String,
    /// Stable identity of the PG logic (independent of version).
    pub pg_logic_id: String,
    /// The frozen PG version.
    pub pg_version: ContentHash,
    /// Frozen prioritization weights.
    pub frozen_weights: BTreeMap<String, f64>,
    /// Optional frozen KG/ontology snapshot reference.
    pub kg_version: Option<ContentHash>,
    /// Creation time (unix millis).
    pub created_at: u64,
    /// Current state.
    pub state: ActivationState,
    /// Whether this is the production activation for its logic.
    pub is_production: bool,
}

/// Filesystem store of activations plus a per-logic production pointer.
#[derive(Debug, Clone)]
pub struct ActivationStore {
    base: PathBuf,
}

#[derive(serde::Serialize, serde::Deserialize)]
struct ProductionPointer {
    activation_id: String,
}

impl ActivationStore {
    /// Open (creating if needed) the store rooted at `base`.
    /// Creates `activations/` and `production/` subdirectories.
    pub fn open(base: impl AsRef<Path>) -> Result<Self, LifecycleError> {
        let base = base.as_ref().to_path_buf();
        fs::create_dir_all(base.join("activations"))?;
        fs::create_dir_all(base.join("production"))?;
        Ok(ActivationStore { base })
    }

    fn act_path(&self, id: &str) -> PathBuf {
        self.base.join("activations").join(format!("{}.json", id))
    }

    fn prod_path(&self, pg_logic_id: &str) -> PathBuf {
        self.base
            .join("production")
            .join(format!("{}.json", pg_logic_id))
    }

    /// Write (or overwrite) an activation record.
    pub fn put(&self, act: &Activation) -> Result<(), LifecycleError> {
        fs::write(
            self.act_path(&act.activation_id),
            serde_json::to_vec_pretty(act)?,
        )?;
        Ok(())
    }

    /// Read an activation by id, if present.
    pub fn get(&self, id: &str) -> Result<Option<Activation>, LifecycleError> {
        let path = self.act_path(id);
        if !path.exists() {
            return Ok(None);
        }
        Ok(Some(serde_json::from_slice(&fs::read(path)?)?))
    }

    /// List all activations.
    pub fn list(&self) -> Result<Vec<Activation>, LifecycleError> {
        let mut out = Vec::new();
        for entry in fs::read_dir(self.base.join("activations"))? {
            let path = entry?.path();
            if path.extension().and_then(|e| e.to_str()) != Some("json") {
                continue;
            }
            out.push(serde_json::from_slice(&fs::read(&path)?)?);
        }
        Ok(out)
    }

    /// List activations for a specific PG logic.
    pub fn list_for_logic(&self, pg_logic_id: &str) -> Result<Vec<Activation>, LifecycleError> {
        Ok(self
            .list()?
            .into_iter()
            .filter(|a| a.pg_logic_id == pg_logic_id)
            .collect())
    }

    /// Return the production activation for a logic, if any.
    pub fn production_for(&self, pg_logic_id: &str) -> Result<Option<Activation>, LifecycleError> {
        let path = self.prod_path(pg_logic_id);
        if !path.exists() {
            return Ok(None);
        }
        let ptr: ProductionPointer = serde_json::from_slice(&fs::read(path)?)?;
        self.get(&ptr.activation_id)
    }

    /// Make `activation_id` the production activation for its logic.
    ///
    /// Clears the previous production flag (marking the prior one `Superseded`)
    /// and sets the new one to `Active` / `is_production = true`.
    pub fn set_production(&self, activation_id: &str) -> Result<Activation, LifecycleError> {
        let mut target = self
            .get(activation_id)?
            .ok_or_else(|| LifecycleError::NotFound(activation_id.to_string()))?;

        if let Some(mut prev) = self.production_for(&target.pg_logic_id)? {
            if prev.activation_id != target.activation_id {
                prev.is_production = false;
                prev.state = ActivationState::Superseded;
                self.put(&prev)?;
            }
        }

        target.is_production = true;
        target.state = ActivationState::Active;
        self.put(&target)?;

        let ptr = ProductionPointer {
            activation_id: target.activation_id.clone(),
        };
        fs::write(
            self.prod_path(&target.pg_logic_id),
            serde_json::to_vec_pretty(&ptr)?,
        )?;
        Ok(target)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn mk(id: &str, logic: &str) -> Activation {
        Activation {
            activation_id: id.to_string(),
            pg_logic_id: logic.to_string(),
            pg_version: ContentHash::of(id.as_bytes()),
            frozen_weights: BTreeMap::new(),
            kg_version: None,
            created_at: 0,
            state: ActivationState::Created,
            is_production: false,
        }
    }

    #[test]
    fn set_production_moves_flag_and_supersedes_previous() {
        let dir = tempfile::tempdir().unwrap();
        let store = ActivationStore::open(dir.path()).unwrap();
        store.put(&mk("a1", "logic")).unwrap();
        store.put(&mk("a2", "logic")).unwrap();

        store.set_production("a1").unwrap();
        assert!(store.get("a1").unwrap().unwrap().is_production);

        store.set_production("a2").unwrap();
        let a1 = store.get("a1").unwrap().unwrap();
        let a2 = store.get("a2").unwrap().unwrap();
        assert!(!a1.is_production);
        assert_eq!(a1.state, ActivationState::Superseded);
        assert!(a2.is_production);
        assert_eq!(
            store
                .production_for("logic")
                .unwrap()
                .unwrap()
                .activation_id,
            "a2"
        );
    }

    #[test]
    fn list_for_logic_filters() {
        let dir = tempfile::tempdir().unwrap();
        let store = ActivationStore::open(dir.path()).unwrap();
        store.put(&mk("a1", "logic-x")).unwrap();
        store.put(&mk("a2", "logic-y")).unwrap();
        assert_eq!(store.list_for_logic("logic-x").unwrap().len(), 1);
    }

    #[test]
    fn set_production_unknown_id_errors() {
        let dir = tempfile::tempdir().unwrap();
        let store = ActivationStore::open(dir.path()).unwrap();
        assert!(matches!(
            store.set_production("nope"),
            Err(LifecycleError::NotFound(_))
        ));
    }
}
