# graphify-arch reference: config.json

Load this when the AI agent needs to generate or edit `graphify-out/arch/config.json`.

## Overview

`config.json` defines architectural rules for the project. The AI agent analyzes the codebase structure and creates this file. The plugin then enforces the rules on every `graphify arch audit` run.

Config lives at: `<project>/graphify-out/arch/config.json`

## Top-level structure

```json
{
  "<directory-prefix>": {
    "manual_fields": { ... },
    "automatic_fields": { ... }
  }
}
```

Each top-level key is a **directory prefix** (relative to project root). Fields apply to all source files under that prefix. Use `"."` for root-level files.

Multiple prefixes can coexist — the most specific (longest path) wins.

## Field types

### Manual fields

Agent-guided metadata. The AI reads code and sets values.

```json
"manual_fields": {
  "ai_summary": {
    "default": "",
    "reset_on_change": true
  },
  "pattern": {
    "values": ["Data Model", "Controller", "Service", "None"],
    "default": "None",
    "reset_on_change": false
  }
}
```

| Property | Required | Description |
|----------|----------|-------------|
| `default` | yes | Value when no override exists |
| `values` | no | Allowed values array. Omit for free-text fields |
| `reset_on_change` | no | If `true`, resets to default when file's AST changes (default: `false`) |

**Rules for manual fields:**
- Cannot have `assignment_rules` (they are set by the agent, not by rules)
- `default` must be in `values` if `values` is specified
- Use `reset_on_change: true` for fields that depend on code content (e.g., `ai_summary`)
- Use `reset_on_change: false` for stable classifications (e.g., `pattern`)

### Automatic fields

Rule-assigned metadata. Computed from path patterns, imports, and calls.

```json
"automatic_fields": {
  "layer": {
    "values": ["Presentation", "Application", "Domain", "Infrastructure"],
    "default": "Domain",
    "assignment_rules": [ ... ],
    "handler": "dependency_check",
    "rules": [ ... ]
  }
}
```

| Property | Required | Description |
|----------|----------|-------------|
| `default` | yes | Value when no assignment rule matches |
| `values` | no | Allowed values. Must include all values used in `assignment_rules` and `rules` |
| `assignment_rules` | no | Rules that assign values based on conditions |
| `handler` | no | `"dependency_check"` or `"propagation"` |
| `rules` | no | Dependency constraints (validated during audit) |
| `reset_on_change` | no | If `true`, resets to default when AST changes |

## Assignment rules

Checked in order. **First match wins.**

```json
"assignment_rules": [
  {
    "value": "Presentation",
    "conditions": {
      "path_prefix": "lib/features/graph/ui"
    }
  },
  {
    "value": "Controller",
    "conditions": {
      "path_prefix": "lib/features/graph/store",
      "class_contains": "Controller"
    }
  }
]
```

### Conditions

All conditions in a rule must match (AND logic). Omit any condition to skip that check.

| Condition | Matches when... |
|-----------|----------------|
| `path_prefix` | File's relative path starts with this string |
| `class_suffix` | Node label (class/function name) ends with this string |
| `class_contains` | Node label contains this string |
| `name_contains` | Node label contains this string |
| `imports_prefix` | Node imports from a path starting with this prefix |
| `calls_prefix` | Node calls a function from a path starting with this prefix |

**Examples:**

Path-only rule — all files under `lib/ui/`:
```json
{"value": "Presentation", "conditions": {"path_prefix": "lib/ui"}}
```

Path + class name — files under `store/` with "Controller" in name:
```json
{"value": "Controller", "conditions": {"path_prefix": "lib/store", "class_contains": "Controller"}}
```

Import-based — files that import from Rust FFI:
```json
{"value": true, "conditions": {"imports_prefix": "lib/src/rust/domain"}}
```

Call-based — files that call into infrastructure:
```json
{"value": "Infrastructure", "conditions": {"calls_prefix": "lib/infrastructure"}}
```

## Handlers

### `dependency_check`

Validates that nodes with a given field value don't depend on forbidden values.

```json
{
  "handler": "dependency_check",
  "rules": [
    {
      "source": "Presentation",
      "target": "Infrastructure",
      "message": "Presentation cannot call Infrastructure"
    }
  ]
}
```

Each rule:
- `source` — value of the calling node's field
- `target` — value of the called node's field
- `message` — violation message shown in audit
- `severity` — optional, defaults to `"error"`

### `propagation`

Propagates field values across the call graph with barriers.

