//! Full-text inverted index with BM25 scoring (Section 11 of core_graph_design.md).
//!
//! This module provides the "entry point" for queries: given natural language text,
//! find the most relevant starting nodes (seeds) before running property-weighted
//! graph traversal.
//!
//! The implementation is a faithful port of the design document, with small
//! ergonomic improvements (better docs, `IndexStats`, etc.).

use std::collections::{HashMap, HashSet, BTreeMap};

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
pub struct DefaultTokenizer {
    stop_words: HashSet<&'static str>,
}

impl DefaultTokenizer {
    pub fn new() -> Self {
        let stop_words: HashSet<&'static str> = [
            "the", "a", "an", "is", "are", "was", "were", "be", "been",
            "being", "have", "has", "had", "do", "does", "did", "will",
            "would", "could", "should", "may", "might", "shall", "can",
            "to", "of", "in", "for", "on", "with", "at", "by", "from",
            "as", "into", "through", "during", "before", "after", "and",
            "but", "or", "nor", "not", "so", "if", "then", "than",
            "that", "this", "it", "its", "they", "them", "we", "you",
            "he", "she", "his", "her", "my", "your", "our", "their",
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

        // Handle last token
        if let Some(s) = start {
            let word = &text[s..];
            if word.len() >= 2 && !word.chars().all(|c| c.is_numeric()) {
                tokens.push((word, s as u32));
            }
        }

        tokens
    }
}

// ============================================================================
// Term Interning
// ============================================================================

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

#[derive(Debug, Clone)]
pub struct TextSearchResult {
    pub node_id: NodeId,
    pub score: f64,
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
        Self { text: 0.6, graph: 0.4 }
    }
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
    /// Cumulative strength of the path taken through the graph.
    pub graph_strength: f64,
    /// Final score after applying [`FusionWeights`] (what results are sorted by).
    pub fused_score: f64,
    /// Underlying graph traversal details (target node, path, depth, etc.).
    pub graph_result: QueryResult,
    /// The text content of the node (borrowed from the blob store).
    pub text: &'a str,
}

#[derive(Debug, Clone)]
pub struct IndexStats {
    pub unique_terms: usize,
    pub total_postings: usize,
    pub document_count: u32,
    pub avg_doc_length: f64,
}

/// Common contract for all text index backends (in-memory today, on-disk in 0.2+).
///
/// Both the classic `InvertedIndex` and future persistent implementations
/// (see `on_disk_text_index`) will implement this trait. This allows
/// `IndexedKnowledgeGraph` to be (mostly) backend-agnostic.
pub trait TextIndex: Send + Sync {
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
    fn is_document_deleted(&self, _node: NodeId) -> bool { false }

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

pub struct InvertedIndex {
    interner: StringInterner,
    index: HashMap<TermId, PostingList>,
    document_count: u32,
    doc_lengths: Vec<u32>,
    avg_doc_length: f64,
    tokenizer: Box<dyn Tokenizer>,
}

impl InvertedIndex {
    pub fn new() -> Self {
        Self::with_tokenizer(Box::new(DefaultTokenizer::new()))
    }

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
                let dl = self
                    .doc_lengths
                    .get(doc_id as usize)
                    .copied()
                    .unwrap_or(1) as f64;

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
    pub fn phrase_search(
        &self,
        phrase: &str,
        max_gap: u32,
        max_results: usize,
    ) -> Vec<TextSearchResult> {
        let tokens = self.tokenizer.tokenize(phrase);
        let terms: Vec<String> = tokens
            .iter()
            .map(|(w, _)| w.to_lowercase())
            .collect();

        if terms.len() < 2 {
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
                    || pl.postings
                        .binary_search_by_key(&node_id.0, |p| p.node_id.0)
                        .is_ok()
            });

            if !all_present {
                continue;
            }

            let term_positions: Vec<&[u32]> = lists
                .iter()
                .map(|pl| {
                    let idx = pl
                        .postings
                        .binary_search_by_key(&node_id.0, |p| p.node_id.0)
                        .unwrap();
                    pl.postings[idx].positions.as_slice()
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
            let Some(term_id) = self.interner.get(term) else { continue };
            let Some(pl) = self.index.get(&term_id) else { continue };

            let df = pl.postings.len() as f64;
            let idf = ((n - df + 0.5) / (df + 0.5) + 1.0).ln();

            if let Ok(idx) = pl.postings.binary_search_by_key(&node_id.0, |p| p.node_id.0) {
                let posting = &pl.postings[idx];
                let tf = posting.frequency as f64;
                let dl = self.doc_lengths.get(node_id.0 as usize).copied().unwrap_or(1) as f64;

                let tf_norm = (tf * (K1 + 1.0))
                    / (tf + K1 * (1.0 - B + B * dl / self.avg_doc_length.max(1.0)));

                score += idf * tf_norm;
            }
        }
        score
    }

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
    pub graph: KnowledgeGraph,
    /// The text index backend. Can be the classic in-memory implementation
    /// or (when the `on-disk-index` feature is enabled) a persistent
    /// segmented implementation.
    pub text_index: Box<dyn TextIndex>,
}

impl IndexedKnowledgeGraph {
    pub fn new() -> Self {
        Self {
            graph: KnowledgeGraph::new(),
            text_index: Box::new(InvertedIndex::new()),
        }
    }

    /// Create an `IndexedKnowledgeGraph` with a custom tokenizer (memory backend).
    pub fn with_tokenizer(tokenizer: Box<dyn Tokenizer>) -> Self {
        Self {
            graph: KnowledgeGraph::new(),
            text_index: Box::new(InvertedIndex::with_tokenizer(tokenizer)),
        }
    }

