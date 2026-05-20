# Ownership in Rust

The ownership system is the foundation of Rust's safety guarantees.

Every value in Rust has exactly one owner. When the owner goes out of scope, the value is dropped.

This eliminates entire classes of bugs:
- Use-after-free
- Double-free
- Data races (when combined with the type system)
