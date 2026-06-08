# Dynamic Architectural Context Compilation
Generated deterministically by Celial Graph Engine.

## Architectural Context Bounds
- **Focal Target**: `.\test_samples\main.rs`
- **Search Direction**: `downstream`
- **Traversed Radius**: `1` hops
- **Resolution Profile**: `full`
- **Total Resolved Nodes**: `4`

---
## 1. Focal Core Target Node
- **Symbol ID**: `.\test_samples\main.rs`
- **Filepath**: `.\test_samples\main.rs`
- **AST Type**: `File`
- **Semantic Facets**: `[Layer: "Tier3Domain", Role: "Unknown", Pattern: "None", Purity: "Unknown", Barrier: false]`
- **AI Summary**: _No cached AI summary available._

#### Active Code Block:
```rust
pub struct UserRepository {
    users: Vec<User>,
}

impl UserRepository {
    pub fn new() -> Self {
        Self { users: Vec::new() }
    }

    pub fn add_user(&mut self, user: User) {
        self.users.push(user);
    }

    pub fn find_by_id(&self, id: u32) -> Option<&User> {
        self.users.iter().find(|u| u.id == id)
    }
}

pub struct User {
    pub id: u32,
    pub name: String,
}

pub fn print_user(user: &User) {
    println!("User: {} ({})", user.name, user.id);
}

```

---
## 2. Traversed Subgraph Neighbors
### Neighbor #1: `.\test_samples\main.rs::User`
- **Symbol ID**: `.\test_samples\main.rs::User`
- **Filepath**: `.\test_samples\main.rs`
- **AST Type**: `Struct`
- **Semantic Facets**: `[Layer: "Tier3Domain", Role: "Unknown", Pattern: "None", Purity: "Unknown", Barrier: false]`
- **AI Summary**: _No cached AI summary available._

#### Active Code Block:
```rust
pub struct User {
    pub id: u32,
    pub name: String,
}
```

### Neighbor #2: `.\test_samples\main.rs::UserRepository`
- **Symbol ID**: `.\test_samples\main.rs::UserRepository`
- **Filepath**: `.\test_samples\main.rs`
- **AST Type**: `Struct`
- **Semantic Facets**: `[Layer: "Tier3Domain", Role: "Unknown", Pattern: "None", Purity: "Unknown", Barrier: false]`
- **AI Summary**: _No cached AI summary available._

#### Active Code Block:
```rust
pub struct UserRepository {
    users: Vec<User>,
}
```

### Neighbor #3: `.\test_samples\main.rs::print_user`
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

