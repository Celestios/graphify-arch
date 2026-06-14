# graphify-arch reference: context compilation

Load this when the user runs `graphify arch compile-context` or asks about understanding impact of changes.

## What compile-context does

Extracts a subgraph neighborhood around a target node and compiles it into deterministic markdown. Gives the agent a focused view of a node and its dependencies for LLM consumption.

## Usage

```bash
graphify arch compile-context --target <node_id> --direction <dir> --resolution <res> --out <file>
```

## Parameters

| Parameter | Values | Default | Description |
|-----------|--------|---------|-------------|
| `--target` | node ID | (required) | The focal node identifier |
| `--direction` | `upstream`, `downstream`, `symmetric` | (required) | Traversal direction |
| `--resolution` | `full`, `signature` | (required) | Detail level |
| `--radius` | integer | `1` | Number of hops to traverse |
| `--out` | file path | (required) | Output markdown file |

## Direction

| Direction | Traverses | Use case |
|-----------|-----------|----------|
| `downstream` | What this node calls | "What does X depend on?" |
| `upstream` | What calls this node | "What depends on X?" |
| `symmetric` | Both directions | Full neighborhood view |

## Resolution

| Resolution | Includes |
|------------|----------|
| `full` | Raw code, metadata, all fields |
| `signature` | Declarations only (no bodies) |

## Example workflow

```bash
# Find node ID
graphify arch query-file --path lib/engine/core.dart --methods

# Check downstream dependencies before modifying
graphify arch compile-context \
  --target "lib/engine/core.dart::processData" \
  --direction downstream \
  --resolution full \
  --out impact.md

# Check what depends on this function
graphify arch compile-context \
  --target "lib/engine/core.dart::processData" \
  --direction upstream \
  --resolution signature \
  --out dependents.md

# Full neighborhood for refactoring
graphify arch compile-context \
  --target "lib/engine/core.dart::CoreEngine" \
  --direction symmetric \
  --radius 2 \
  --resolution full \
  --out refactor-context.md
```
