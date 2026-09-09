//! BlobStore - chunked arena for large immutable text (cold path).
//! Zero-copy reads via direct slice into the arena. See core_graph_design.md:147-221.
//!
//! Supports two backends:
//! - In-memory chunks (always available)
//! - Memory-mapped file (when the `mmap` feature is enabled) for very large corpora.

const CHUNK_SIZE: usize = 4 * 1024 * 1024;

/// Lightweight handle into a [`BlobStore`] chunk.
///
/// `chunk_idx`, `length`, and `uncompressed_len` are `u32`; `offset` is `u64` to
/// support blob files beyond 4 GiB in the memory-mapped backend (struct is 24 bytes).
#[derive(Debug, Clone)]
pub struct BlobRef {
    /// Index of the chunk inside the `BlobStore`.
    pub chunk_idx: u32,
    /// Byte offset: local within the chunk for in-memory `Chunks`, or the global
    /// offset into the mapped file for mapped chunks.
    pub offset: u64,
    /// Length of the stored (possibly compressed) bytes.
    pub length: u32,
    /// Original (uncompressed) length in bytes.
    pub uncompressed_len: u32,
}

/// An immutable shared arena chunk. Only uniquely owned memory can be appended.
#[derive(Debug)]
enum Chunk {
    Owned(Vec<u8>),
    #[cfg(feature = "mmap")]
    Mapped(memmap2::Mmap),
}
impl Chunk {
    fn bytes(&self) -> &[u8] {
        match self {
            Self::Owned(bytes) => bytes,
            #[cfg(feature = "mmap")]
            Self::Mapped(map) => map,
        }
    }
}

/// Append-only payload arena. Rebalance shares immutable chunks; subsequent writes
/// allocate a new chunk rather than copying or modifying shared payloads.
#[derive(Debug, Default)]
pub struct BlobStore {
    chunks: Vec<std::sync::Arc<Chunk>>,
}

impl BlobStore {
    /// Creates an empty arena, allocating payload memory on first write.
    pub fn new() -> Self {
        Self::default()
    }

    /// Append text and return a lightweight reference.
    pub fn store(&mut self, text: &str) -> BlobRef {
        self.store_bytes(text.as_bytes())
    }

    /// Share the referenced chunk, remapping its local index. The import cache
    /// belongs to this destination store and must not outlive its chunk layout.
    pub(crate) fn import_blob(
        &mut self,
        source: &Self,
        reference: &BlobRef,
        imported: &mut std::collections::HashMap<usize, u32>,
    ) -> BlobRef {
        let chunk = &source.chunks[reference.chunk_idx as usize];
        let key = std::sync::Arc::as_ptr(chunk) as usize;
        let index = *imported.entry(key).or_insert_with(|| {
            let index = u32::try_from(self.chunks.len()).expect("too many blob chunks");
            self.chunks.push(chunk.clone());
            index
        });
        BlobRef {
            chunk_idx: index,
            ..reference.clone()
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
                std::mem::size_of_val(embedding),
            )
        };
        self.store_bytes(bytes)
    }

    fn store_bytes(&mut self, bytes: &[u8]) -> BlobRef {
        let writable = self.chunks.last_mut().and_then(std::sync::Arc::get_mut);
        if !matches!(writable, Some(Chunk::Owned(chunk)) if chunk.len() + bytes.len() <= CHUNK_SIZE)
        {
            self.chunks
                .push(std::sync::Arc::new(Chunk::Owned(Vec::with_capacity(
                    CHUNK_SIZE.max(bytes.len()),
                ))));
        }
        let last = self.chunks.len() - 1;
        let chunk = match std::sync::Arc::get_mut(&mut self.chunks[last]).unwrap() {
            Chunk::Owned(chunk) => chunk,
            #[cfg(feature = "mmap")]
            Chunk::Mapped(_) => unreachable!("new chunk is writable"),
        };
        let offset = chunk.len();
        chunk.extend_from_slice(bytes);
        BlobRef {
            chunk_idx: u32::try_from(last).expect("too many blob chunks"),
            offset: offset as u64,
            length: u32::try_from(bytes.len()).expect("blob exceeds u32::MAX"),
            uncompressed_len: bytes.len() as u32,
        }
    }

    /// Zero-copy UTF-8 read, including shared and mapped chunks.
    pub fn read(&self, r: &BlobRef) -> &str {
        std::str::from_utf8(self.read_bytes(r).expect("invalid blob reference"))
            .expect("text blob is not UTF-8")
    }

    /// Whether any payload chunk is memory mapped.
    #[cfg(feature = "mmap")]
    pub fn is_mapped(&self) -> bool {
        self.chunks
            .iter()
            .any(|chunk| matches!(chunk.as_ref(), Chunk::Mapped(_)))
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

    /// Checked raw payload access, also used by the streaming serializer.
    pub(crate) fn read_bytes(&self, r: &BlobRef) -> Option<&[u8]> {
        let chunk = self.chunks.get(r.chunk_idx as usize)?.bytes();
        let start = usize::try_from(r.offset).ok()?;
        chunk.get(start..start.checked_add(r.length as usize)?)
    }

    /// Bytes referenced by this arena, including dead bytes in shared chunks.
    /// Summing this across shards can count shared allocations more than once.
    pub fn total_bytes(&self) -> usize {
        self.chunks.iter().map(|c| c.bytes().len()).sum()
    }

    /// Number of referenced chunks.
    pub fn chunk_count(&self) -> usize {
        self.chunks.len()
    }

    pub(crate) fn from_chunks(chunks: Vec<Vec<u8>>) -> Self {
        Self {
            chunks: chunks
                .into_iter()
                .map(|c| std::sync::Arc::new(Chunk::Owned(c)))
                .collect(),
        }
    }

    #[cfg(feature = "mmap")]
    pub(crate) fn from_mmap(mmap: memmap2::Mmap) -> Self {
        Self {
            chunks: vec![std::sync::Arc::new(Chunk::Mapped(mmap))],
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn imported_payload_is_shared_and_appends_do_not_copy_or_alias_writes() {
        let mut source = BlobStore::new();
        let original = source.store("Romanian legal rule");
        let pointer = source.read(&original).as_ptr();
        let mut target = BlobStore::new();
        let mut imports = Default::default();
        let imported = target.import_blob(&source, &original, &mut imports);
        assert_eq!(target.read(&imported).as_ptr(), pointer);
        let another = target.import_blob(&source, &original, &mut imports);
        assert_eq!(another.chunk_idx, imported.chunk_idx);
        source.store("source append");
        let new = target.store("target append");
        assert_ne!(new.chunk_idx, imported.chunk_idx);
        assert_eq!(source.read(&original).as_ptr(), pointer);
        drop(source);
        assert_eq!(target.read(&imported), "Romanian legal rule");
        assert_eq!(target.read(&new), "target append");
    }

    #[test]
    fn blobref_offset_is_u64_capable() {
        let r = BlobRef {
            chunk_idx: 0,
            offset: (u32::MAX as u64) + 1234,
            length: 4,
            uncompressed_len: 4,
        };
        assert_eq!(r.offset, 4_294_968_529);
    }
}
