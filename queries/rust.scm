(struct_item
  name: (type_identifier) @node.class)

(enum_item
  name: (type_identifier) @node.class)

(trait_item
  name: (type_identifier) @node.class)

(impl_item
  type: (type_identifier) @node.impl_target)

(function_item
  name: (identifier) @node.function)

(call_expression
  function: (identifier) @relation.calls)

(call_expression
  function: (field_identifier) @relation.calls)

(scoped_identifier
  (identifier) @relation.calls)

(attribute
  (identifier) @node.ffi_export
  (#match? @node.ffi_export "no_mangle"))

(attribute_item
  (attribute
    (identifier) @node.ffi_export
    (#match? @node.ffi_export "no_mangle")))

(function_item
  (function_signature
    (abi) @abi
    (#match? @abi "extern"))
  name: (identifier) @node.ffi_export)
