//! BlobStore - chunked arena for large immutable text (cold path).
//! Zero-copy reads via direct slice into the arena. See core_graph_design.md:147-221.
//!
//! Supports two backends:
//! - In-memory chunks (always available)
//! - Memory-mapped file (when the `mmap` feature is enabled) for very large corpora.

const CHUNK_SIZE: usize = 4 * 1024 * 1024;

/// Lightweight handle into a [`BlobStore`] chunk.
///
/// All fields are `u32` to keep the struct small (16 bytes).
#[derive(Debug, Clone)]
pub struct BlobRef {
    /// Index of the chunk inside the `BlobStore`.
    pub chunk_idx: u32,
    /// Byte offset within that chunk where this blob starts.
    pub offset: u32,
    /// Length of the stored (possibly compressed) bytes.
    pub length: u32,
    /// Original (uncompressed) length in bytes.
    pub uncompressed_len: u32,
}

/// Internal storage backend for blobs.
#[derive(Debug)]
pub(crate) enum Storage {
    /// Normal in-memory chunks (used for writing and portable loading).
    Chunks(Vec<Vec<u8>>),
    /// Memory-mapped flat blob file (used for fast loading of huge datasets).
    #[cfg(feature = "mmap")]
    Mmap(memmap2::Mmap),
}

impl Default for Storage {
    fn default() -> Self {
        Storage::Chunks(vec![Vec::with_capacity(CHUNK_SIZE)])
    }
}

impl Storage {
    fn from_chunks(chunks: Vec<Vec<u8>>) -> Self {
        Storage::Chunks(chunks)
    }
}

/// Arena-based storage for large immutable text payloads (node blobs).
///
/// Blobs are appended to fixed-size chunks and referenced by [`BlobRef`].
/// In-memory mode (always available) stores chunks as plain `Vec<u8>`.
/// The `mmap` feature adds a read-only memory-mapped backend for large corpora.
#[derive(Debug, Default)]
pub struct BlobStore {
    pub(crate) storage: Storage,
}

impl BlobStore {
    /// Creates an empty `BlobStore` with a single empty in-memory chunk.
    pub fn new() -> Self {
        Self {
            storage: Storage::default(),
        }
    }

    /// Append text, return lightweight BlobRef.
    /// Only supported in in-memory mode (writing while using mmap mode is not intended).
    pub fn store(&mut self, text: &str) -> BlobRef {
        // Copy-on-write: a write to a memory-mapped (read-mostly) store migrates the
        // mapped bytes into a single owned chunk so every existing BlobRef (global
        // offset, chunk_idx 0) stays valid, then we append normally. One-time cost:
        // the corpus is copied into RAM on the first mutation after a mmap open.
        #[cfg(feature = "mmap")]
        if let Storage::Mmap(mmap) = &self.storage {
            let owned: Vec<u8> = mmap.to_vec();
            self.storage = Storage::Chunks(vec![owned]);
        }

        // At this point the storage is always Chunks (either originally or after the
        // copy-on-write migration above).
        let chunks = match &mut self.storage {
            Storage::Chunks(chunks) => chunks,
            #[cfg(feature = "mmap")]
            Storage::Mmap(_) => unreachable!("mmap was already converted to Chunks above"),
        };

        let bytes = text.as_bytes();
        let len = bytes.len();

        let chunk_idx = {
            let last = chunks.len() - 1;
            if chunks[last].len() + len > CHUNK_SIZE && !chunks[last].is_empty() {
                chunks.push(Vec::with_capacity(CHUNK_SIZE.max(len)));
                chunks.len() - 1
            } else {
                last
            }
        };

        let offset = chunks[chunk_idx].len();
        chunks[chunk_idx].extend_from_slice(bytes);

        BlobRef {
            chunk_idx: chunk_idx as u32,
            offset: offset as u32,
            length: len as u32,
            uncompressed_len: len as u32,
        }
    }

    /// Store a slice of f32 values (intended for embeddings).
    /// The data is stored as raw bytes and can be read back with `read_embedding`.
    #[allow(unsafe_code)]
    pub fn store_embedding(&mut self, embedding: &[f32]) -> BlobRef {
        // SAFETY: We are only reinterpreting the f32 slice as bytes for storage.
        // The inverse operation in `read_embedding` is also carefully controlled.
        let bytes = unsafe {
            std::slice::from_raw_parts(
                embedding.as_ptr() as *const u8,
                embedding.len() * std::mem::size_of::<f32>(),
            )
        };
        self.store_bytes(bytes)
    }

    /// Internal helper used by both `store` and `store_embedding`.
    fn store_bytes(&mut self, bytes: &[u8]) -> BlobRef {
        // Copy-on-write: a write to a memory-mapped (read-mostly) store migrates the
        // mapped bytes into a single owned chunk so every existing BlobRef (global
        // offset, chunk_idx 0) stays valid, then we append normally. One-time cost:
        // the corpus is copied into RAM on the first mutation after a mmap open.
        #[cfg(feature = "mmap")]
        if let Storage::Mmap(mmap) = &self.storage {
            let owned: Vec<u8> = mmap.to_vec();
            self.storage = Storage::Chunks(vec![owned]);
        }

        let chunks = match &mut self.storage {
            Storage::Chunks(chunks) => chunks,
            #[cfg(feature = "mmap")]
            Storage::Mmap(_) => unreachable!(),
        };

        let len = bytes.len();
        let chunk_idx = {
            let last = chunks.len() - 1;
            if chunks[last].len() + len > CHUNK_SIZE && !chunks[last].is_empty() {
                chunks.push(Vec::with_capacity(CHUNK_SIZE.max(len)));
                chunks.len() - 1
            } else {
                last
            }
        };

        let offset = chunks[chunk_idx].len();
        chunks[chunk_idx].extend_from_slice(bytes);

        BlobRef {
            chunk_idx: chunk_idx as u32,
            offset: offset as u32,
            length: len as u32,
            uncompressed_len: len as u32,
        }
    }

