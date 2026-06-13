# graphify-arch

Architecture enforcement plugin for [graphify](https://github.com/Celestios/graphify). Indexes code structure into a queryable graph database, enforces architectural layer/tier constraints, and provides semantic search over your codebase.

## Features

- **Ontology-driven metadata** — Define manual and automatic fields per directory (layer, tier, architectural role, purity, etc.) in a JSON config
- **Dependency rule enforcement** — Audit layer violations (e.g., Presentation calling Infrastructure) and tier violations
- **Semantic propagation** — Purity and barrier-based metadata propagation across the call graph
- **Weight-aware clustering** — Edge weights reflect architectural relationships for better community detection
- **Semantic search** — Vector similarity search over code nodes using local ONNX embeddings
- **Context compilation** — Extract subgraph neighborhoods around any node for LLM context
- **MCP resource integration** — Arch reports exposed as MCP resources in the graphify server

## Requirements

- Python >= 3.10
- [graphify](https://github.com/Celestios/graphify) (custom fork with plugin hooks)

## Installation

```bash
# Install graphify fork first
git clone https://github.com/Celestios/graphify.git
cd graphify
pip install -e .

# Install graphify-arch
git clone https://github.com/Celestios/graphify-arch.git
cd graphify-arch
pip install -e .
```

## Quick Start

```bash
# 1. Run graphify on your project to generate the graph
graphify extract /path/to/your/project

# 2. Initialize the arch config
graphify arch init

# 3. Edit graphify-out/arch/config.json to define your ontology

# 4. Reindex with architecture metadata
graphify arch reindex

# 5. Audit for violations
graphify arch audit

# 6. Query the graph
graphify arch query-file --path src/my_module.py --methods
```

## Ontology Config

Place `graphify-out/arch/config.json` in your project. Define per-directory fields:

```json
{
  "lib": {
    "manual_fields": {
      "pattern": {
        "values": ["Data Model", "Controller", "None"],
        "default": "None"
      }
    },
    "automatic_fields": {
      "layer": {
        "values": ["Presentation", "Application", "Domain", "Infrastructure"],
        "default": "Domain",
        "assignment_rules": [
          {"value": "Presentation", "conditions": {"path_prefix": "lib/ui"}}
        ],
        "handler": "dependency_check",
        "rules": [
          {"source": "Presentation", "target": "Infrastructure", "message": "Presentation cannot call Infrastructure"}
        ]
      }
    }
  }
}
```

## CLI Commands

| Command | Description |
|---------|-------------|
| `graphify arch init` | Bootstrap workspace and generate config |
| `graphify arch reindex` | Scan workspace, update DB graph with arch metadata |
| `graphify arch audit` | Audit architectural rules and report violations |
| `graphify arch query-file --path <file>` | Query nodes for a specific file |
| `graphify arch compile-context --target <id>` | Compile subgraph context for LLM input |
| `graphify arch semantic-search --query <text>` | Vector similarity search over code nodes |
| `graphify arch get-dirty-nodes` | List nodes needing summary regeneration |
| `graphify arch update-nodes --payload-file <file>` | Batch update node metadata |

## License

MIT
