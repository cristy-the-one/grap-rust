//! BlobStore - chunked arena for large immutable text (cold path).
//! Zero-copy reads via direct slice into the arena. See core_graph_design.md:147-221.
//!
//! Supports two backends:
//! - In-memory chunks (always available)
//! - Memory-mapped file (when the `mmap` feature is enabled) for very large corpora.



const CHUNK_SIZE: usize = 4 * 1024 * 1024;

#[derive(Debug, Clone)]
pub struct BlobRef {
    pub chunk_idx: u32,
    pub offset: u32,
    pub length: u32,
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

#[derive(Debug, Default)]
pub struct BlobStore {
    pub(crate) storage: Storage,
}

impl BlobStore {
    pub fn new() -> Self {
        Self {
            storage: Storage::default(),
        }
    }

    /// Append text, return lightweight BlobRef.
    /// Only supported in in-memory mode (writing while using mmap mode is not intended).
    pub fn store(&mut self, text: &str) -> BlobRef {
        // Ensure we are in chunks mode for writing
        if let Storage::Chunks(chunks) = &mut self.storage {
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

            return BlobRef {
                chunk_idx: chunk_idx as u32,
                offset: offset as u32,
                length: len as u32,
                uncompressed_len: len as u32,
            };
        }

        // If somehow in mmap mode, fall back to creating a new in-memory store (shouldn't normally happen)
        self.storage = Storage::default();
        self.store(text)
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
    pub fn is_mapped(&self) -> bool {
        matches!(self.storage, Storage::Mmap(_))
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
