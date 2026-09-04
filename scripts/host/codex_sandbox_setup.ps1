# Run once in YOUR terminal (not from Claude). Installs the Codex Windows restricted-token sandbox helper
# so `codex exec -s workspace-write` can write files. May prompt for elevation.
$exe = Join-Path (npm root -g) "@openai\codex\node_modules\@openai\codex-win32-x64\vendor\x86_64-pc-windows-msvc\codex-resources\codex-windows-sandbox-setup.exe"
Write-Host "Running: $exe"
& $exe
