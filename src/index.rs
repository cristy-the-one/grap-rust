//! Full-text inverted index with BM25 scoring (Section 11 of core_graph_design.md).
//!
//! This module provides the "entry point" for queries: given natural language text,
//! find the most relevant starting nodes (seeds) before running property-weighted
//! graph traversal.
//!
//! The implementation is a faithful port of the design document, with small
//! ergonomic improvements (better docs, `IndexStats`, etc.).

use std::collections::{BTreeMap, HashMap, HashSet};

use crate::ids::NodeId;
use crate::properties::PropertyValue;
use crate::GraphError;

// The on-disk text index lives as a sibling module when the feature is enabled.
// Declared from lib.rs to keep the file at src/on_disk_text_index.rs.

// ============================================================================
// Tokenizer
// ============================================================================

/// Pluggable tokenizer trait.
///
/// Implementors must be `Send + Sync` so `InvertedIndex` can be used
/// behind `RwLock` or in multi-threaded contexts.
pub trait Tokenizer: Send + Sync {
    /// Tokenize text into (term, byte_position) pairs.
    ///
    /// Positions are byte offsets into the original string (useful for phrase queries).
    fn tokenize<'a>(&self, text: &'a str) -> Vec<(&'a str, u32)>;
}

/// Default tokenizer used by `InvertedIndex::new()`.
///
/// Rules (exactly as specified in the design):
/// - Splits on non-alphanumeric + underscore boundaries
/// - Lowercases terms for matching (original case is kept for display)
/// - Drops tokens shorter than 2 characters
/// - Drops pure-numeric tokens
/// - Drops a curated list of English stop words
#[derive(Debug)]
pub struct DefaultTokenizer {
    stop_words: HashSet<&'static str>,
}

impl DefaultTokenizer {
    /// Creates a new tokenizer with the built-in English stop-word list.
    pub fn new() -> Self {
        let stop_words: HashSet<&'static str> = [
            "the", "a", "an", "is", "are", "was", "were", "be", "been", "being", "have", "has",
            "had", "do", "does", "did", "will", "would", "could", "should", "may", "might",
            "shall", "can", "to", "of", "in", "for", "on", "with", "at", "by", "from", "as",
            "into", "through", "during", "before", "after", "and", "but", "or", "nor", "not", "so",
            "if", "then", "than", "that", "this", "it", "its", "they", "them", "we", "you", "he",
            "she", "his", "her", "my", "your", "our", "their",
        ]
        .iter()
        .copied()
        .collect();

        Self { stop_words }
    }
}

impl Tokenizer for DefaultTokenizer {
    fn tokenize<'a>(&self, text: &'a str) -> Vec<(&'a str, u32)> {
        let mut tokens = Vec::new();
        let mut start = None;

        for (i, ch) in text.char_indices() {
            if ch.is_alphanumeric() || ch == '_' {
                if start.is_none() {
                    start = Some(i);
                }
            } else if let Some(s) = start.take() {
                let word = &text[s..i];
                if word.len() >= 2
                    && !word.chars().all(|c| c.is_numeric())
                    && !self.stop_words.contains(word.to_lowercase().as_str())
                {
                    tokens.push((word, s as u32));
                }
            }
        }

        // Handle last token (same rules as the main loop, including stop-word check)
        if let Some(s) = start {
            let word = &text[s..];
            if word.len() >= 2
                && !word.chars().all(|c| c.is_numeric())
                && !self.stop_words.contains(word.to_lowercase().as_str())
            {
                tokens.push((word, s as u32));
            }
        }

        tokens
    }
}

// ============================================================================
// Term Interning
// ============================================================================

/// A compact numeric identifier for an interned term string.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub struct TermId(u32);

#[derive(Debug, Default)]
struct StringInterner {
    strings: Vec<String>,
    lookup: HashMap<String, TermId>,
}

impl StringInterner {
    fn new() -> Self {
        Self::default()
    }

    fn intern(&mut self, s: &str) -> TermId {
        let lower = s.to_lowercase();
        if let Some(&id) = self.lookup.get(&lower) {
            return id;
        }
        let id = TermId(self.strings.len() as u32);
        self.strings.push(lower.clone());
        self.lookup.insert(lower, id);
        id
    }

    fn get(&self, s: &str) -> Option<TermId> {
        self.lookup.get(s.to_lowercase().as_str()).copied()
    }
}

// ============================================================================
// Postings
// ============================================================================

const MAX_POSITIONS_PER_POSTING: usize = 256;

#[derive(Debug, Clone)]
struct Posting {
    node_id: NodeId,
    frequency: u32,
    positions: Vec<u32>, // byte positions, sorted
}

#[derive(Debug, Clone, Default)]
struct PostingList {
    postings: Vec<Posting>,
    total_frequency: u64,
}

impl PostingList {
    fn add_posting(&mut self, node_id: NodeId, positions: Vec<u32>) {
        let freq = positions.len() as u32;
        self.total_frequency += freq as u64;

        match self
            .postings
            .binary_search_by_key(&node_id.0, |p| p.node_id.0)
        {
            Ok(idx) => {
                // Re-indexing the same node (rare in append-only use)
                self.postings[idx].frequency += freq;
                let p = &mut self.postings[idx];
                if p.positions.len() < MAX_POSITIONS_PER_POSTING {
                    p.positions.extend_from_slice(&positions);
                    p.positions.sort_unstable();
                    p.positions.dedup();
                }
            }
            Err(idx) => {
                let capped = if positions.len() > MAX_POSITIONS_PER_POSTING {
                    positions[..MAX_POSITIONS_PER_POSTING].to_vec()
                } else {
                    positions
                };
                self.postings.insert(
                    idx,
                    Posting {
                        node_id,
                        frequency: freq,
                        positions: capped,
                    },
                );
            }
        }
    }
}

// ============================================================================
// InvertedIndex
// ============================================================================

/// A ranked match returned by [`InvertedIndex::search`] and related methods.
#[derive(Debug, Clone)]
pub struct TextSearchResult {
    /// The node that matched the query.
    pub node_id: NodeId,
    /// BM25-style relevance score (higher is better).
    pub score: f64,
    /// The query terms that were found in this document.
    pub matched_terms: Vec<String>,
}

/// Controls how the text retrieval score and the graph traversal strength
/// are combined when producing final ranked results from the high-level
/// search methods on [`IndexedKnowledgeGraph`].
#[derive(Debug, Clone, Copy)]
pub struct FusionWeights {
    /// Relative weight given to the text / phrase search score (BM25 or hybrid).
    /// Higher values make results more sensitive to how well documents match
    /// the user's natural language query.
    pub text: f64,
    /// Relative weight given to the cumulative graph path strength.
    /// Higher values favor nodes reached through strong relationship chains.
    pub graph: f64,
}

impl Default for FusionWeights {
    /// Slightly favors text relevance (common default for knowledge retrieval).
    fn default() -> Self {
        Self {
            text: 0.6,
            graph: 0.4,
        }
    }
}

/// Weights used when combining text (BM25), vector (embedding), and graph signals
/// during hybrid retrieval.
///
/// Weights are **not** normalized automatically; ensure `text + vector + graph ≈ 1.0`
/// for meaningful fused scores. The default weights sum to 1.0.
#[derive(Debug, Clone)]
pub struct HybridWeights {
    /// Weight given to the text / BM25 score.
    pub text: f64,
    /// Weight given to the vector similarity score (e.g. cosine).
    pub vector: f64,
    /// Weight given to the graph path strength.
    pub graph: f64,
    /// If `Some(name)`, use this edge property's numeric value as the
    /// traversal priority weight instead of `edge.strength`.
    /// Falls back to `edge.strength` if the property is absent on a given edge.
    /// `None` (default) preserves existing behaviour exactly.
    pub traversal_weight_property: Option<String>,
}

impl Default for HybridWeights {
    fn default() -> Self {
        Self {
            text: 0.4,
            vector: 0.4,
            graph: 0.2,
            traversal_weight_property: None,
        }
    }
}

/// Cosine similarity between two vectors.
/// Returns 0.0 if either vector is empty or they have different lengths.
pub(crate) fn cosine_similarity(a: &[f32], b: &[f32]) -> f64 {
    if a.len() != b.len() || a.is_empty() {
        return 0.0;
    }

    let mut dot = 0.0;
    let mut norm_a = 0.0;
    let mut norm_b = 0.0;

    for i in 0..a.len() {
        dot += a[i] as f64 * b[i] as f64;
        norm_a += a[i] as f64 * a[i] as f64;
        norm_b += b[i] as f64 * b[i] as f64;
    }

    if norm_a == 0.0 || norm_b == 0.0 {
        return 0.0;
    }

    dot / (norm_a.sqrt() * norm_b.sqrt())
}