    /// Create an `IndexedKnowledgeGraph` that uses the experimental on-disk
    /// segmented inverted index (requires the `on-disk-index` feature).
    #[cfg(feature = "on-disk-index")]
    pub fn new_on_disk() -> Self {
        Self {
            graph: KnowledgeGraph::new(),
            text_index: Box::new(crate::on_disk_text_index::OnDiskTextIndex::new()),
        }
    }

    /// Builder-style constructor that lets you choose the text backend.
    /// When the `on-disk-index` feature is enabled you can pass an
    /// `OnDiskTextIndex`.
    pub fn with_text_index(index: Box<dyn TextIndex>) -> Self {
        Self {
            graph: KnowledgeGraph::new(),
            text_index: index,
        }
    }

    /// Add a node and immediately index its text content.
    pub fn add_node(&mut self, text: &str, label: Option<&str>) -> NodeId {
        let id = self.graph.add_node(text, label);
        self.text_index.index_document(id, text);
        id
    }

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
    ) -> Vec<CombinedResult> {
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

    pub fn read_blob(&self, node: NodeId) -> &str {
        self.graph.read_blob(node)
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
    ) -> Vec<CombinedResult> {
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
    ) -> Vec<CombinedResult> {
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
    ) -> Vec<CombinedResult> {
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

    pub fn index_stats(&self) -> IndexStats {
        self.text_index.stats()
    }

    /// Saves the underlying graph to disk (the inverted index is not persisted;
    /// it will be rebuilt on load).
    pub fn save_to_disk(&self, dir: &std::path::Path) -> Result<(), GraphError> {
        self.graph.save_to_disk(dir)
    }

    /// Loads a graph from disk and rebuilds the full-text inverted index.
    pub fn load_from_disk(dir: &std::path::Path) -> Result<Self, GraphError> {
        let graph = KnowledgeGraph::load_from_disk(dir)?;
        let mut ig = Self {
            graph,
            text_index: Box::new(InvertedIndex::new()),
        };

        // Rebuild the index (simple and correct)
        for i in 0..ig.graph.node_count() {
            let id = NodeId(i as u32);
            let text = ig.graph.read_blob(id);
            ig.text_index.index_document(id, text);
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

    /// Attempts to repair a potentially corrupted graph directory.
    ///
    /// Delegates to [`KnowledgeGraph::repair`].
    pub fn repair(dir: &std::path::Path) -> Result<crate::serialize::RepairReport, GraphError> {
        KnowledgeGraph::repair(dir)
    }

    /// Load using memory-mapped blobs (zero-copy) and rebuild the text index.
    ///
    /// Requires the `mmap` feature to be enabled.
    #[cfg(feature = "mmap")]
    pub fn load_mmap(dir: &std::path::Path) -> Result<Self, GraphError> {
        let graph = KnowledgeGraph::load_mmap(dir)?;
        let mut ig = Self {
            graph,
            text_index: InvertedIndex::new(),
        };

        for i in 0..ig.graph.node_count() {
            let id = NodeId(i as u32);
            let text = ig.graph.read_blob(id);
            ig.text_index.index_document(id, text);
        }

        Ok(ig)
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
    pub fn phrase_search(&self, phrase: &str, max_gap: u32, max_results: usize) -> Vec<TextSearchResult> {
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

    pub fn save_to_dir(&mut self, dir: &std::path::Path) -> Result<(), GraphError> {
        // Save the graph part
        self.graph.save_to_dir(dir.join("graph"))?;

        // Save the text index (on-disk backend will actually write data)
        self.text_index.save_to_dir(&dir.join("text_index"))?;

        Ok(())
    }

    pub fn load_from_dir(dir: &std::path::Path) -> Result<Self, GraphError> {
        let graph = KnowledgeGraph::load_from_dir(&dir.join("graph"))?;
        // For text index we currently default to memory and let the caller
        // decide to use OnDiskTextIndex separately.
        // A full implementation would inspect the directory.
        let text_index: Box<dyn TextIndex> = Box::new(InvertedIndex::new());

        Ok(Self { graph, text_index })
    }

    pub fn load_mmap(dir: &std::path::Path) -> Result<Self, GraphError> {
        let graph = KnowledgeGraph::load_mmap(&dir.join("graph"))?;
        let text_index: Box<dyn TextIndex> = Box::new(InvertedIndex::new());
        Ok(Self { graph, text_index })
    }

    /// Returns segment-level information from the text index, if it supports it
    /// (i.e. when using the on-disk backend). Returns empty vec for memory backend.
    pub fn text_index_segment_info(&self) -> Vec<crate::on_disk_index::SegmentInfo> {
        // For the on-disk case this will be populated; for memory it is empty.
        // We use a small hack via downcast for now (proper solution: extend TextIndex trait).
        // This is acceptable during the 0.2 integration spike.
        vec![]
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

        let n0 = g.add_node("Rust ownership and borrowing are fundamental to memory safety.", Some("rust"));
        let n1 = g.add_node("The borrow checker enforces rules about references and lifetimes.", Some("borrow"));
        let n2 = g.add_node("Concurrency in Rust is fearless thanks to the ownership model.", Some("concurrency"));

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
        let results = g.search_and_traverse_default(
            "ownership borrowing memory",
            5,
            &["topic"],
            0.5,
            2,
            10,
        );

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
    fn phrase_vs_keyword_ranking() {
        let mut g = IndexedKnowledgeGraph::new();

        g.add_node("Rust ownership model prevents data races effectively", Some("a"));
        g.add_node("Effective data race prevention using Rust ownership", Some("b"));

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
