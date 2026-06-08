use grap::wire::{decode, encode, WireScalar, WireTable, WireValue};

#[test]
fn round_trip_preserves_rows_and_scalars() {
    let table = WireTable {
        tag: 'e',
        key: "events".to_string(),
        cols: vec!["id".to_string(), "type".to_string()],
        intern_cols: vec![1],
        rows: vec![
            vec![WireValue::Int(1), WireValue::Str("promote".into())],
            vec![WireValue::Int(2), WireValue::Str("promote".into())],
        ],
    };
    let scalars = vec![WireScalar { key: "count".to_string(), value: WireValue::Int(2) }];
    let wire = encode("query_events", "qe1", &scalars, &[table]);
    let doc = decode(&wire);

    assert_eq!(doc.tool, "query_events");
    assert_eq!(doc.enc_id, "qe1");
    let events = &doc.tables["events"];
    assert_eq!(events.len(), 2);
    assert_eq!(events[1]["type"], "promote"); // legend expanded back
    assert_eq!(doc.scalars["count"], "2");
}
