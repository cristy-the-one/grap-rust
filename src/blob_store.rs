//! BlobStore - chunked arena for large immutable text (cold path).
//! Zero-copy reads via direct slice into the arena. See core_graph_design.md:147-221.

const CHUNK_SIZE: usize = 4 * 1024 * 1024;

#[derive(Debug, Clone)]
pub struct BlobRef {
    pub chunk_idx: u32,
    pub offset: u32,
    pub length: u32,
    pub uncompressed_len: u32,
}

#[derive(Debug, Default)]
pub struct BlobStore {
    chunks: Vec<Vec<u8>>,
}

impl BlobStore {
    pub fn new() -> Self {
        Self {
            chunks: vec![Vec::with_capacity(CHUNK_SIZE)],
        }
    }

    /// Append text, return lightweight BlobRef. (Uncompressed in v1; LZ4 later.)
    pub fn store(&mut self, text: &str) -> BlobRef {
        let bytes = text.as_bytes();
        let len = bytes.len();

        let chunk_idx = {
            let last = self.chunks.len() - 1;
            if self.chunks[last].len() + len > CHUNK_SIZE && !self.chunks[last].is_empty() {
                self.chunks.push(Vec::with_capacity(CHUNK_SIZE.max(len)));
                self.chunks.len() - 1
            } else {
                last
            }
        };

        let offset = self.chunks[chunk_idx].len();
        self.chunks[chunk_idx].extend_from_slice(bytes);

        BlobRef {
            chunk_idx: chunk_idx as u32,
            offset: offset as u32,
            length: len as u32,
            uncompressed_len: len as u32,
        }
    }

    /// Zero-copy read. Caller must ensure the BlobRef belongs to this store.
    #[allow(unsafe_code)]
    pub fn read(&self, r: &BlobRef) -> &str {
        let chunk = &self.chunks[r.chunk_idx as usize];
        let start = r.offset as usize;
        let end = start + r.length as usize;
        // PORT NOTE: matches the design doc's unsafe from_utf8_unchecked (md:219).
        // Safety: we only ever store valid UTF-8 from &str.
        unsafe { std::str::from_utf8_unchecked(&chunk[start..end]) }
    }
}