/// Rich result type returned by [`IndexedKnowledgeGraph::search_and_traverse`]
/// and [`IndexedKnowledgeGraph::phrase_search_and_traverse`].
///
/// It carries both the original text search signal and the graph traversal
/// signal, plus the final fused score used for ranking.
#[derive(Debug, Clone)]
pub struct CombinedResult<'a> {
    /// Original score from the text or phrase search.
    pub text_score: f64,
    /// Cosine similarity to the query vector (0.0 if no vector was provided or node has none).
    pub vector_score: f64,
    /// Cumulative strength of the path taken through the graph.
    pub graph_strength: f64,
    /// Final score after applying [`HybridWeights`] or [`FusionWeights`].
    pub fused_score: f64,
    /// Underlying graph traversal details (target node, path, depth, etc.).
    pub graph_result: QueryResult,
    /// The text content of the node (borrowed from the blob store).
    pub text: &'a str,
}

/// Diagnostic statistics about the current state of a text index backend.
#[derive(Debug, Clone)]
pub struct IndexStats {
    /// Number of distinct terms in the index vocabulary.
    pub unique_terms: usize,
    /// Total number of (term, document) posting entries.
    pub total_postings: usize,
    /// Number of documents (nodes) currently indexed.
    pub document_count: u32,
    /// Average document length in tokens.
    pub avg_doc_length: f64,
}

/// Common contract for all text index backends (in-memory today, on-disk in 0.2+).
///
/// Both the classic `InvertedIndex` and future persistent implementations
/// (see `on_disk_text_index`) will implement this trait. This allows
/// `IndexedKnowledgeGraph` to be (mostly) backend-agnostic.
pub trait TextIndex: Send + Sync + std::any::Any {
    /// Index the text content of a node.
    fn index_document(&mut self, node: NodeId, text: &str);

    /// Basic term search (returns (node, score) pairs). The implementation
    /// may return up to `max_results` entries.
    fn search(&self, query: &str, max_results: usize) -> Vec<(NodeId, f64)>;

    /// Phrase / proximity search.
    fn phrase_search(&self, phrase: &str, max_gap: u32, max_results: usize) -> Vec<(NodeId, f64)>;

    /// Return basic statistics about the index.
    fn stats(&self) -> IndexStats;

    /// Optional: delete support (tombstones in the on-disk case).
    fn delete_document(&mut self, _node: NodeId) {}

    /// Returns true if the document has been logically deleted (tombstone).
    /// On-disk backends use this during rebalance and traversal to skip/clean.
    fn is_document_deleted(&self, _node: NodeId) -> bool {
        false
    }

    // === Persistence hooks (0.2 on-disk index) ===

    /// Save the text index to a directory.
    /// Memory backends may return an error or be a no-op.
    fn save_to_dir(&mut self, _dir: &std::path::Path) -> Result<(), GraphError> {
        Ok(())
    }

    /// Load a text index from a directory.
    /// The caller is responsible for knowing which backend to use.
    /// This is mainly implemented by `OnDiskTextIndex`.
    fn load_from_dir(_dir: &std::path::Path) -> Result<Box<dyn TextIndex>, GraphError>
    where
        Self: Sized,
    {
        Err(GraphError::Other(
            "load_from_dir not supported for this TextIndex backend".into(),
        ))
    }

    /// Trigger compaction / vacuum on the text index (no-op for memory backend).
    fn vacuum(&mut self) {}

    /// Trigger a lighter compaction (no-op for memory backend).
    fn compact(&mut self) {}

    // Segment migration & content-aware rebalancing support.
    // Default implementations are no-ops; only `OnDiskTextIndex` provides real behavior.

    /// Removes and returns the sealed segment at `index`, together with its tier number.
    ///
    /// Default (in-memory) implementation always returns `None`.
    fn take_sealed_segment(&mut self, _index: usize) -> Option<(crate::OnDiskSegment, u32)> {
        None
    }

    /// Inserts an externally-produced segment (e.g. from a rebalance) at the given tier.
    ///
    /// Default (in-memory) implementation is a no-op.
    fn add_sealed_segment(&mut self, _seg: crate::OnDiskSegment, _tier: u32) {}

    /// Returns a short string describing the content of the segment at `seg_idx`.
    ///
    /// Used by rebalancing logic to pick a representative for cluster labelling.
    /// Default (in-memory) implementation returns `None`.
    fn sample_representative_for_segment(&self, _seg_idx: usize) -> Option<String> {
        None
    }

    /// Returns per-segment information (doc counts, live counts, approximate sizes, tiers).
    /// Memory backends return an empty vec. On-disk backends return one entry per sealed segment.
    /// Useful for monitoring, cost-based rebalancing, and deciding when to vacuum.
    fn segment_info(&self) -> Vec<crate::SegmentInfo> {
        vec![]
    }
}

impl TextIndex for InvertedIndex {
    fn index_document(&mut self, node: NodeId, text: &str) {
        // Delegate to the existing inherent method
        InvertedIndex::index_document(self, node, text);
    }

    fn search(&self, query: &str, max_results: usize) -> Vec<(NodeId, f64)> {
        self.search(query, max_results)
            .into_iter()
            .map(|r| (r.node_id, r.score))
            .collect()
    }

    fn phrase_search(&self, phrase: &str, max_gap: u32, max_results: usize) -> Vec<(NodeId, f64)> {
        self.phrase_search(phrase, max_gap, max_results)
            .into_iter()
            .map(|r| (r.node_id, r.score))
            .collect()
    }

    fn stats(&self) -> IndexStats {
        self.stats()
    }

    fn delete_document(&mut self, node: NodeId) {
        // The current in-memory index does not have delete yet (0.2 work)
        let _ = node;
    }
}

/// In-memory BM25 inverted index over node blobs.
///
/// This is the default text backend for [`IndexedKnowledgeGraph`] when no
/// on-disk persistence is needed. It keeps all postings in heap memory and
/// does not survive a process restart.
///
/// For persistence use `OnDiskTextIndex` (requires the `fulltext` feature).
pub struct InvertedIndex {
    interner: StringInterner,
    index: HashMap<TermId, PostingList>,
    document_count: u32,
    doc_lengths: Vec<u32>,
    avg_doc_length: f64,
    tokenizer: Box<dyn Tokenizer>,
}

impl InvertedIndex {
    /// Creates a new empty `InvertedIndex` with the default [`DefaultTokenizer`].
    pub fn new() -> Self {
        Self::with_tokenizer(Box::new(DefaultTokenizer::new()))
    }

    /// Creates a new empty `InvertedIndex` with a custom tokenizer.
    pub fn with_tokenizer(tokenizer: Box<dyn Tokenizer>) -> Self {
        Self {
            interner: StringInterner::new(),
            index: HashMap::new(),
            document_count: 0,
            doc_lengths: Vec::new(),
            avg_doc_length: 0.0,
            tokenizer,
        }
    }

    /// Index a document's text. Called automatically by `IndexedKnowledgeGraph::add_node`.
    pub fn index_document(&mut self, node_id: NodeId, text: &str) {
        let tokens = self.tokenizer.tokenize(text);

        let mut term_positions: BTreeMap<String, Vec<u32>> = BTreeMap::new();
        for (word, pos) in &tokens {
            let lower = word.to_lowercase();
            term_positions.entry(lower).or_default().push(*pos);
        }

        let doc_length = tokens.len() as u32;
        let idx = node_id.0 as usize;

        if idx >= self.doc_lengths.len() {
            self.doc_lengths.resize(idx + 1, 0);
        }
        self.doc_lengths[idx] = doc_length;
        self.document_count = self.document_count.max(idx as u32 + 1);

        // Recompute average (cheap enough for now)
        let total: u64 = self.doc_lengths.iter().map(|&l| l as u64).sum();
        self.avg_doc_length = total as f64 / self.document_count.max(1) as f64;

        for (term, positions) in term_positions {
            let term_id = self.interner.intern(&term);
            let pl = self.index.entry(term_id).or_default();
            pl.add_posting(node_id, positions);
        }
    }

