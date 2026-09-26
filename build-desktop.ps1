$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
python -m venv .venv
if ($LASTEXITCODE -ne 0) { throw 'venv failed' }
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt pyinstaller==6.22.3
if ($LASTEXITCODE -ne 0) { throw 'dependencies failed' }
& .\.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --onefile --windowed --name AppleStoreMonitor --add-data 'web;web' --add-data 'catalog.json;.' --collect-all selenium desktop_main.py
if ($LASTEXITCODE -ne 0) { throw 'build failed' }
Write-Output 'EXE: dist\AppleStoreMonitor.exe'
