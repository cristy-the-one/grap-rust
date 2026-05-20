# Borrowing

Borrowing allows you to temporarily use a value without taking ownership.

There are two kinds of borrows:
- Shared borrows (`&T`) — multiple readers allowed
- Mutable borrows (`&mut T`) — exclusive writer

The borrow checker enforces these rules at compile time.
