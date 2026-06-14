# graphify-arch reference: commands

All `graphify arch` subcommands. Requires `graphify-out/arch/config.json` to exist.

## audit

Check all files for architectural violations.

```bash
graphify arch audit
```

Output is JSON with `violations` array. Each violation includes:
- `filepath` — file with the violation
- `status` — always `VIOLATION_DETECTED`
- `violations[].rule` — rule type (`layer_violation`, `tier_violation`, or custom)
- `violations[].message` — human-readable description
- `violations[].source_file` — file making the illegal dependency
- `violations[].target_file` — file being depended on illegally

See `references/arch-audit.md` for interpretation and fix guidance.

## set-status

Override compliance status for a single file.

```bash
graphify arch set-status <filepath> <status> [message]
```

| Argument | Values |
|----------|--------|
| `filepath` | Relative path to the file |
| `status` | `COMPLIANT`, `VIOLATION_DETECTED`, `PENDING_AUDIT` |
| `message` | Optional description of why |

Examples:
```bash
graphify arch set-status lib/ffi/bridge.dart COMPLIANT "Intentional FFI bridge"
graphify arch set-status lib/legacy/old.dart VIOLATION_DETECTED "Known tech debt"
```

## update-component

Update component metadata from a JSON file. Accepts status overrides and arbitrary field updates.

```bash
graphify arch update-component <json-file>
```

JSON format:
```json
{
  "lib/path/to/file.dart": {
    "status": "COMPLIANT",
    "violations": "Intentional bridge pattern",
    "layer": "Infrastructure",
    "pattern": "Service"
  },
  "lib/another/file.dart": {
    "status": "VIOLATION_DETECTED",
    "violations": "Known issue, tracking in #123"
  }
}
```

| Key | Description |
|-----|-------------|
| `status` | `COMPLIANT`, `VIOLATION_DETECTED`, or `PENDING_AUDIT` |
| `violations` | Description string or array of strings |
| Any other key | Sets the corresponding `arch_meta_<key>` field on matching nodes |

## analyze

Validate ontology config syntax and consistency.

```bash
graphify arch analyze
```

Checks:
- All `default` values are in their `values` arrays
- Manual fields don't have `assignment_rules`
- Assignment rule values are in `values` array
- Dependency rule source/target values are in `values` array

## discover-ontology

Print current ontology config as JSON.

```bash
graphify arch discover-ontology
```

## query-file

Query nodes for a specific file.

```bash
graphify arch query-file --path <file> [--methods] [--classes] [--functions] [--include-body]
```

| Flag | Description |
|------|-------------|
| `--path` | Target file path (required) |
| `--methods` | Filter to methods only |
| `--classes` | Filter to classes/structs only |
| `--functions` | Filter to functions only |
| `--include-body` | Include raw code body in output |

## compile-context

Compile subgraph context for LLM input.

```bash
graphify arch compile-context --target <node_id> --direction <dir> --resolution <res> --out <file> [--radius N]
```

| Parameter | Values | Default | Description |
|-----------|--------|---------|-------------|
| `--target` | node ID | (required) | The focal node identifier |
| `--direction` | `upstream`, `downstream`, `symmetric` | (required) | Traversal direction |
| `--resolution` | `full`, `signature` | (required) | Detail level |
| `--radius` | integer | `1` | Number of hops to traverse |
| `--out` | file path | (required) | Output markdown file |

## semantic-search

Vector similarity search over code.

```bash
graphify arch semantic-search --query "<text>" [--limit N]
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `--query` | (required) | Natural language query |
| `--limit` | `5` | Maximum number of results |

## setup-embeddings

Download ONNX embedding model for semantic search.

```bash
graphify arch setup-embeddings
```

Required before using `graphify arch semantic-search`.

## install

Install arch skill section and reference files to AI assistant configs.

```bash
graphify arch install
```

## Status values

| Status | Meaning |
|--------|---------|
| `COMPLIANT` | File passes all architectural rules |
| `VIOLATION_DETECTED` | File has at least one violation |
| `PENDING_AUDIT` | File needs re-audit (e.g., after code changes) |
