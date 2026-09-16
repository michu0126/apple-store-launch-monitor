$monitorRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$docker = Get-Command docker -ErrorAction SilentlyContinue
if (-not $docker) { exit 0 }
$previous = Get-Location
try {
  Set-Location -LiteralPath $monitorRoot
  & $docker.Source compose down
} finally {
  Set-Location $previous
}
