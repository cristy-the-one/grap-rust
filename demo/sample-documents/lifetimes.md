# Lifetimes

Lifetimes are annotations that describe how long references are valid.

They are a key part of how the borrow checker proves that your code is memory safe.

Most of the time the compiler can infer lifetimes (lifetime elision), but explicit annotations are sometimes required in function signatures.
