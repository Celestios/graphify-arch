(class_declaration
  name: (type_identifier) @node.class)

(mixin_declaration
  name: (type_identifier) @node.class)

(enum_declaration
  name: (type_identifier) @node.class)

(extension_declaration
  name: (type_identifier) @node.class)

(function_declaration
  name: (identifier) @node.function)

(method_declaration
  name: (identifier) @node.function)

(method_invocation
  function: (identifier) @relation.calls)

(implements_clause
  (type_identifier) @relation.implements)

(superclass
  (type_identifier) @relation.implements)

(identifier) @relation.ffi_bridge
(#match? @relation.ffi_bridge "^(DynamicLibrary|Native|ffi)$")

(argument
  (identifier) @relation.calls
  (#match? @relation.calls "^(DynamicLibrary|DynamicLibrary\\.open|lookup|Native|ffi)$"))
