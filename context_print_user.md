# Dynamic Architectural Context Compilation
Generated deterministically by Celial Graph Engine.

## Architectural Context Bounds
- **Focal Target**: `.\test_samples\main.rs::print_user`
- **Search Direction**: `downstream`
- **Traversed Radius**: `1` hops
- **Resolution Profile**: `full`
- **Total Resolved Nodes**: `1`

---
## 1. Focal Core Target Node
- **Symbol ID**: `.\test_samples\main.rs::print_user`
- **Filepath**: `.\test_samples\main.rs`
- **AST Type**: `Method`
- **Semantic Facets**: `[Layer: "Tier3Domain", Role: "Unknown", Pattern: "None", Purity: "Unknown", Barrier: false]`
- **AI Summary**: _No cached AI summary available._

#### Active Code Block:
```rust
pub fn print_user(user: &User) {
    println!("User: {} ({})", user.name, user.id);
}
```

---
## 2. Traversed Subgraph Neighbors
