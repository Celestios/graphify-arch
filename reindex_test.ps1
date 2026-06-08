<script>
  param($Path = ".", $Reset = $true)
  if (!(Test-Path .celial_graph.db)) { Write-Output "No existing .celial_graph.db found. Will create/reset if -Reset is used." }
  if ($Reset) { if (Test-Path .celial_graph.db) { Remove-Item -LiteralPath .celial_graph.db -Force } }
  Write-Output "Rebuilding Rust binary..."
  Set-Location src
  cargo build 2>&1
  Write-Output "Running reindex on: $Path"
  cargo run reindex -- --path $Path 2>&1