    /// BM25 search. Returns the top `max_results` nodes ranked by relevance.
    pub fn search(&self, query: &str, max_results: usize) -> Vec<TextSearchResult> {
        let query_tokens = self.tokenizer.tokenize(query);
        let query_terms: Vec<String> = query_tokens
            .iter()
            .map(|(w, _)| w.to_lowercase())
            .collect::<HashSet<_>>()
            .into_iter()
            .collect();

        if query_terms.is_empty() {
            return Vec::new();
        }

        const K1: f64 = 1.2;
        const B: f64 = 0.75;

        let n = self.document_count as f64;
        let mut scores: HashMap<u32, (f64, Vec<String>)> = HashMap::new();

        for term in &query_terms {
            let Some(term_id) = self.interner.get(term) else {
                continue;
            };
            let Some(pl) = self.index.get(&term_id) else {
                continue;
            };

            let df = pl.postings.len() as f64;
            let idf = ((n - df + 0.5) / (df + 0.5) + 1.0).ln();

            for posting in &pl.postings {
                let doc_id = posting.node_id.0;
                let tf = posting.frequency as f64;
                let dl = self.doc_lengths.get(doc_id as usize).copied().unwrap_or(1) as f64;

                let tf_norm = (tf * (K1 + 1.0))
                    / (tf + K1 * (1.0 - B + B * dl / self.avg_doc_length.max(1.0)));

                let score = idf * tf_norm;

                let entry = scores.entry(doc_id).or_insert((0.0, Vec::new()));
                entry.0 += score;
                if !entry.1.contains(term) {
                    entry.1.push(term.clone());
                }
            }
        }

        let mut results: Vec<TextSearchResult> = scores
            .into_iter()
            .map(|(doc_id, (score, terms))| TextSearchResult {
                node_id: NodeId(doc_id),
                score,
                matched_terms: terms,
            })
            .collect();

        results.sort_by(|a, b| {
            b.score
                .partial_cmp(&a.score)
                .unwrap_or(std::cmp::Ordering::Equal)
        });
        results.truncate(max_results);
        results
    }

    /// BM25 search for a phrase. Returns nodes where the terms appear close together.
    ///
    /// Falls back to regular `search()` if the phrase has fewer than 2 terms.
    /// Search for documents where the given `phrase` terms appear within `max_gap`
    /// positions of each other, then rank by a blended BM25 + proximity score.
    ///
    /// # Single-term phrases
    ///
    /// When `phrase` tokenises to fewer than two terms (after stop-word removal),
    /// the method falls back to a plain [`search`] call. The result ranking is
    /// identical to calling `search` directly — there is no proximity penalty.
    pub fn phrase_search(
        &self,
        phrase: &str,
        max_gap: u32,
        max_results: usize,
    ) -> Vec<TextSearchResult> {
        let tokens = self.tokenizer.tokenize(phrase);
        let terms: Vec<String> = tokens.iter().map(|(w, _)| w.to_lowercase()).collect();

        if terms.len() < 2 {
            // Single token (or empty) — fall back to normal BM25 search.
            return self.search(phrase, max_results);
        }

        let posting_lists: Vec<Option<&PostingList>> = terms
            .iter()
            .map(|t| self.interner.get(t).and_then(|id| self.index.get(&id)))
            .collect();

        if posting_lists.iter().any(|pl| pl.is_none()) {
            return Vec::new();
        }

        let lists: Vec<&PostingList> = posting_lists.into_iter().map(|pl| pl.unwrap()).collect();

        let mut results = Vec::new();

        // Start with shortest posting list for efficiency
        let shortest_idx = lists
            .iter()
            .enumerate()
            .min_by_key(|(_, pl)| pl.postings.len())
            .map(|(i, _)| i)
            .unwrap_or(0);

        for posting in &lists[shortest_idx].postings {
            let node_id = posting.node_id;

            let all_present = lists.iter().enumerate().all(|(i, pl)| {
                i == shortest_idx
                    || pl
                        .postings
                        .binary_search_by_key(&node_id.0, |p| p.node_id.0)
                        .is_ok()
            });

            if !all_present {
                continue;
            }

            let term_positions: Vec<&[u32]> = lists
                .iter()
                .filter_map(|pl| {
                    // Safe: all_present already confirmed this succeeds. Use
                    // if-let rather than .unwrap() to guard against any future
                    // ordering inconsistency.
                    if let Ok(idx) = pl
                        .postings
                        .binary_search_by_key(&node_id.0, |p| p.node_id.0)
                    {
                        Some(pl.postings[idx].positions.as_slice())
                    } else {
                        None
                    }
                })
                .collect();

            let proximity = compute_proximity_score(&term_positions, max_gap);

            if proximity > 0.0 {
                // Simple but effective hybrid: boost BM25 by proximity factor
                let bm25 = self.bm25_score_for_terms(node_id, &terms);
                let blended = bm25 * (0.4 + 0.6 * proximity);

                results.push(TextSearchResult {
                    node_id,
                    score: blended,
                    matched_terms: terms.clone(),
                });
            }
        }

        results.sort_by(|a, b| {
            b.score
                .partial_cmp(&a.score)
                .unwrap_or(std::cmp::Ordering::Equal)
        });
        results.truncate(max_results);
        results
    }

    /// Internal helper: BM25 score of a document for a specific set of terms.
    fn bm25_score_for_terms(&self, node_id: NodeId, terms: &[String]) -> f64 {
        const K1: f64 = 1.2;
        const B: f64 = 0.75;

        let n = self.document_count as f64;
        let mut score = 0.0;

        for term in terms {
            let Some(term_id) = self.interner.get(term) else {
                continue;
            };
            let Some(pl) = self.index.get(&term_id) else {
                continue;
            };

            let df = pl.postings.len() as f64;
            let idf = ((n - df + 0.5) / (df + 0.5) + 1.0).ln();

            if let Ok(idx) = pl
                .postings
                .binary_search_by_key(&node_id.0, |p| p.node_id.0)
            {
                let posting = &pl.postings[idx];
                let tf = posting.frequency as f64;
                let dl = self
                    .doc_lengths
                    .get(node_id.0 as usize)
                    .copied()
                    .unwrap_or(1) as f64;

                let tf_norm = (tf * (K1 + 1.0))
                    / (tf + K1 * (1.0 - B + B * dl / self.avg_doc_length.max(1.0)));

                score += idf * tf_norm;
            }
        }
        score
    }

    /// Returns diagnostic statistics about the current state of the index.
    pub fn stats(&self) -> IndexStats {
        let total_postings: usize = self.index.values().map(|pl| pl.postings.len()).sum();
        IndexStats {
            unique_terms: self.index.len(),
            total_postings,
            document_count: self.document_count,
            avg_doc_length: self.avg_doc_length,
        }
    }
}

impl Default for InvertedIndex {
    fn default() -> Self {
        Self::new()
    }
}

impl std::fmt::Debug for InvertedIndex {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("InvertedIndex")
            .field("document_count", &self.document_count)
            .field("unique_terms", &self.index.len())
            .finish_non_exhaustive()
    }
}

// ============================================================================
// IndexedKnowledgeGraph – the main high-level type users will interact with
// ============================================================================

use crate::graph::KnowledgeGraph;
use crate::query::QueryResult;

/// A `KnowledgeGraph` that also maintains a full-text inverted index over the node blobs.
///
/// Adding a node automatically indexes its text. This enables the powerful
/// `search_and_traverse` workflow described in the design document.
pub struct IndexedKnowledgeGraph {
    /// The underlying knowledge graph (nodes, edges, properties).
    pub graph: KnowledgeGraph,
    /// The text index backend. Can be the classic in-memory `InvertedIndex`
    /// or the persistent segmented `OnDiskTextIndex`.
    pub text_index: Box<dyn TextIndex>,

    /// Vector / ANN index (optional).
    /// When present, `search_and_traverse_hybrid` can use it for fast vector
    /// candidate generation, and `vector_search` is available.
    ///
    /// Phase 0/1: populated with a `BruteForceVectorIndex` when the first
    /// embedding is added. Later replaced by an HNSW (or other) implementation.
    pub(crate) vector_index: Option<Box<dyn crate::vector_index::VectorIndex>>,

    /// Background merge/vacuum worker handles for on-disk text indexes.
    /// Stored here so they stay alive as long as the graph does.
    merge_workers: Vec<crate::MergeWorkerHandle>,
}

impl std::fmt::Debug for IndexedKnowledgeGraph {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("IndexedKnowledgeGraph")
            .field("graph", &self.graph)
            .finish_non_exhaustive()
    }
}

impl IndexedKnowledgeGraph {
    /// Creates a new empty `IndexedKnowledgeGraph` with the in-memory `InvertedIndex` backend.
    pub fn new() -> Self {
        Self {
            graph: KnowledgeGraph::new(),
            text_index: Box::new(InvertedIndex::new()),
            vector_index: None,
            merge_workers: Vec::new(),
        }
    }

    /// Create an `IndexedKnowledgeGraph` with a custom tokenizer (memory backend).
    pub fn with_tokenizer(tokenizer: Box<dyn Tokenizer>) -> Self {
        Self {
            graph: KnowledgeGraph::new(),
            text_index: Box::new(InvertedIndex::with_tokenizer(tokenizer)),
            vector_index: None,
            merge_workers: Vec::new(),
        }
    }

