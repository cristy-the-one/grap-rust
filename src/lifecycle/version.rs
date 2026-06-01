//! Immutable, content-addressed versions and their filesystem store.

use std::collections::BTreeMap;
use std::fs;
use std::path::{Path, PathBuf};

use super::{ContentHash, LifecycleError};

/// What a version represents.
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub enum VersionKind {
    /// A Procedure Graph source file.
    Pg,
    /// A KG/ontology snapshot.
    Ontology,
    /// An immutable explanation record (raw signals + rendered snapshot).
    Explanation,
    /// An immutable validation report for a PG version.
    Validation,
}

/// Metadata for an immutable, content-addressed version.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct Version {
    /// Content hash of the stored bytes.
    pub hash: ContentHash,
    /// What kind of artifact this version is.
    pub kind: VersionKind,
    /// Creation time (unix millis).
    pub created_at: u64,
    /// Free-form metadata (e.g. name, version string).
    pub metadata: BTreeMap<String, String>,
}

/// Filesystem-backed, content-addressed version store.
#[derive(Debug, Clone)]
pub struct VersionStore {
    base: PathBuf,
}

impl VersionStore {
    /// Open (creating if needed) a version store rooted at `base`.
    pub fn open(base: impl AsRef<Path>) -> Result<Self, LifecycleError> {
        let base = base.as_ref().to_path_buf();
        fs::create_dir_all(base.join("objects"))?;
        fs::create_dir_all(base.join("meta"))?;
        Ok(VersionStore { base })
    }

    fn object_path(&self, hash: &ContentHash) -> PathBuf {
        self.base.join("objects").join(hash.as_str())
    }

    fn meta_path(&self, hash: &ContentHash) -> PathBuf {
        self.base
            .join("meta")
            .join(format!("{}.json", hash.as_str()))
    }

    /// Store `content`, returning the resulting [`Version`]. Idempotent:
    /// re-storing identical bytes does not duplicate or overwrite the blob.
    pub fn put(
        &self,
        kind: VersionKind,
        content: &[u8],
        metadata: BTreeMap<String, String>,
    ) -> Result<Version, LifecycleError> {
        let hash = ContentHash::of(content);
        let version = Version {
            hash: hash.clone(),
            kind,
            created_at: super::now_millis(),
            metadata,
        };
        if !self.object_path(&hash).exists() {
            fs::write(self.object_path(&hash), content)?;
            fs::write(self.meta_path(&hash), serde_json::to_vec_pretty(&version)?)?;
        }
        Ok(version)
    }

    /// Returns `true` if a blob with this hash exists.
    pub fn exists(&self, hash: &ContentHash) -> bool {
        self.object_path(hash).exists()
    }

    /// Fetch a version's metadata and raw bytes, if present.
    pub fn get(&self, hash: &ContentHash) -> Result<Option<(Version, Vec<u8>)>, LifecycleError> {
        if !self.exists(hash) {
            return Ok(None);
        }
        let meta: Version = serde_json::from_slice(&fs::read(self.meta_path(hash))?)?;
        let bytes = fs::read(self.object_path(hash))?;
        Ok(Some((meta, bytes)))
    }

    /// List all versions, optionally filtered by kind.
    pub fn list(&self, kind: Option<VersionKind>) -> Result<Vec<Version>, LifecycleError> {
        let mut out = Vec::new();
        for entry in fs::read_dir(self.base.join("meta"))? {
            let path = entry?.path();
            if path.extension().and_then(|e| e.to_str()) != Some("json") {
                continue;
            }
            let v: Version = serde_json::from_slice(&fs::read(&path)?)?;
            if kind.is_none() || kind == Some(v.kind) {
                out.push(v);
            }
        }
        Ok(out)
    }

    /// Delete a version's blob + metadata. Returns `true` if the blob existed.
    pub fn delete(&self, hash: &ContentHash) -> Result<bool, LifecycleError> {
        let obj = self.object_path(hash);
        if !obj.exists() {
            return Ok(false);
        }
        fs::remove_file(obj)?;
        let meta = self.meta_path(hash);
        if meta.exists() {
            fs::remove_file(meta)?;
        }
        Ok(true)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tmp() -> tempfile::TempDir {
        tempfile::tempdir().unwrap()
    }

    #[test]
    fn put_then_get_round_trips() {
        let dir = tmp();
        let store = VersionStore::open(dir.path()).unwrap();
        let v = store
            .put(VersionKind::Pg, b"content", BTreeMap::new())
            .unwrap();
        let (meta, bytes) = store.get(&v.hash).unwrap().unwrap();
        assert_eq!(meta.kind, VersionKind::Pg);
        assert_eq!(bytes, b"content");
    }

    #[test]
    fn put_is_content_addressed_and_idempotent() {
        let dir = tmp();
        let store = VersionStore::open(dir.path()).unwrap();
        let a = store
            .put(VersionKind::Pg, b"same", BTreeMap::new())
            .unwrap();
        let b = store
            .put(VersionKind::Pg, b"same", BTreeMap::new())
            .unwrap();
        assert_eq!(a.hash, b.hash);
        assert_eq!(store.list(None).unwrap().len(), 1);
    }

    #[test]
    fn explanation_kind_round_trips() {
        let dir = tmp();
        let store = VersionStore::open(dir.path()).unwrap();
        let v = store
            .put(VersionKind::Explanation, b"{\"x\":1}", BTreeMap::new())
            .unwrap();
        let (meta, bytes) = store.get(&v.hash).unwrap().unwrap();
        assert_eq!(meta.kind, VersionKind::Explanation);
        assert_eq!(bytes, b"{\"x\":1}");
        assert_eq!(store.list(Some(VersionKind::Explanation)).unwrap().len(), 1);
    }

    #[test]
    fn list_filters_by_kind() {
        let dir = tmp();
        let store = VersionStore::open(dir.path()).unwrap();
        store.put(VersionKind::Pg, b"pg", BTreeMap::new()).unwrap();
        store
            .put(VersionKind::Ontology, b"onto", BTreeMap::new())
            .unwrap();
        assert_eq!(store.list(Some(VersionKind::Pg)).unwrap().len(), 1);
        assert_eq!(store.list(None).unwrap().len(), 2);
    }
}
