# graphify-arch reference: audit

Load this when the user runs `graphify arch audit` or asks about architectural violations.

## What audit does

Scans the code graph for dependency rule violations defined in `graphify-out/arch/config.json`. Checks:

1. **Layer violations** — e.g., Presentation calling Infrastructure
2. **Tier violations** — e.g., Tier 3 depending on Tier 1
3. **Custom rules** — any `handler: "dependency_check"` rules in the config

## Output format

```json
{
  "violations": [
    {
      "filepath": "lib/features/graph/store/graph_data_controller.dart",
      "status": "VIOLATION_DETECTED",
      "violations": [
        {
          "origin": "automated",
          "rule": "layer_violation",
          "message": "Domain layer cannot reference Presentation layer details",
          "source_file": "lib/features/graph/store/graph_data_controller.dart",
          "target_file": "lib/features/graph/ui/canvas/graph_canvas.dart",
          "filepath": "lib/features/graph/store/graph_data_controller.dart"
        }
      ]
    }
  ]
}
```

## Running audit

```bash
graphify arch audit                                    # check all files
graphify arch audit --rule layer_violation             # check specific rule type
```

## Interpreting results

| Field | Description |
|-------|-------------|
| `filepath` | File with the violation |
| `status` | Always `VIOLATION_DETECTED` for audit results |
| `violations[].rule` | Rule type: `layer_violation`, `tier_violation`, or custom |
| `violations[].message` | Human-readable description |
| `violations[].source_file` | File making the illegal dependency |
| `violations[].target_file` | File being depended on illegally |

## Fixing violations

1. Read the violation message to understand the constraint
2. Check `graphify-out/arch/config.json` for the rule definition
3. Refactor the code to remove the illegal dependency
4. Run `graphify arch audit` again to verify

If the violation is intentional (e.g., an FFI bridge), use `graphify arch set-status` to override:

```bash
graphify arch set-status lib/path/to/file.dart COMPLIANT "Intentional FFI bridge"
```