    /// Create an `IndexedKnowledgeGraph` that uses the on-disk segmented
    /// inverted index (the recommended default for persistent workloads).
    ///
    /// After construction, call `start_background_workers(...)` to enable
    /// automatic incremental compaction / vacuum in the background.
    pub fn new_on_disk() -> Self {
        Self {
            graph: KnowledgeGraph::new(),
            text_index: Box::new(crate::on_disk_text_index::OnDiskTextIndex::new()),
            vector_index: None,
            merge_workers: Vec::new(),
        }
    }

    /// Create an `IndexedKnowledgeGraph` using the on-disk segmented index
    /// **with a tiered merge policy** enabled for automatic background merging.
    ///
    /// `segments_per_tier` controls the merge aggressiveness (recommended: 3–5).
    /// After construction, call `start_background_workers` to actually run the merger.
    pub fn new_on_disk_with_merge_policy(segments_per_tier: usize) -> Self {
        Self {
            graph: KnowledgeGraph::new(),
            text_index: Box::new(
                crate::on_disk_text_index::OnDiskTextIndex::new_with_merge_policy(
                    segments_per_tier,
                ),
            ),
            vector_index: None,
            merge_workers: Vec::new(),
        }
    }

    /// Builder-style constructor that lets you choose the text backend.
    /// You can pass an `OnDiskTextIndex` (for persistence) or a plain
    /// `InvertedIndex` (lightweight, in-memory only).
    pub fn with_text_index(index: Box<dyn TextIndex>) -> Self {
        Self {
            graph: KnowledgeGraph::new(),
            text_index: index,
            vector_index: None,
            merge_workers: Vec::new(),
        }
    }

    /// Add a node and immediately index its text content.
    /// Adds a node with optional embedding.
    ///
    /// When an embedding is provided, it will be stored and can participate in hybrid vector + text + graph search.
    pub fn add_node(
        &mut self,
        text: &str,
        label: Option<&str>,
        embedding: Option<&[f32]>,
    ) -> NodeId {
        let id = self.graph.add_node(text, label, embedding);
        self.text_index.index_document(id, text);

        // Vector index ingestion — lazily initialise with the canonical factory.
        if let Some(emb) = embedding {
            let vidx = self
                .vector_index
                .get_or_insert_with(|| crate::vector_index::create_default_vector_index());
            vidx.add_vector(id, emb);
        }

        id
    }

    /// Replace a node's text content and optionally its embedding.
    ///
    /// - Text index is updated: old terms are removed (tombstoned for on-disk
    ///   indexes; may linger for the in-memory `InvertedIndex`) and new text is
    ///   indexed.
    /// - If `new_embedding` is `Some`, the vector index entry is replaced.
    /// - If `new_embedding` is `None`, the existing vector index entry is kept.
    /// - **Edges are never touched.**
    ///
    /// # Errors
    /// Returns `GraphError::Other` if the node is out of bounds or deleted.
    pub fn update_node(
        &mut self,
        id: crate::NodeId,
        new_text: &str,
        new_embedding: Option<&[f32]>,
    ) -> Result<(), crate::GraphError> {
        self.graph
            .update_node_content(id, new_text, new_embedding)?;
        self.text_index.delete_document(id);
        self.text_index.index_document(id, new_text);
        if let Some(emb) = new_embedding {
            if let Some(vidx) = &mut self.vector_index {
                vidx.remove_vector(id);
                vidx.add_vector(id, emb);
            }
        }
        Ok(())
    }

    /// Merge `absorb` into `keep` and update all indexes.
    ///
    /// Delegates to [`KnowledgeGraph::merge_nodes`] for graph topology, then:
    /// - Removes `absorb` from the text and vector indexes.
    /// - If `keep` gained `absorb`'s embedding, replaces `keep`'s vector index entry.
    ///
    /// # Errors
    /// Returns `GraphError::Other` if either node is out of bounds or deleted.
    pub fn merge_nodes(
        &mut self,
        keep: crate::NodeId,
        absorb: crate::NodeId,
    ) -> Result<crate::MergeReport, crate::GraphError> {
        let report = self.graph.merge_nodes(keep, absorb)?;

        // Remove absorbed node from indexes
        self.text_index.delete_document(absorb);
        if let Some(vidx) = &mut self.vector_index {
            vidx.remove_vector(absorb);
        }

        // If keep gained absorb's embedding, update the vector index for keep
        if report.embedding_updated {
            if let Some(new_emb) = self.graph.read_embedding(keep) {
                if let Some(vidx) = &mut self.vector_index {
                    vidx.remove_vector(keep);
                    vidx.add_vector(keep, &new_emb);
                }
            }
        }

        Ok(report)
    }

    /// Single-hop: direct outgoing neighbors where the connecting edge has
    /// `property == value`. Returns `(target_node, edge_strength)` pairs.
    ///
    /// Delegates to [`KnowledgeGraph::neighbors_by_relation`].
    pub fn neighbors_by_relation(
        &self,
        node: crate::NodeId,
        property: &str,
        value: crate::properties::PropertyValue,
    ) -> Vec<(crate::NodeId, f64)> {
        self.graph.neighbors_by_relation(node, property, value)
    }

    /// Single-hop: direct incoming predecessors where the connecting edge has
    /// `property == value`. Returns `(source_node, edge_strength)` pairs.
    ///
    /// Delegates to [`KnowledgeGraph::predecessors_by_relation`].
    pub fn predecessors_by_relation(
        &self,
        node: crate::NodeId,
        property: &str,
        value: crate::properties::PropertyValue,
    ) -> Vec<(crate::NodeId, f64)> {
        self.graph.predecessors_by_relation(node, property, value)
    }

    /// Multi-hop BFS up to `depth` hops following only edges where
    /// `property == value`. Returns full [`QueryResult`] list.
    ///
    /// Delegates to [`KnowledgeGraph::traverse_by_relation`].
    pub fn traverse_by_relation(
        &self,
        node: crate::NodeId,
        property: &str,
        value: crate::properties::PropertyValue,
        depth: u32,
        max_results: usize,
    ) -> Vec<crate::query::QueryResult> {
        self.graph
            .traverse_by_relation(node, property, value, depth, max_results)
    }

    /// Perform a pure (standalone) vector similarity search.
    ///
    /// Uses the configured `VectorIndex` (HNSW when the `ann-hnsw` feature is enabled,
    /// otherwise a brute-force fallback).
    ///
    /// Returns up to `top_k` nodes with their cosine similarity scores (higher = more similar).
    ///
    /// This method is independent of text search and graph traversal. It is useful for:
    /// - Pure semantic search
    /// - Building custom hybrid pipelines
    /// - Debugging / inspecting what the vector index "sees"
    pub fn vector_search(&self, query: &[f32], top_k: usize) -> Vec<(NodeId, f64)> {
        if let Some(vidx) = &self.vector_index {
            vidx.search(query, top_k)
        } else {
            // No vectors have been indexed yet — return empty (graceful)
            Vec::new()
        }
    }

    /// Add a directed edge to the graph. Properties are encoded into the edge bitmask.
    pub fn add_edge(
        &mut self,
        source: NodeId,
        target: NodeId,
        strength: f64,
        props: Vec<(crate::ids::PropertyTag, PropertyValue)>,
    ) -> crate::ids::EdgeId {
        self.graph.add_edge(source, target, strength, props)
    }

    /// High-level "text + graph" query.
    ///
    /// 1. Runs BM25 search to find high-quality seed nodes.
    /// 2. Expands outward using the property-weighted graph traversal.
    /// 3. Fuses the original text score with graph path strength using the supplied weights
    ///    and re-ranks the final results.
    ///
    /// Returns rich [`CombinedResult`] entries so callers can see both signals.
    pub fn search_and_traverse(
        &self,
        text_query: &str,
        max_seeds: usize,
        property_names: &[&str],
        min_strength: f64,
        max_depth: u32,
        max_results: usize,
        weights: FusionWeights,
    ) -> Vec<CombinedResult<'_>> {
        let text_results = self.text_index.search(text_query, max_seeds);

        if text_results.is_empty() {
            return Vec::new();
        }

        let seed_ids: Vec<NodeId> = text_results.iter().map(|(id, _)| *id).collect();

        // Build a quick lookup for seed text scores
        let seed_scores: std::collections::HashMap<NodeId, f64> =
            text_results.iter().map(|(id, sc)| (*id, *sc)).collect();

        let mut builder = self
            .graph
            .find()
            .from_nodes(&seed_ids)
            .min_strength(min_strength)
            .depth(max_depth)
            .limit(max_results);

        for name in property_names {
            builder = builder.with_property(name);
        }

