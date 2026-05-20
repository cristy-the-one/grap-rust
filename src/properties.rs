//! Property registry, values, filters and 64-bit bitmask support.
//! Directly ported from core_graph_design.md Sections 1 & 4.

use std::collections::HashMap;

use crate::ids::PropertyTag;

#[derive(Debug, Clone, PartialEq)]
pub enum PropertyValue {
    Float(f64),
    Int(i64),
    Bool(bool),
    /// Short human-readable labels (topics, relation types, etc.).
    /// For v1 we use String; the design used a CompactString simulation.
    Label(String),
}

#[derive(Debug, Clone)]
pub enum PropertyFilter {
    Exists,
    Equals(PropertyValue),
    Range { min: f64, max: f64 },
    MinStrength(f64),
}

/// Bidirectional name <-> u16 tag registry. Max 64 tags (bitmask limit).
#[derive(Debug, Default)]
pub struct PropertyRegistry {
    pub(crate) name_to_tag: HashMap<String, PropertyTag>,
    pub(crate) tag_to_name: Vec<String>,
}

impl PropertyRegistry {
    pub fn new() -> Self {
        Self::default()
    }

    /// Register a property name. Idempotent. Panics if >64 (per design).
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

    pub fn get_tag(&self, name: &str) -> Option<PropertyTag> {
        self.name_to_tag.get(name).copied()
    }

    pub fn get_name(&self, tag: PropertyTag) -> &str {
        &self.tag_to_name[tag.0 as usize]
    }

    /// Build a u64 mask from a list of property names (for GraphQuery).
    pub fn build_mask(&self, names: &[&str]) -> u64 {
        let mut mask = 0u64;
        for name in names {
            if let Some(tag) = self.get_tag(name) {
                mask |= 1u64 << tag.0;
            }
        }
        mask
    }

    /// Internal: get the ordered list of property names for serialization.
    pub(crate) fn property_names(&self) -> &[String] {
        &self.tag_to_name
    }
}
