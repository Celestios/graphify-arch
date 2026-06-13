# graphify-arch reference: commands

All `graphify arch` subcommands. Requires `graphify-out/arch/config.json` to exist.

## audit

Check all files for architectural violations.

```bash
graphify arch audit                                    # check all files
graphify arch audit --rule layer_violation             # check specific rule type
```

Output is JSON with `violations` array. Each violation includes:
- `filepath` — file with the violation
- `status` — always `VIOLATION_DETECTED`
- `violations[].rule` — rule type (`layer_violation`, `tier_violation`, or custom)
- `violations[].message` — human-readable description
- `violations[].source_file` — file making the illegal dependency
- `violations[].target_file` — file being depended on illegally

See `references/audit.md` for interpretation and fix guidance.

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

## set-status-bulk

Bulk-set manual status overrides from a JSON file.

```bash
graphify arch set-status-bulk <json-file>
```

JSON format:
```json
{
  "lib/path/to/file.dart": {
    "status": "COMPLIANT",
    "violations": "Intentional bridge pattern"
  },
  "lib/another/file.dart": {
    "status": "VIOLATION_DETECTED",
    "violations": "Known issue, tracking in #123"
  }
}
```

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

## setup-embeddings

Download ONNX embedding model for semantic search.

```bash
graphify arch setup-embeddings
```

Required before using `graphify arch semantic-search`.

## Status values

| Status | Meaning |
|--------|---------|
| `COMPLIANT` | File passes all architectural rules |
| `VIOLATION_DETECTED` | File has at least one violation |
| `PENDING_AUDIT` | File needs re-audit (e.g., after code changes) |

## Additional CLI commands (via main.py)

These are available when running `arch` directly (not through `graphify arch`):

| Command | Description |
|---------|-------------|
| `arch reindex` | Sync graph to database with arch metadata |
| `arch discover-ontology` | Print current ontology config |
| `arch get-dirty-nodes` | List nodes needing summary regeneration |
| `arch query-file --path <file>` | Query nodes for a specific file |
| `arch compile-context --target <id>` | Compile subgraph context for LLM input |
| `arch semantic-search --query <text>` | Vector similarity search over code |
| `arch update-nodes --payload-file <file>` | Batch update node metadata |