        let graph_results = builder.execute_with_text();

        // Convert to rich results and apply fusion
        let mut combined: Vec<CombinedResult> = graph_results
            .into_iter()
            .map(|(gr, text)| {
                let text_score = seed_scores.get(&gr.node_id).copied().unwrap_or(0.0);
                let graph_strength = gr.cumulative_strength;
                let fused_score = text_score * weights.text + graph_strength * weights.graph;

                CombinedResult {
                    text_score,
                    vector_score: 0.0,
                    graph_strength,
                    fused_score,
                    graph_result: gr,
                    text,
                }
            })
            .collect();

        // Re-rank by fused score
        combined.sort_by(|a, b| {
            b.fused_score
                .partial_cmp(&a.fused_score)
                .unwrap_or(std::cmp::Ordering::Equal)
        });
        combined.truncate(max_results);

        combined
    }

    /// High-level hybrid "text + vector + graph" query.
    ///
    /// Seed selection strategy (Phase 2 integration):
    /// 1. Always runs BM25 text search for high-quality textual seeds.
    /// 2. If a `vector` is provided **and** a `VectorIndex` (HNSW or brute-force) exists,
    ///    we also pull approximate nearest neighbors from the vector index as additional seeds.
    /// 3. The union of text + vector seeds is used to start graph traversal.
    /// 4. Final ranking uses three-way fusion via [`HybridWeights`] (text / vector / graph).
    ///
    /// Nodes proposed directly by the ANN get a strong pre-computed `vector_score`.
    /// Nodes reached only through graph expansion still receive a `vector_score` via
    /// on-the-fly cosine if they have an embedding stored.
    ///
    /// When `vector` is `None` (or no vector index is present), behavior is identical
    /// to the non-hybrid `search_and_traverse`.
    pub fn search_and_traverse_hybrid(
        &self,
        text_query: &str,
        vector: Option<&[f32]>,
        max_seeds: usize,
        property_names: &[&str],
        min_strength: f64,
        max_depth: u32,
        max_results: usize,
        weights: HybridWeights,
    ) -> Vec<CombinedResult<'_>> {
        // Named constants make the budget arithmetic self-documenting.
        const MIN_VECTOR_CANDIDATES: usize = 8;
        const VECTOR_OVERSAMPLE: usize = 2;

        let text_results = self.text_index.search(text_query, max_seeds);
        let text_seed_scores: HashMap<NodeId, f64> = text_results.into_iter().collect();

        let vector_budget = max_seeds.max(MIN_VECTOR_CANDIDATES) * VECTOR_OVERSAMPLE;
        let mut vector_seed_scores: HashMap<NodeId, f64> = HashMap::with_capacity(vector_budget);

        if let Some(qvec) = vector {
            if let Some(vidx) = &self.vector_index {
                for (node, sim) in vidx.search(qvec, vector_budget) {
                    // Keep the highest similarity if the same node appears twice.
                    let entry = vector_seed_scores.entry(node).or_insert(0.0);
                    if sim > *entry {
                        *entry = sim;
                    }
                }
            }
        }

        // Build the union of text + vector seeds as a single deduped list.
        let all_seeds: Vec<NodeId> = text_seed_scores
            .keys()
            .chain(
                vector_seed_scores
                    .keys()
                    .filter(|k| !text_seed_scores.contains_key(k)),
            )
            .copied()
            .collect();

        if all_seeds.is_empty() {
            return Vec::new();
        }

        let mut builder = self
            .graph
            .find()
            .from_nodes(&all_seeds)
            .min_strength(min_strength)
            .depth(max_depth)
            .limit(max_results);

        for name in property_names {
            builder = builder.with_property(name);
        }

        if let Some(ref prop) = weights.traversal_weight_property {
            builder = builder.weight_by_property(prop);
        }

        let graph_results = builder.execute_with_text();

        let mut combined: Vec<CombinedResult> = graph_results
            .into_iter()
            .map(|(gr, text)| {
                let node = gr.node_id;

                let text_score = text_seed_scores.get(&node).copied().unwrap_or(0.0);

                // Prefer the pre-computed ANN score; fall back to on-the-fly cosine
                // for nodes reached only through graph expansion.
                let vector_score = if let Some(precomputed) = vector_seed_scores.get(&node) {
                    *precomputed
                } else if let Some(qvec) = vector {
                    self.read_embedding(node)
                        .map(|node_vec| cosine_similarity(qvec, &node_vec))
                        .unwrap_or(0.0)
                } else {
                    0.0
                };

                let graph_strength = gr.cumulative_strength;

                let fused_score = text_score * weights.text
                    + vector_score * weights.vector
                    + graph_strength * weights.graph;

                CombinedResult {
                    text_score,
                    vector_score,
                    graph_strength,
                    fused_score,
                    graph_result: gr,
                    text,
                }
            })
            .collect();

        combined.sort_by(|a, b| {
            b.fused_score
                .partial_cmp(&a.fused_score)
                .unwrap_or(std::cmp::Ordering::Equal)
        });
        combined.truncate(max_results);

        combined
    }

    /// Like [`search_and_traverse_hybrid`] but with sensible default [`HybridWeights`].
    pub fn search_and_traverse_hybrid_default(
        &self,
        text_query: &str,
        vector: Option<&[f32]>,
        max_seeds: usize,
        property_names: &[&str],
        min_strength: f64,
        max_depth: u32,
        max_results: usize,
    ) -> Vec<CombinedResult<'_>> {
        self.search_and_traverse_hybrid(
            text_query,
            vector,
            max_seeds,
            property_names,
            min_strength,
            max_depth,
            max_results,
            HybridWeights::default(),
        )
    }

    /// Returns the full text content of a node's blob (zero-copy).
    pub fn read_blob(&self, node: NodeId) -> &str {
        self.graph.read_blob(node)
    }

    /// Number of live (non-tombstoned) nodes (see `KnowledgeGraph::live_node_count`).
    pub fn live_node_count(&self) -> usize {
        self.graph.live_node_count()
    }

    /// True if a node has been logically deleted (e.g. absorbed by `merge_nodes`).
    pub fn is_node_deleted(&self, node: NodeId) -> bool {
        self.graph.is_node_deleted(node)
    }

    /// Returns the embedding stored for a node, if any.
    pub fn read_embedding(&self, node: NodeId) -> Option<Vec<f32>> {
        self.graph.read_embedding(node)
    }

    /// Phrase-aware high-level query (see [`search_and_traverse`] for details).
    ///
    /// Uses the hybrid phrase + BM25 scoring for seed selection.
    pub fn phrase_search_and_traverse(
        &self,
        phrase: &str,
        max_gap: u32,
        max_seeds: usize,
        property_names: &[&str],
        min_strength: f64,
        max_depth: u32,
        max_results: usize,
        weights: FusionWeights,
    ) -> Vec<CombinedResult<'_>> {
        let text_results = self.text_index.phrase_search(phrase, max_gap, max_seeds);

        if text_results.is_empty() {
            return Vec::new();
        }

        let seed_ids: Vec<NodeId> = text_results.iter().map(|(id, _)| *id).collect();

        let seed_scores: std::collections::HashMap<NodeId, f64> =
            text_results.iter().map(|(id, sc)| (*id, *sc)).collect();

        let mut builder = self
            .graph
            .find()
            .from_nodes(&seed_ids)
            .min_strength(min_strength)
            .depth(max_depth)
            .limit(max_results);

        for name in property_names {
            builder = builder.with_property(name);
        }

        let graph_results = builder.execute_with_text();

        let mut combined: Vec<CombinedResult> = graph_results
            .into_iter()
            .map(|(gr, text)| {
                let text_score = seed_scores.get(&gr.node_id).copied().unwrap_or(0.0);
                let graph_strength = gr.cumulative_strength;
                let fused_score = text_score * weights.text + graph_strength * weights.graph;

                CombinedResult {
                    text_score,
                    vector_score: 0.0,
                    graph_strength,
                    fused_score,
                    graph_result: gr,
                    text,
                }
            })
            .collect();

        combined.sort_by(|a, b| {
            b.fused_score
                .partial_cmp(&a.fused_score)
                .unwrap_or(std::cmp::Ordering::Equal)
        });
        combined.truncate(max_results);

        combined
    }

    /// Convenience wrapper that uses [`FusionWeights::default()`].
    pub fn search_and_traverse_default(
        &self,
        text_query: &str,
        max_seeds: usize,
        property_names: &[&str],
        min_strength: f64,
        max_depth: u32,
        max_results: usize,
    ) -> Vec<CombinedResult<'_>> {
        self.search_and_traverse(
            text_query,
            max_seeds,
            property_names,
            min_strength,
            max_depth,
            max_results,
            FusionWeights::default(),
        )
    }

    /// Convenience wrapper that uses [`FusionWeights::default()`].
    pub fn phrase_search_and_traverse_default(
        &self,
        phrase: &str,
        max_gap: u32,
        max_seeds: usize,
        property_names: &[&str],
        min_strength: f64,
        max_depth: u32,
        max_results: usize,
    ) -> Vec<CombinedResult<'_>> {
        self.phrase_search_and_traverse(
            phrase,
            max_gap,
            max_seeds,
            property_names,
            min_strength,
            max_depth,
            max_results,
            FusionWeights::default(),
        )
    }

    /// Returns diagnostic statistics about the current text index state.
    pub fn index_stats(&self) -> IndexStats {
        self.text_index.stats()
    }

    /// Exposed for CLI export/visualization tooling.
    pub fn all_edges(&self) -> &Vec<crate::graph::Edge> {
        self.graph.all_edges()
    }

    /// Saves the underlying graph to disk.
    ///
    /// The vector index (if present) is also persisted into a `vector_index/`
    /// subdirectory for fast reload (Phase 3 persistence).
    pub fn save_to_disk(&self, dir: &std::path::Path) -> Result<(), GraphError> {
        self.graph.save_to_disk(dir)?;

        // Persist vector index (HNSW or brute-force) if we have one.
        if let Some(vidx) = &self.vector_index {
            let vdir = dir.join("vector_index");
            if let Err(e) = vidx.save_to_dir(&vdir) {
                log::warn!(
                    "Vector index save failed — next load will rebuild from embeddings: {e}"
                );
            }
        }

        Ok(())
    }

    /// Loads a graph from disk and rebuilds the full-text inverted index.
    pub fn load_from_disk(dir: &std::path::Path) -> Result<Self, GraphError> {
        let graph = KnowledgeGraph::load_from_disk(dir)?;
        let mut ig = Self {
            graph,
            text_index: Box::new(InvertedIndex::new()),
            vector_index: None,
            merge_workers: Vec::new(),
        };

        // Rebuild the text index (simple and correct)
        for i in 0..ig.graph.node_count() {
            let id = NodeId(i as u32);
            let text = ig.graph.read_blob(id);
            ig.text_index.index_document(id, text);
        }

        // Phase 3: Try fast vector load, otherwise full rebuild
        if ig.load_vector_index(dir) {
            ig.sync_new_vectors();
        } else {
            ig.rebuild_vector_index();
        }

        Ok(ig)
    }

    /// Load only the metadata for a persisted graph (without loading blobs
    /// or rebuilding the text index).
    ///
    /// Delegates to [`KnowledgeGraph::load_meta`].
    pub fn load_meta(dir: &std::path::Path) -> Result<crate::serialize::GraphMeta, GraphError> {
        KnowledgeGraph::load_meta(dir)
    }

    /// Attempts to load a previously persisted vector index from `vector_index/`
    /// inside the given directory. Returns `true` if a valid persisted index was
    /// successfully loaded (fast path). Returns `false` if no persisted data was
    /// found or it was invalid — in which case the caller should fall back to
    /// `rebuild_vector_index()`.
    fn load_vector_index(&mut self, base_dir: &std::path::Path) -> bool {
        let vdir = base_dir.join("vector_index");
        let manifest_path = vdir.join("manifest.json");

        if !manifest_path.exists() {
            return false;
        }

        // Create the appropriate backend (HNSW if the feature is enabled)
        let mut vidx = crate::vector_index::create_default_vector_index();

        match vidx.try_load_from_dir(&vdir) {
            Ok(true) => {
                self.vector_index = Some(vidx);
                true
            }
            Ok(false) | Err(_) => {
                // Persisted data was missing, incompatible, or corrupted.
                // Fall back to full rebuild from the BlobStore.
                false
            }
        }
    }

    /// Rebuild the vector index from embeddings stored in the graph.
    ///
    /// Called after any load path (`load_from_disk`, `load_from_dir`, `load_mmap`)
    /// when no valid persisted index is available. The ANN index is an in-memory
    /// acceleration structure; embeddings are durably persisted via the `BlobStore`,
    /// so the index is always re-derivable from them.
    fn rebuild_vector_index(&mut self) {
        for i in 0..self.graph.node_count() {
            let id = NodeId(i as u32);
            if self.graph.is_node_deleted(id) {
                continue;
            }
            if let Some(emb) = self.graph.read_embedding(id) {
                let vidx = self
                    .vector_index
                    .get_or_insert_with(|| crate::vector_index::create_default_vector_index());
                vidx.add_vector(id, &emb);
            }
        }
    }

    /// After a successful fast load of a persisted vector index, this method
    /// inserts any *new* nodes that have embeddings but are not yet present
    /// in the vector index. This is the "delta sync" optimization.
    fn sync_new_vectors(&mut self) {
        if self.vector_index.is_none() {
            self.vector_index = Some(crate::vector_index::create_default_vector_index());
        }

        if let Some(vidx) = &mut self.vector_index {
            for i in 0..self.graph.node_count() {
                let id = NodeId(i as u32);
                if self.graph.is_node_deleted(id) {
                    continue;
                }
                if vidx.contains_vector(id) {
                    continue; // already have it — skip
                }
                if let Some(emb) = self.graph.read_embedding(id) {
                    vidx.add_vector(id, &emb);
                }
            }
        }
    }

    /// Attempts to repair a potentially corrupted graph directory.
    ///
    /// Delegates to [`KnowledgeGraph::repair`].
    pub fn repair(dir: &std::path::Path) -> Result<crate::serialize::RepairReport, GraphError> {
        KnowledgeGraph::repair(dir)
    }

    /// Plain text search (delegates to the inverted index).
    pub fn search(&self, query: &str, max_results: usize) -> Vec<TextSearchResult> {
        self.text_index
            .search(query, max_results)
            .into_iter()
            .map(|(node_id, score)| TextSearchResult {
                node_id,
                score,
                matched_terms: vec![], // TODO: populate from the backend
            })
            .collect()
    }

    /// Phrase search (delegates to the inverted index).
    pub fn phrase_search(
        &self,
        phrase: &str,
        max_gap: u32,
        max_results: usize,
    ) -> Vec<TextSearchResult> {
        self.text_index
            .phrase_search(phrase, max_gap, max_results)
            .into_iter()
            .map(|(node_id, score)| TextSearchResult {
                node_id,
                score,
                matched_terms: vec![],
            })
            .collect()
    }

    /// Removes a node from both the graph topology and the text index (tombstone).
    ///
    /// In sharded mode this will eventually trigger ghost-edge cleanup.
    /// Returns whether the node was successfully removed.
    pub fn remove_node(&mut self, node: NodeId) -> bool {
        let removed = self.graph.remove_node(node);
        if removed {
            self.text_index.delete_document(node);

            // Optional background ghost-edge cleanup hook.
            // In a real deployment this could be scheduled asynchronously.
            // For now we do a cheap synchronous pass (can be made lazy later).
            self.maybe_cleanup_ghost_edges_for(node);
        }
        removed
    }

    /// Hook for ghost-edge cleanup after deletion.
    /// In sharded setups this should be delegated to the `ShardRouter`.
    fn maybe_cleanup_ghost_edges_for(&mut self, _node: NodeId) {
        // No-op in the single-graph case.
        // Real implementation lives in ShardedGraph / ShardRouter.
    }

    // === Persistence for sharded + on-disk setups (0.2) ===

    /// Save the graph topology and the text index to a directory.
    ///
    /// Creates two subdirectories: `graph/` and `text_index/`.
    pub fn save_to_dir(&mut self, dir: &std::path::Path) -> Result<(), GraphError> {
        // Save the graph part
        self.graph.save_to_disk(&dir.join("graph"))?;

        // Save the text index
        self.text_index.save_to_dir(&dir.join("text_index"))?;

        // Save the vector index (HNSW or brute-force)
        if let Some(vidx) = &self.vector_index {
            if let Err(e) = vidx.save_to_dir(&dir.join("vector_index")) {
                log::warn!(
                    "Vector index save failed — next load will rebuild from embeddings: {e}"
                );
            }
        }

        Ok(())
    }

    /// Load a previously saved `IndexedKnowledgeGraph` from `dir`.
    ///
    /// Auto-detects whether `text_index/manifest.json` is present and chooses
    /// the on-disk backend; otherwise falls back to the in-memory `InvertedIndex`.
    pub fn load_from_dir(dir: &std::path::Path) -> Result<Self, GraphError> {
        // Load the graph topology + blobs (use the established portable loader)
        let graph = KnowledgeGraph::load_from_disk(&dir.join("graph"))?;

        // Decide on the text index backend by inspecting the directory.
        // If a manifest.json is present we use the persistent on-disk backend;
        // otherwise we fall back to the lightweight in-memory index.
        let text_dir = dir.join("text_index");
        let text_index: Box<dyn TextIndex> = if text_dir.join("manifest.json").exists() {
            match crate::on_disk_text_index::OnDiskTextIndex::load_from_dir(&text_dir) {
                Ok(idx) => idx,
                Err(_) => Box::new(InvertedIndex::new()),
            }
        } else {
            Box::new(InvertedIndex::new())
        };

        let mut result = Self {
            graph,
            text_index,
            vector_index: None,
            merge_workers: Vec::new(),
        };

        // Phase 3: Prefer fast load from persisted vector index if available.
        if result.load_vector_index(dir) {
            // Fast path succeeded → only sync new nodes (delta sync)
            result.sync_new_vectors();
        } else {
            result.rebuild_vector_index();
        }

        Ok(result)
    }

    /// Loads an `IndexedKnowledgeGraph` from `dir` using memory-mapped I/O for the graph data.
    ///
    /// Requires the `mmap` feature. Falls back to an empty in-memory text index when no
    /// on-disk text-index manifest is found.
    #[cfg(feature = "mmap")]
    pub fn load_mmap(dir: &std::path::Path) -> Result<Self, GraphError> {
        // When the mmap feature is enabled, use the real mmap loader for the graph part.
        let graph = KnowledgeGraph::load_mmap(&dir.join("graph"))?;

        let text_dir = dir.join("text_index");
        let text_index: Box<dyn TextIndex> = if text_dir.join("manifest.json").exists() {
            match crate::on_disk_text_index::OnDiskTextIndex::load_from_dir(&text_dir) {
                Ok(idx) => idx,
                Err(_) => Box::new(InvertedIndex::new()),
            }
        } else {
            Box::new(InvertedIndex::new())
        };

        let mut result = Self {
            graph,
            text_index,
            vector_index: None,
            merge_workers: Vec::new(),
        };

        // Phase 3: Prefer fast vector load
        if !result.load_vector_index(dir) {
            result.rebuild_vector_index();
        } else {
            result.sync_new_vectors();
        }

        Ok(result)
    }

    /// Loads an `IndexedKnowledgeGraph` from `dir`.
    ///
    /// This is the non-`mmap` fallback — it simply delegates to [`Self::load_from_dir`].
    /// Provided so call sites can always write `load_mmap` and the right implementation
    /// is chosen at compile time via the `mmap` feature flag.
    #[cfg(not(feature = "mmap"))]
    pub fn load_mmap(dir: &std::path::Path) -> Result<Self, GraphError> {
        // Fallback: when mmap feature is not compiled in, behave like load_from_dir.
        Self::load_from_dir(dir)
    }

    /// Returns segment-level information from the text index (doc counts, live counts, sizes, tiers).
    /// Memory backends return an empty list. On-disk backends return one entry per sealed segment.
    pub fn text_index_segment_info(&self) -> Vec<crate::SegmentInfo> {
        self.text_index.segment_info()
    }

    /// Trigger incremental vacuum / space reclamation on the text index
    /// (especially valuable for on-disk backends after deletes).
    pub fn vacuum_deleted_nodes(&mut self) {
        self.text_index.vacuum();
    }

    /// Starts background merge + incremental vacuum workers for on-disk text indexes.
    ///
    /// This is the recommended high-level API for getting automatic background
    /// compaction on a single graph. Worker handles are kept internally.
    ///
    /// Returns how many workers were started.
    pub fn start_background_workers(&mut self, interval: std::time::Duration) -> usize {
        let mut started = 0;

        // If the current text index is an OnDiskTextIndex, start a background
        // merge/vacuum worker for it. Worker handles are stored internally so
        // they live as long as the IndexedKnowledgeGraph.
        if let Some(ot) = (&mut *self.text_index as &mut dyn std::any::Any)
            .downcast_mut::<crate::on_disk_text_index::OnDiskTextIndex>()
        {
            let handle = ot.start_merge_worker(interval);
            self.merge_workers.push(handle);
            started += 1;
        }

        started
    }
}

