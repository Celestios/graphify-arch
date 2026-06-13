# graphify-arch

Architecture enforcement plugin for [graphify](https://github.com/Celestios/graphify). An AI-agent-facing tool that lets agents index code structure, enforce architectural rules defined by the user, and query the resulting graph.

## What This Does

This plugin extends graphify with an architecture layer. When an AI agent runs graphify on a codebase, this plugin:

1. **Indexes code** into a queryable graph database (nodes, edges, metadata)
2. **Applies user-defined rules** — layer constraints, tier boundaries, dependency directions — from a simple JSON config
3. **Audits violations** — detects when code breaks the architectural rules the user specified
4. **Provides context** — compiles subgraph neighborhoods and semantic search for agents to understand code structure

The AI agent reads the user's architectural intent (e.g., "Presentation cannot call Infrastructure directly") and encodes it in `graphify-out/arch/config.json`. The plugin then enforces those rules on every reindex.

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

# 2. The AI agent generates graphify-out/arch/config.json with ontology rules

# 3. Reindex with architecture metadata
graphify arch reindex

# 4. Audit for violations
graphify arch audit

# 5. Query the graph
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
| `graphify arch reindex` | Scan workspace, update DB graph with arch metadata |
| `graphify arch audit` | Audit architectural rules and report violations |
| `graphify arch query-file --path <file>` | Query nodes for a specific file |
| `graphify arch compile-context --target <id>` | Compile subgraph context for LLM input |
| `graphify arch semantic-search --query <text>` | Vector similarity search over code nodes |
| `graphify arch get-dirty-nodes` | List nodes needing summary regeneration |
| `graphify arch update-nodes --payload-file <file>` | Batch update node metadata |

## License

MIT
