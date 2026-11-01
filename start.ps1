param([switch]$Check, [switch]$NativeTitlebar)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
try {
    $taskPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $taskPython)) {
        Write-Host 'Creating a local Python environment...'
        $taskBasePython = Get-Command python -ErrorAction SilentlyContinue
        if ($taskBasePython) {
            & $taskBasePython.Source -m venv (Join-Path $PSScriptRoot '.venv')
        } else {
            $taskPyLauncher = Get-Command py -ErrorAction Stop
            & $taskPyLauncher.Source -3 -m venv (Join-Path $PSScriptRoot '.venv')
        }
        if ($LASTEXITCODE -ne 0) { throw 'Python 3.10 or newer with Tk is required.' }
    }
    & $taskPython (Join-Path $PSScriptRoot 'yuketang_helper\check_environment.py')
    if ($LASTEXITCODE -eq 2) { throw 'Python 3.10 or newer with Tk is required. Reinstall Python with Tcl/Tk enabled.' }
    if ($LASTEXITCODE -ne 0) {
        Write-Host 'Installing the video helper dependency...'
        & $taskPython -m pip install -r (Join-Path $PSScriptRoot 'requirements.txt')
        if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed. Check your network and retry.' }
    }
    $taskArguments = @((Join-Path $PSScriptRoot 'app.py'))
    if ($Check) { $taskArguments += '--check' }
    if ($NativeTitlebar) { $taskArguments += '--native-titlebar' }
    & $taskPython @taskArguments
    exit $LASTEXITCODE
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}