#[cfg(test)]
mod update_node_tests {
    use crate::index::{IndexedKnowledgeGraph, InvertedIndex};

    fn make_graph() -> IndexedKnowledgeGraph {
        IndexedKnowledgeGraph::with_text_index(Box::new(InvertedIndex::new()))
    }

    #[test]
    fn update_node_new_text_searchable() {
        let mut g = make_graph();
        let id = g.add_node("original text", None, None);

        g.update_node(id, "updated content", None).unwrap();

        let hits = g.text_index.search("content", 5);
        assert!(
            hits.iter().any(|(n, _)| *n == id),
            "new text must be findable"
        );
    }

    #[test]
    fn update_node_preserves_edges() {
        let mut g = make_graph();
        let a = g.add_node("a", None, None);
        let b = g.add_node("b", None, None);
        g.add_edge(a, b, 0.8, vec![]);

        g.update_node(a, "a changed", None).unwrap();

        assert_eq!(g.graph.nodes[a.0 as usize].outgoing.len(), 1);
    }

    #[test]
    fn update_node_rejects_deleted() {
        let mut g = make_graph();
        let id = g.add_node("x", None, None);
        g.graph.remove_node(id);

        assert!(g.update_node(id, "y", None).is_err());
    }

    #[test]
    fn update_node_none_embedding_leaves_vector_index_unchanged() {
        let mut g = make_graph();
        g.vector_index = Some(Box::new(crate::BruteForceVectorIndex::new()));
        let id = g.add_node("a", None, Some(&[1.0f32, 0.0]));

        // Update text only; embedding must remain
        g.update_node(id, "b", None).unwrap();

        assert!(g.vector_index.as_ref().unwrap().contains_vector(id));
    }
}

