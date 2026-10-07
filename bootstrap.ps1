param([string]$PythonExecutable = '')

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Invoke-Python {
    param([string]$Executable, [string[]]$PythonArgs)
    & $Executable @PythonArgs
    if ($LASTEXITCODE -ne 0) {
        throw "Python step failed (exit $LASTEXITCODE): $($PythonArgs -join ' ')"
    }
}

Push-Location $PSScriptRoot
try {
    $versionCheck = 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'
    $venvPython = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
    if (Test-Path -LiteralPath (Join-Path $PSScriptRoot '.venv')) {
        if (-not (Test-Path -LiteralPath $venvPython)) {
            throw 'Existing .venv is invalid. Repair it or choose a fresh checkout.'
        }
    } else {
        if ($PythonExecutable) {
            $python = $PythonExecutable
        } elseif (Get-Command python -ErrorAction SilentlyContinue) {
            $python = 'python'
        } elseif (Get-Command py -ErrorAction SilentlyContinue) {
            $python = 'py'
        } else {
            throw 'Install Python 3.11+ or pass -PythonExecutable with its path.'
        }
        Invoke-Python $python @('-c', $versionCheck)
        Invoke-Python $python @('-m', 'venv', '.venv')
    }
    Invoke-Python $venvPython @('-c', $versionCheck)
    Invoke-Python $venvPython @('-c', 'import sys; sys.exit(0 if sys.prefix != sys.base_prefix else 1)')
    Invoke-Python $venvPython @('-m', 'pip', 'install', '-e', '.[dev]')
    Invoke-Python $venvPython @('-m', 'pip', 'check')
    Invoke-Python $venvPython @('-m', 'ruff', 'check', '.')
    Invoke-Python $venvPython @('-m', 'ruff', 'format', '--check', '.')
    Invoke-Python $venvPython @('-m', 'pytest', '-q')
    Invoke-Python $venvPython @('-m', 'keyword_searcher', '--help')
    Write-Host 'Bootstrap completed. Activate with: .\.venv\Scripts\Activate.ps1'
    Write-Host 'Run keyword-searcher with --queries, --run-dir and an external --output-dir.'
} catch {
    Write-Error $_ -ErrorAction Continue
    exit 1
} finally {
    Pop-Location
}