    /// Zero-copy read. Works for both in-memory and memory-mapped backends.
    #[allow(unsafe_code)]
    pub fn read(&self, r: &BlobRef) -> &str {
        match &self.storage {
            Storage::Chunks(chunks) => {
                let chunk = &chunks[r.chunk_idx as usize];
                let start = r.offset as usize;
                let end = start + r.length as usize;
                unsafe { std::str::from_utf8_unchecked(&chunk[start..end]) }
            }
            #[cfg(feature = "mmap")]
            Storage::Mmap(mmap) => {
                // In mmap mode we treat the entire mapped file as a single flat region.
                // The saved BlobRef's "chunk_idx + offset" is interpreted as an absolute offset
                // into the mapped file (this is how save_to_disk + load_mmap work together).
                let start = r.offset as usize; // For mmap loads we currently store global offset in .offset
                                               // If we ever want true chunked mmap we can reinterpret chunk_idx, but flat is simpler and fast.
                let end = start + r.length as usize;

                // SAFETY: The mmap is owned by this BlobStore and lives at least as long as the &str.
                let bytes = &mmap[start..end];
                unsafe { std::str::from_utf8_unchecked(bytes) }
            }
        }
    }

    /// Returns true if this BlobStore is backed by a memory map.
    #[cfg(feature = "mmap")]
    pub fn is_mapped(&self) -> bool {
        matches!(self.storage, Storage::Mmap(_))
    }

    /// Best-effort read back of a previously stored embedding.
    /// Returns `None` if the blob is not a valid f32 array.
    ///
    /// The result is always an owned `Vec<f32>` because the underlying byte storage
    /// cannot guarantee f32 alignment for zero-copy reinterpretation.
    pub fn read_embedding(&self, r: &BlobRef) -> Option<Vec<f32>> {
        let bytes = self.read_bytes(r)?;
        if bytes.len() % 4 != 0 {
            return None;
        }

        // Safe: we validated the length. We copy into an aligned Vec<f32>.
        let mut out = Vec::with_capacity(bytes.len() / 4);
        // Copy in chunks of 4 bytes and transmute (portable, no alignment issues)
        for chunk in bytes.chunks_exact(4) {
            let val = f32::from_le_bytes([chunk[0], chunk[1], chunk[2], chunk[3]]);
            out.push(val);
        }
        Some(out)
    }

    /// Internal helper to read raw bytes for a BlobRef (used by `read` and `read_embedding`).
    fn read_bytes(&self, r: &BlobRef) -> Option<&[u8]> {
        match &self.storage {
            Storage::Chunks(chunks) => {
                let chunk = &chunks[r.chunk_idx as usize];
                let start = r.offset as usize;
                let end = start + r.length as usize;
                if end > chunk.len() {
                    return None;
                }
                Some(&chunk[start..end])
            }
            #[cfg(feature = "mmap")]
            Storage::Mmap(mmap) => {
                let start = r.offset as usize;
                let end = start + r.length as usize;
                if end > mmap.len() {
                    return None;
                }
                Some(&mmap[start..end])
            }
        }
    }

    /// Total bytes currently held by the store (sum of chunk lengths, or the
    /// mapped-file length). Includes dead/orphaned bytes — `vacuum_blobs` reclaims them.
    pub fn total_bytes(&self) -> usize {
        match &self.storage {
            Storage::Chunks(c) => c.iter().map(|chunk| chunk.len()).sum(),
            #[cfg(feature = "mmap")]
            Storage::Mmap(m) => m.len(),
        }
    }

    /// Returns the number of chunks (only meaningful in in-memory mode).
    pub fn chunk_count(&self) -> usize {
        match &self.storage {
            Storage::Chunks(c) => c.len(),
            #[cfg(feature = "mmap")]
            Storage::Mmap(_) => 1, // treated as one flat region
        }
    }

    /// Iterate over chunks (in-memory mode only). Used by the serializer.
    pub(crate) fn chunks(&self) -> &[Vec<u8>] {
        match &self.storage {
            Storage::Chunks(c) => c,
            #[cfg(feature = "mmap")]
            Storage::Mmap(_) => &[], // should not be called in mmap mode during save
        }
    }

    /// Internal constructor used by the serializer for the portable load path.
    pub(crate) fn from_chunks(chunks: Vec<Vec<u8>>) -> Self {
        Self {
            storage: Storage::from_chunks(chunks),
        }
    }
}

#[cfg(feature = "mmap")]
impl BlobStore {
    /// Create a BlobStore backed by an already-mapped file.
    /// The caller is responsible for ensuring the offsets in BlobRef make sense for this mapping.
    pub(crate) fn from_mmap(mmap: memmap2::Mmap) -> Self {
        Self {
            storage: Storage::Mmap(mmap),
        }
    }
}
