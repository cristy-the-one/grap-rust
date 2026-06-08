//! Content-addressed hashing (SHA-256, hex-encoded).

use sha2::{Digest, Sha256};

/// A content-addressed identifier: the lowercase hex SHA-256 of some bytes.
#[derive(Debug, Clone, PartialEq, Eq, Hash, serde::Serialize, serde::Deserialize)]
pub struct ContentHash(pub String);

/// Raw 32-byte SHA-256 digest. The lean form used for per-node hashing;
/// `ContentHash::of` is the hex-string wrapper for human-facing IDs.
pub(crate) fn sha256_digest(bytes: &[u8]) -> [u8; 32] {
    let mut hasher = Sha256::new();
    hasher.update(bytes);
    hasher.finalize().into()
}

impl ContentHash {
    /// Compute the content hash of `bytes`.
    pub fn of(bytes: &[u8]) -> Self {
        let digest = sha256_digest(bytes);
        let mut hex = String::with_capacity(64);
        for b in digest {
            hex.push_str(&format!("{:02x}", b));
        }
        ContentHash(hex)
    }

    /// The hex string form of this hash.
    pub fn as_str(&self) -> &str {
        &self.0
    }
}

impl std::fmt::Display for ContentHash {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}", self.0)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn hash_is_deterministic_and_hex() {
        let a = ContentHash::of(b"hello world");
        let b = ContentHash::of(b"hello world");
        assert_eq!(a, b);
        assert_eq!(a.as_str().len(), 64);
        assert!(a.as_str().chars().all(|c| c.is_ascii_hexdigit()));
    }

    #[test]
    fn different_content_differs() {
        assert_ne!(ContentHash::of(b"a"), ContentHash::of(b"b"));
    }

    #[test]
    fn digest_matches_content_hash_hex() {
        let d = sha256_digest(b"hello");
        let hex: String = d.iter().map(|b| format!("{:02x}", b)).collect();
        assert_eq!(hex, ContentHash::of(b"hello").as_str());
        assert_eq!(d.len(), 32);
    }
}
