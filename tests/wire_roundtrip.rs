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

#[test]
fn round_trip_two_tables_mirrors_graph_neighbors() {
    // Mirrors the wired graph_neighbors shape: two tables + scalars.
    let neighbors = WireTable {
        tag: 'n',
        key: "neighbors".to_string(),
        cols: vec![
            "id".to_string(),
            "label".to_string(),
            "kind".to_string(),
            "title".to_string(),
        ],
        intern_cols: vec![2], // intern "kind"
        rows: vec![
            vec![
                WireValue::Int(7),
                WireValue::Str("Brake".into()),
                WireValue::Str("act".into()),
                WireValue::Str("alpha".into()),
            ],
            vec![
                WireValue::Int(9),
                WireValue::Str("Signal".into()),
                WireValue::Str("act".into()),
                WireValue::Null, // null title
            ],
        ],
    };
    let edges = WireTable {
        tag: 'e',
        key: "edges".to_string(),
        cols: vec!["source".to_string(), "target".to_string(), "type".to_string()],
        intern_cols: vec![2], // intern "type"
        rows: vec![
            vec![WireValue::Int(5), WireValue::Int(7), WireValue::Str("cites".into())],
            vec![WireValue::Int(5), WireValue::Int(9), WireValue::Str("cites".into())],
        ],
    };
    let scalars = vec![
        WireScalar { key: "status".to_string(), value: WireValue::Str("ok".into()) },
        WireScalar { key: "center".to_string(), value: WireValue::Int(5) },
    ];
    let wire = encode("graph_neighbors", "gn1", &scalars, &[neighbors, edges]);
    let doc = decode(&wire);

    // Both tables reconstruct with the right row counts.
    assert_eq!(doc.tables["neighbors"].len(), 2);
    assert_eq!(doc.tables["edges"].len(), 2);
    // Interned cells decode back to their literal.
    assert_eq!(doc.tables["neighbors"][0]["kind"], "act");
    assert_eq!(doc.tables["neighbors"][1]["kind"], "act");
    assert_eq!(doc.tables["edges"][0]["type"], "cites");
    // Null title decodes to the empty string.
    assert_eq!(doc.tables["neighbors"][1]["title"], "");
    // The Int scalar decodes back to its string form.
    assert_eq!(doc.scalars["center"], "5");
    assert_eq!(doc.scalars["status"], "ok");
}