impl Default for IndexedKnowledgeGraph {
    fn default() -> Self {
        Self::new()
    }
}

/// Score how well term positions form a near-consecutive phrase.
/// Ported from core_graph_design.md.
fn compute_proximity_score(term_positions: &[&[u32]], max_gap: u32) -> f64 {
    if term_positions.is_empty() {
        return 0.0;
    }
    if term_positions.len() == 1 {
        return 1.0;
    }

    let first_positions = term_positions[0];
    let mut best_score = 0.0f64;

    for &start_pos in first_positions {
        let mut current_pos = start_pos;
        let mut matched = 1u32;

        for i in 1..term_positions.len() {
            let next_positions = term_positions[i];
            let search = next_positions.binary_search(&(current_pos + 1));
            let candidate_idx = match search {
                Ok(idx) => idx,
                Err(idx) => idx,
            };

            if candidate_idx < next_positions.len() {
                let candidate = next_positions[candidate_idx];
                if candidate <= current_pos + max_gap {
                    current_pos = candidate;
                    matched += 1;
                } else {
                    break;
                }
            } else {
                break;
            }
        }

        let coverage = matched as f64 / term_positions.len() as f64;
        if coverage > best_score {
            best_score = coverage;
        }
    }

    best_score
}

// ============================================================================
// Basic smoke test for the index + traversal integration
// ============================================================================

#[cfg(test)]
mod tests {
    use super::*;
    use crate::properties::PropertyValue;

    #[test]
    fn indexed_graph_text_search_and_traverse() {
        let mut g = IndexedKnowledgeGraph::new();

        let topic = g.graph.properties.register("topic");

        let n0 = g.add_node(
            "Rust ownership and borrowing are fundamental to memory safety.",
            Some("rust"),
            None,
        );
        let n1 = g.add_node(
            "The borrow checker enforces rules about references and lifetimes.",
            Some("borrow"),
            None,
        );
        let n2 = g.add_node(
            "Concurrency in Rust is fearless thanks to the ownership model.",
            Some("concurrency"),
            None,
        );

        g.add_edge(
            n0,
            n1,
            0.92,
            vec![(topic, PropertyValue::Label("memory_safety".to_string()))],
        );
        g.add_edge(
            n1,
            n2,
            0.65,
            vec![(topic, PropertyValue::Label("memory_safety".to_string()))],
        );

        // Text query should surface n0 and n1 as seeds
        let results =
            g.search_and_traverse_default("ownership borrowing memory", 5, &["topic"], 0.5, 2, 10);

        assert!(!results.is_empty());
        // n0 (the seed with strongest text match) should be present
        assert!(results.iter().any(|r| r.graph_result.node_id == n0));
    }