```json
{
  "handler": "propagation",
  "barriers": ["Repository"],
  "barrier_field": "architectural_role",
  "weights": {"Pure": 1, "StateMutator": 2, "IoBound": 3, "Unknown": 0}
}
```

| Property | Description |
|----------|-------------|
| `barriers` | Values that stop propagation |
| `barrier_field` | Which field to check for barrier values |
| `weights` | Numeric weights for each value (higher = "worse") |

Propagation flows **backward** from callees to callers. When it hits a node whose `barrier_field` value is in `barriers`, it stops.

## How to generate config.json

**Important:** Only generate `config.json` when the user explicitly requests it. Architectural rules must be discussed and agreed upon with the user before writing the config. The agent proposes rules based on codebase analysis, but the user decides what constraints to enforce.

### Step 1: User request

Wait for the user to ask for architecture enforcement. Example prompts:
- "Set up architectural rules for this project"
- "Enforce layer boundaries"
- "Add dependency constraints"

### Step 2: Analyze codebase

Run `graphify extract <path>` to build the graph. Then explore the codebase:

```
graphify query "What are the main directories and their purposes?"
graphify explain "How is the codebase organized?"
```

### Step 3: Propose rules to the user

Present your analysis and proposed rules. Discuss:
- What layers/tiers make sense for this project
- Which directories map to which layers
- What dependency constraints to enforce
- Whether any exceptions are needed

**Do not write the config until the user approves the rules.**

### Step 4: Write config.json

Once the user approves, write `graphify-out/arch/config.json`.

### Step 5: Validate

Run `graphify arch analyze` to check config syntax and consistency.

### Step 6: Initial audit

Run `graphify arch audit` to see existing violations. Discuss with the user which violations to fix vs. override.

## Full example

```json
{
  "lib": {
    "manual_fields": {
      "ai_summary": {
        "default": "",
        "reset_on_change": true
      },
      "pattern": {
        "values": ["Data Model", "Controller", "Service", "None"],
        "default": "None",
        "reset_on_change": false
      }
    },
    "automatic_fields": {
      "layer": {
        "values": ["Presentation", "Application", "Domain", "Infrastructure"],
        "default": "Domain",
        "assignment_rules": [
          {"value": "Presentation", "conditions": {"path_prefix": "lib/ui"}},
          {"value": "Application", "conditions": {"path_prefix": "lib/engine"}},
          {"value": "Infrastructure", "conditions": {"path_prefix": "lib/infra"}}
        ],
        "handler": "dependency_check",
        "rules": [
          {"source": "Presentation", "target": "Infrastructure", "message": "Presentation cannot call Infrastructure"},
          {"source": "Domain", "target": "Presentation", "message": "Domain cannot reference Presentation"}
        ]
      },
      "tier": {
        "values": [1, 2, 3],
        "default": 3,
        "assignment_rules": [
          {"value": 1, "conditions": {"path_prefix": "lib/ui"}},
          {"value": 2, "conditions": {"path_prefix": "lib/engine"}}
        ],
        "handler": "dependency_check",
        "rules": [
          {"source": 3, "target": 2, "message": "Tier 3 cannot depend on Tier 2"},
          {"source": 3, "target": 1, "message": "Tier 3 cannot depend on Tier 1"},
          {"source": 2, "target": 1, "message": "Tier 2 cannot depend on Tier 1"}
        ]
      },
      "purity": {
        "values": ["Pure", "StateMutator", "IoBound", "Unknown"],
        "default": "Unknown",
        "handler": "propagation",
        "barriers": ["Repository"],
        "barrier_field": "architectural_role",
        "weights": {"Pure": 1, "StateMutator": 2, "IoBound": 3, "Unknown": 0}
      },
      "architectural_role": {
        "values": ["Repository", "Controller", "Service", "Utility"],
        "default": "Utility",
        "assignment_rules": [
          {"value": "Controller", "conditions": {"class_contains": "Controller"}},
          {"value": "Repository", "conditions": {"path_prefix": "lib/repo"}},
          {"value": "Service", "conditions": {"path_prefix": "lib/services"}}
        ]
      }
    }
  },
  "rust": {
    "automatic_fields": {
      "layer": {
        "values": ["Bridge", "Domain", "Persistence"],
        "default": "Domain",
        "assignment_rules": [
          {"value": "Bridge", "conditions": {"path_prefix": "rust/src/bridge"}},
          {"value": "Persistence", "conditions": {"path_prefix": "rust/src/persistence"}}
        ],
        "handler": "dependency_check",
        "rules": [
          {"source": "Domain", "target": "Persistence", "message": "Rust Domain should not depend on Persistence"}
        ]
      }
    }
  }
}
```
