//! Run with `cargo run --release --example rebalance_memory`.
//! Tracks requested heap bytes (not RSS); intentionally text-heavy, sparse tokens.
use std::alloc::{GlobalAlloc, Layout, System};
use std::sync::atomic::{AtomicUsize, Ordering::SeqCst};
use std::time::Instant;
static LIVE: AtomicUsize = AtomicUsize::new(0);
static PEAK: AtomicUsize = AtomicUsize::new(0);
struct Meter;
unsafe impl GlobalAlloc for Meter {
    unsafe fn alloc(&self, layout: Layout) -> *mut u8 {
        let ptr = System.alloc(layout);
        if !ptr.is_null() {
            PEAK.fetch_max(
                LIVE.fetch_add(layout.size(), SeqCst) + layout.size(),
                SeqCst,
            );
        }
        ptr
    }
    unsafe fn dealloc(&self, ptr: *mut u8, layout: Layout) {
        LIVE.fetch_sub(layout.size(), SeqCst);
        System.dealloc(ptr, layout);
    }
}
#[global_allocator]
static ALLOCATOR: Meter = Meter;
fn main() {
    let mut graph = grap::ShardedGraph::new(3);
    for i in 0..256 {
        let text = format!("document {i} {}", " ".repeat(256 * 1024));
        graph.add_node(&text, Some("document")).unwrap();
    }
    let before = LIVE.load(SeqCst);
    PEAK.store(before, SeqCst);
    let start = Instant::now();
    let plan = graph.plan_rebalance(5);
    graph.execute_rebalance(plan).unwrap();
    let elapsed = start.elapsed();
    let peak = PEAK.load(SeqCst);
    let after = LIVE.load(SeqCst);
    println!("nodes=256 before_bytes={before} peak_bytes={peak} after_bytes={after} extra_peak_bytes={} elapsed_ms={:.2}", peak-before, elapsed.as_secs_f64()*1000.0);
}
