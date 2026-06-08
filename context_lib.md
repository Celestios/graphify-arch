# Dynamic Architectural Context Compilation
Generated deterministically by Celial Graph Engine.

## Architectural Context Bounds
- **Focal Target**: `.\test_samples\lib.rs`
- **Search Direction**: `downstream`
- **Traversed Radius**: `1` hops
- **Resolution Profile**: `full`
- **Total Resolved Nodes**: `2`

---
## 1. Focal Core Target Node
- **Symbol ID**: `.\test_samples\lib.rs`
- **Filepath**: `.\test_samples\lib.rs`
- **AST Type**: `File`
- **Semantic Facets**: `[Layer: "Tier3Domain", Role: "Unknown", Pattern: "None", Purity: "Unknown", Barrier: false]`
- **AI Summary**: _No cached AI summary available._

#### Active Code Block:
```rust
pub mod data;
pub mod controller;

pub fn boot() {
    controller::action();
}
```

---
## 2. Traversed Subgraph Neighbors
### Neighbor #1: `.\test_samples\lib.rs::boot`
- **Symbol ID**: `.\test_samples\lib.rs::boot`
- **Filepath**: `.\test_samples\lib.rs`
- **AST Type**: `Method`
- **Semantic Facets**: `[Layer: "Tier3Domain", Role: "Unknown", Pattern: "None", Purity: "Unknown", Barrier: false]`
- **AI Summary**: _No cached AI summary available._

#### Active Code Block:
```rust
pub fn boot() {
    controller::action();
}
```