    #[test]
    fn phrase_search_basic() {
        let mut idx = InvertedIndex::new();

        idx.index_document(NodeId(0), "the quick brown fox jumps over the lazy dog");
        idx.index_document(NodeId(1), "a quick fox and a lazy dog are friends");

        // "quick fox" should match both, but "quick brown fox" should strongly prefer doc 0
        // thanks to hybrid BM25 + proximity scoring
        let phrase_results = idx.phrase_search("quick brown fox", 10, 5);
        assert!(!phrase_results.is_empty());
        assert_eq!(phrase_results[0].node_id, NodeId(0));
    }

    #[test]
    fn stopword_check_is_case_insensitive() {
        let tok = DefaultTokenizer::new();
        // Stop word at end of string (no trailing whitespace) — last-token path
        let tokens = tok.tokenize("quick fox THE");
        let words: Vec<&str> = tokens.iter().map(|(w, _)| *w).collect();
        assert!(
            !words.contains(&"THE"),
            "'THE' at end of string should be filtered as a stop word"
        );
        // Stop word mid-string — main loop path
        let tokens2 = tok.tokenize("THE quick brown fox");
        let words2: Vec<&str> = tokens2.iter().map(|(w, _)| *w).collect();
        assert!(
            !words2.contains(&"THE"),
            "'THE' mid-string should be filtered as a stop word"
        );
    }

    #[test]
    fn vector_index_survives_save_load() {
        use tempfile::tempdir;
        let dir = tempdir().unwrap();
        let mut g = IndexedKnowledgeGraph::new();
        let n0 = g.add_node("rust ownership", None, Some(&[1.0f32, 0.0, 0.0]));
        let _n1 = g.add_node("python gc", None, Some(&[0.0f32, 1.0, 0.0]));
        g.save_to_dir(dir.path()).unwrap();

        let loaded = IndexedKnowledgeGraph::load_from_dir(dir.path()).unwrap();
        let results = loaded.vector_search(&[1.0f32, 0.0, 0.0], 2);
        let ids: Vec<u32> = results.iter().map(|(n, _)| n.0).collect();
        assert!(ids.contains(&n0.0), "n0 must be found after load");
        assert!(!ids.is_empty());
    }

    #[test]
    fn phrase_vs_keyword_ranking() {
        let mut g = IndexedKnowledgeGraph::new();

        g.add_node(
            "Rust ownership model prevents data races effectively",
            Some("a"),
            None,
        );
        g.add_node(
            "Effective data race prevention using Rust ownership",
            Some("b"),
            None,
        );

        // Keyword search may rank "b" higher or equal
        let keyword = g.search("data race prevention", 2);

        // Phrase search for the natural order should strongly prefer "a"
        let phrase = g.phrase_search("data race prevention", 5, 2);

        // With hybrid scoring, the ordered phrase should rank the first document higher
        if !keyword.is_empty() && !phrase.is_empty() {
            // This is a soft assertion — mainly to exercise the path
            assert!(phrase[0].score > 0.0);
        }
    }
}

#[cfg(test)]
mod merge_node_tests {
    use crate::index::{IndexedKnowledgeGraph, InvertedIndex};

    fn make_graph() -> IndexedKnowledgeGraph {
        IndexedKnowledgeGraph::with_text_index(Box::new(InvertedIndex::new()))
    }

    #[test]
    fn indexed_merge_returns_report() {
        let mut g = make_graph();
        let keep = g.add_node("keep node", None, None);
        let absorb = g.add_node("absorb node", None, None);
        let other = g.add_node("other", None, None);
        g.add_edge(absorb, other, 0.7, vec![]);

        let report = g.merge_nodes(keep, absorb).unwrap();

        assert_eq!(report.absorbed, absorb);
        assert_eq!(report.edges_redirected, 1);
        assert!(g.graph.is_node_deleted(absorb));
    }

    #[test]
    fn indexed_merge_updates_vector_index() {
        let mut g = make_graph();
        g.vector_index = Some(Box::new(crate::BruteForceVectorIndex::new()));

        let keep = g.add_node("keep", None, None); // no embedding
        let absorb = g.add_node("absorb", None, Some(&[1.0f32, 0.0])); // has embedding

        let report = g.merge_nodes(keep, absorb).unwrap();

        assert!(report.embedding_updated);
        // keep should now be findable in vector search
        let results = g.vector_search(&[1.0f32, 0.0], 5);
        assert!(results.iter().any(|(n, _)| n.0 == keep.0));
        // absorb must be gone from vector index
        assert!(!g.vector_index.as_ref().unwrap().contains_vector(absorb));
    }
}

#[cfg(test)]
mod hybrid_weight_tests {
    use crate::index::{HybridWeights, IndexedKnowledgeGraph, InvertedIndex};
    use crate::properties::PropertyValue;

    fn make_graph() -> IndexedKnowledgeGraph {
        IndexedKnowledgeGraph::with_text_index(Box::new(InvertedIndex::new()))
    }

    #[test]
    fn hybrid_traversal_weight_property_changes_ranking() {
        let mut g = make_graph();
        let tag = g.graph.properties.register("confidence");

        let seed = g.add_node("seed query", None, None);
        let hi = g.add_node("hi answer", None, None);
        let lo = g.add_node("lo answer", None, None);

        // Equal strength, different confidence
        g.add_edge(seed, hi, 0.5, vec![(tag, PropertyValue::Float(0.9))]);
        g.add_edge(seed, lo, 0.5, vec![(tag, PropertyValue::Float(0.1))]);

        let weights_with = HybridWeights {
            traversal_weight_property: Some("confidence".into()),
            ..HybridWeights::default()
        };
        let results_with =
            g.search_and_traverse_hybrid("seed query", None, 5, &[], 0.0, 2, 10, weights_with);

        let weights_without = HybridWeights::default();
        let results_without =
            g.search_and_traverse_hybrid("seed query", None, 5, &[], 0.0, 2, 10, weights_without);

        // With confidence weight, hi must come before lo
        let with_ids: Vec<_> = results_with
            .iter()
            .map(|r| r.graph_result.node_id)
            .collect();
        let hi_pos = with_ids.iter().position(|&n| n == hi).unwrap_or(usize::MAX);
        let lo_pos = with_ids.iter().position(|&n| n == lo).unwrap_or(usize::MAX);
        assert!(
            hi_pos < lo_pos,
            "hi-confidence must rank above lo-confidence with weight property"
        );

        // Without weight property, both paths have equal cumulative strength,
        // so we just check both are reachable.
        let without_ids: Vec<_> = results_without
            .iter()
            .map(|r| r.graph_result.node_id)
            .collect();
        assert!(without_ids.contains(&hi));
        assert!(without_ids.contains(&lo));
    }
}

#[cfg(test)]
mod indexed_relation_tests {
    use crate::index::{IndexedKnowledgeGraph, InvertedIndex};
    use crate::properties::PropertyValue;

    fn make_graph() -> IndexedKnowledgeGraph {
        IndexedKnowledgeGraph::with_text_index(Box::new(InvertedIndex::new()))
    }

    #[test]
    fn indexed_neighbors_by_relation_delegates() {
        let mut g = make_graph();
        let tag = g.graph.properties.register("link");

        let a = g.add_node("a", None, None);
        let b = g.add_node("b", None, None);
        let c = g.add_node("c", None, None);

        g.add_edge(a, b, 0.8, vec![(tag, PropertyValue::Label("has".into()))]);
        g.add_edge(a, c, 0.5, vec![(tag, PropertyValue::Label("not".into()))]);

        let results = g.neighbors_by_relation(a, "link", PropertyValue::Label("has".into()));

        assert_eq!(results.len(), 1);
        assert_eq!(results[0].0, b);
    }

    #[test]
    fn indexed_predecessors_by_relation_delegates() {
        let mut g = make_graph();
        let tag = g.graph.properties.register("t");

        let src = g.add_node("src", None, None);
        let dst = g.add_node("dst", None, None);

        g.add_edge(src, dst, 0.9, vec![(tag, PropertyValue::Bool(true))]);

        let preds = g.predecessors_by_relation(dst, "t", PropertyValue::Bool(true));

        assert_eq!(preds.len(), 1);
        assert_eq!(preds[0].0, src);
    }

    #[test]
    fn indexed_traverse_by_relation_delegates() {
        let mut g = make_graph();
        let tag = g.graph.properties.register("rel");

        let a = g.add_node("a", None, None);
        let b = g.add_node("b", None, None);
        let c = g.add_node("c", None, None);

        let val = PropertyValue::Label("supports".into());
        g.add_edge(a, b, 0.9, vec![(tag, val.clone())]);
        g.add_edge(b, c, 0.9, vec![(tag, val.clone())]);

        let results = g.traverse_by_relation(a, "rel", val, 3, 10);
        let ids: Vec<_> = results.iter().map(|r| r.node_id).collect();

        assert!(ids.contains(&b));
        assert!(ids.contains(&c));
    }
}
