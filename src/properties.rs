//! Property system: typed values, filters, and the 64-property bitmask registry.
//!
//! Every edge in the graph can carry up to 64 distinct property types. Each
//! property is assigned a dense `u16` tag by the [`PropertyRegistry`]. These
//! tags are used to construct `u64` bitmasks that enable extremely fast
//! edge filtering during traversal (O(1) per node via bitwise AND).
//!
//! This design is a core performance foundation of the engine (see
//! `core_graph_design.md` Sections 1 and 4).

use std::collections::HashMap;

use crate::ids::PropertyTag;

/// A typed property value attached to an edge.
///
/// Values are intentionally kept small. For large or structured data, store
/// the bulk payload in the node's text blob and use properties only for
/// fast filtering dimensions (topics, categories, confidence scores, etc.).
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum PropertyValue {
    /// 64-bit floating point (confidence scores, weights, etc.).
    Float(f64),
    /// 64-bit signed integer.
    Int(i64),
    /// Boolean flag.
    Bool(bool),
    /// Short human-readable label (e.g. `"memory_safety"`, `"core_concept"`).
    ///
    /// For v1 we use `String`. The original design document used a
    /// `CompactString` simulation; a `compact_str` crate can be swapped in
    /// later if profiling shows this to be a bottleneck.
    Label(String),
}

/// A filter that can be applied to edge properties during traversal.
///
/// Currently the filter list is used to build the required bitmask.
/// More sophisticated runtime filtering can be added later.
#[derive(Debug, Clone)]
pub enum PropertyFilter {
    /// The property must exist on the edge.
    Exists,
    /// The property must equal the given value.
    Equals(PropertyValue),
    /// Numeric range filter (currently only meaningful for `Float`/`Int`).
    Range {
        /// Lower bound (inclusive).
        min: f64,
        /// Upper bound (inclusive).
        max: f64,
    },
    /// Minimum strength threshold (used for edge pruning).
    MinStrength(f64),
}

/// A bidirectional registry that maps human-readable property names to
/// dense `u16` tags (0..63).
///
/// The registry is the single source of truth for which properties exist
/// in a graph. Registering the same name twice returns the same tag
/// (idempotent). Attempting to register more than 64 distinct names will
/// panic (the hard architectural limit imposed by the 64-bit mask).
///
/// Typical usage:
///
/// ```rust
/// use grap::KnowledgeGraph;
///
/// let mut g = KnowledgeGraph::new();
/// let topic = g.properties.register("topic");
/// let confidence = g.properties.register("confidence");
/// ```
#[derive(Debug, Default)]
pub struct PropertyRegistry {
    pub(crate) name_to_tag: HashMap<String, PropertyTag>,
    pub(crate) tag_to_name: Vec<String>,
}

impl PropertyRegistry {
    /// Creates an empty property registry.
    pub fn new() -> Self {
        Self::default()
    }

    /// Registers a property name and returns its dense tag.
    ///
    /// The operation is idempotent: registering the same name twice returns
    /// the previously assigned tag. Names are case-sensitive.
    ///
    /// # Panics
    ///
    /// Panics if the registry already contains 64 distinct properties
    /// (the hard limit of the 64-bit edge mask).
    pub fn register(&mut self, name: &str) -> PropertyTag {
        if let Some(&tag) = self.name_to_tag.get(name) {
            return tag;
        }
        assert!(
            self.tag_to_name.len() < 64,
            "Max 64 property types (bitmask limit). See core_graph_design.md:308"
        );
        let tag = PropertyTag(self.tag_to_name.len() as u16);
        self.tag_to_name.push(name.to_string());
        self.name_to_tag.insert(name.to_string(), tag);
        tag
    }

    /// Looks up the tag for a previously registered property name.
    pub fn get_tag(&self, name: &str) -> Option<PropertyTag> {
        self.name_to_tag.get(name).copied()
    }

    /// Returns the human-readable name for a given tag.
    ///
    /// # Panics
    ///
    /// Panics if the tag was not issued by this registry.
    pub fn get_name(&self, tag: PropertyTag) -> &str {
        &self.tag_to_name[tag.0 as usize]
    }

    /// Builds a `u64` bitmask from a list of property names.
    ///
    /// Any names not present in the registry are silently ignored.
    /// The resulting mask can be passed to `QueryBuilder::require_properties`.
    pub fn build_mask(&self, names: &[&str]) -> u64 {
        let mut mask = 0u64;
        for name in names {
            if let Some(tag) = self.get_tag(name) {
                mask |= 1u64 << tag.0;
            }
        }
        mask
    }

    /// Returns the ordered list of all registered property names.
    ///
    /// Used internally during serialization.
    pub(crate) fn property_names(&self) -> &[String] {
        &self.tag_to_name
    }

    /// Like `register` but returns `Err(GraphError::TooManyProperties)` instead of panicking
    /// when the 64-property limit is reached.
    pub fn try_register(&mut self, name: &str) -> Result<PropertyTag, crate::GraphError> {
        if let Some(&tag) = self.name_to_tag.get(name) {
            return Ok(tag);
        }
        if self.tag_to_name.len() >= 64 {
            return Err(crate::GraphError::TooManyProperties);
        }
        let tag = PropertyTag(self.tag_to_name.len() as u16);
        self.tag_to_name.push(name.to_string());
        self.name_to_tag.insert(name.to_string(), tag);
        Ok(tag)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn try_register_returns_error_at_limit() {
        let mut reg = PropertyRegistry::new();
        for i in 0..64 {
            reg.register(&format!("prop_{}", i));
        }
        // 65th registration must return Err
        let result = reg.try_register("one_too_many");
        assert!(
            result.is_err(),
            "should return Err at the 64-property limit"
        );
    }

    #[test]
    fn try_register_is_idempotent() {
        let mut reg = PropertyRegistry::new();
        let t1 = reg.try_register("topic").unwrap();
        let t2 = reg.try_register("topic").unwrap();
        assert_eq!(t1, t2);
    }
}
