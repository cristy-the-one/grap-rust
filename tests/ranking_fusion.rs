use grap::FusionMode;
use grap::{HybridWeights, IndexedKnowledgeGraph};

#[test]
fn rrf_and_linear_both_return_results() {
    let mut g = IndexedKnowledgeGraph::new();
    let a = g.add_node("rust memory safety ownership", Some("A"), None);
    let b = g.add_node("garbage collection tradeoffs", Some("B"), None);
    let c = g.add_node("memory safety without gc", Some("C"), None);
    g.add_edge(a, c, 0.9, vec![]);
    g.add_edge(b, c, 0.2, vec![]);

    let mut w = HybridWeights::default();
    w.mode = FusionMode::Rrf { k: 60.0 };
    let rrf = g.search_and_traverse_hybrid("memory safety", None, 5, &[], 0.0, 2, 10, w);
    assert!(!rrf.is_empty());

    let mut wl = HybridWeights::default();
    wl.mode = FusionMode::Linear;
    let lin = g.search_and_traverse_hybrid("memory safety", None, 5, &[], 0.0, 2, 10, wl);
    assert!(!lin.is_empty());
}
