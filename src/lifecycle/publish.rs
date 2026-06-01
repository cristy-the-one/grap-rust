//! Publish records and the Draft -> Promoted state machine.

use std::fs;
use std::path::{Path, PathBuf};

use super::{ContentHash, LifecycleError};

/// Publish level for a version.
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub enum PublishLevel {
    /// Low-friction working state.
    Draft,
    /// Higher-bar state; required before activation.
    Promoted,
}

/// A record of a version having been published at some level.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct PublishRecord {
    /// The version this record refers to.
    pub hash: ContentHash,
    /// Current publish level.
    pub level: PublishLevel,
    /// Rationale (required when promoting).
    pub rationale: Option<String>,
    /// Last update time (unix millis).
    pub ts: u64,
}

/// Filesystem store of publish records, one JSON file per version hash.
#[derive(Debug, Clone)]
pub struct PublishStore {
    base: PathBuf,
}

impl PublishStore {
    /// Open (creating if needed) the publish store rooted at `base`.
    pub fn open(base: impl AsRef<Path>) -> Result<Self, LifecycleError> {
        let base = base.as_ref().to_path_buf();
        fs::create_dir_all(&base)?;
        Ok(PublishStore { base })
    }

    fn path(&self, hash: &ContentHash) -> PathBuf {
        self.base.join(format!("{}.json", hash.as_str()))
    }

    /// Write (or overwrite) a publish record.
    pub fn put(&self, record: &PublishRecord) -> Result<(), LifecycleError> {
        fs::write(self.path(&record.hash), serde_json::to_vec_pretty(record)?)?;
        Ok(())
    }

    /// Read a publish record, if present.
    pub fn get(&self, hash: &ContentHash) -> Result<Option<PublishRecord>, LifecycleError> {
        let path = self.path(hash);
        if !path.exists() {
            return Ok(None);
        }
        Ok(Some(serde_json::from_slice(&fs::read(path)?)?))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn put_get_round_trip() {
        let dir = tempfile::tempdir().unwrap();
        let store = PublishStore::open(dir.path()).unwrap();
        let rec = PublishRecord {
            hash: ContentHash::of(b"x"),
            level: PublishLevel::Draft,
            rationale: None,
            ts: 1,
        };
        store.put(&rec).unwrap();
        assert_eq!(store.get(&rec.hash).unwrap().unwrap(), rec);
    }

    #[test]
    fn missing_record_is_none() {
        let dir = tempfile::tempdir().unwrap();
        let store = PublishStore::open(dir.path()).unwrap();
        assert!(store.get(&ContentHash::of(b"nope")).unwrap().is_none());
    }
}
