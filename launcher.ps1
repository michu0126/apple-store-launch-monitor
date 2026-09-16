$monitorRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$docker = Get-Command docker -ErrorAction SilentlyContinue
if (-not $docker) {
  Add-Type -AssemblyName PresentationFramework
  [System.Windows.MessageBox]::Show('Docker Desktop is required. Install and start Docker Desktop, then run this file again.','Apple Store Monitor') | Out-Null
  exit 1
}
$previous = Get-Location
try {
  Set-Location -LiteralPath $monitorRoot
  & $docker.Source compose up -d --build
  if ($LASTEXITCODE -ne 0) { throw 'docker compose failed' }
  for ($attempt = 0; $attempt -lt 30; $attempt++) {
    try { Invoke-WebRequest -Uri 'http://127.0.0.1:8765/api/state' -TimeoutSec 2 | Out-Null; break }
    catch { Start-Sleep -Seconds 2 }
  }
} finally {
  Set-Location $previous
}
Start-Process 'http://127.0.0.1:8765'
Start-Process 'http://127.0.0.1:7900/?autoconnect=true&resize=scale'
