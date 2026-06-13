# graphify-arch

Architecture enforcement plugin for [graphify](https://github.com/Celestios/graphify). An AI-agent-facing tool that lets agents index code structure, enforce architectural rules defined by the user, and query the resulting graph.

## What This Does

This plugin extends graphify with an architecture layer. When an AI agent runs graphify on a codebase, this plugin:

1. **Indexes code** into a queryable graph database (nodes, edges, metadata)
2. **Applies user-defined rules** — layer constraints, tier boundaries, dependency directions — from a simple JSON config
3. **Audits violations** — detects when code breaks the architectural rules the user specified
4. **Provides context** — compiles subgraph neighborhoods and semantic search for agents to understand code structure

The AI agent reads the user's architectural intent (e.g., "Presentation cannot call Infrastructure directly") and encodes it in `graphify-out/arch/config.json`. The plugin then enforces those rules on every reindex.

## Installation

```bash
# Recommended: install graphify fork with arch plugin included
uv tool install --from "git+https://github.com/Celestios/graphify.git" "graphifyy[arch]"
```

Or install separately:

```bash
# Install graphify fork
uv tool install --from "git+https://github.com/Celestios/graphify.git" graphifyy

# Install arch plugin in the same environment
uv pip install --python "$(uv tool dir)/graphifyy/Scripts/python.exe" "git+https://github.com/Celestios/graphify-arch.git"
```

## Quick Start

```bash
# 1. Run graphify on your project to generate the graph
graphify extract /path/to/your/project

# 2. The AI agent generates graphify-out/arch/config.json with ontology rules

# 3. Audit for violations
graphify arch audit

# 4. Query the graph
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

The AI agent should discuss architectural rules with you before writing this file. See `references/config.md` for the full schema.

## CLI Commands

| Command | Description |
|---------|-------------|
| `graphify arch` | Show help and available commands |
| `graphify arch audit` | Check all files for architectural violations |
| `graphify arch set-status <file> <status> [msg]` | Override compliance status for a file |
| `graphify arch set-status-bulk <json-file>` | Bulk-set manual status overrides |
| `graphify arch analyze` | Validate ontology config syntax and consistency |
| `graphify arch setup-embeddings` | Download ONNX embedding model for semantic search |

## License

MIT
