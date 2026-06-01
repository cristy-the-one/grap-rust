//! Content-addressed hashing (SHA-256, hex-encoded).

use sha2::{Digest, Sha256};

/// A content-addressed identifier: the lowercase hex SHA-256 of some bytes.
#[derive(Debug, Clone, PartialEq, Eq, Hash, serde::Serialize, serde::Deserialize)]
pub struct ContentHash(pub String);

impl ContentHash {
    /// Compute the content hash of `bytes`.
    pub fn of(bytes: &[u8]) -> Self {
        let mut hasher = Sha256::new();
        hasher.update(bytes);
        let digest = hasher.finalize();
        let mut hex = String::with_capacity(digest.len() * 2);
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
}
