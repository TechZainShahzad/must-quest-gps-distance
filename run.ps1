# SYNTHETIC: MUST Company hiring Quest. Windows equivalent of the Makefile.
# Usage: .\run.ps1 [setup|data|qa|verify|evidence|test|all]
param([string]$Target = "all")
$ErrorActionPreference = "Stop"
$py = if (Test-Path ".venv\Scripts\python.exe") { ".venv\Scripts\python.exe" } else { "python" }

function Invoke-Step([string[]]$cmd) {
    & $py @cmd
    if ($LASTEXITCODE -ne 0) { throw "failed: $py $($cmd -join ' ')" }
}

function Invoke-ToFile([string]$script, [string]$outFile) {
    # cmd redirection keeps the file UTF-8 without a BOM (Windows PowerShell 5.1 would write UTF-16)
    cmd /c "`"$py`" $script > $outFile"
    if ($LASTEXITCODE -ne 0) { throw "failed: $py $script" }
}

switch ($Target) {
    "setup"    { Invoke-Step @("-m", "pip", "install", "-r", "requirements.txt") }
    "data"     { Invoke-Step @("data/generate_data.py") }
    "qa"       { Invoke-Step @("qa_check.py", "--pings", "data/pings.csv", "--out", "reports/") }
    "verify"   { Invoke-Step @("verify_tool.py", "--validation", "reports/validation.csv", "--manifest", "data/fault_manifest.csv", "--out", "reports/") }
    "evidence" {
        Invoke-ToFile "docs\make_figures.py" "reports\make_figures_output.txt"
        Invoke-ToFile "docs\findings_numbers.py" "reports\findings_numbers.txt"
    }
    "test"     {
        # pytest exits non-zero only on real failures; strict-xfail cases that fail as declared count as passing.
        Invoke-Step @("-m", "pytest", "--junitxml=reports/test-results.xml")
        Invoke-Step @("tests/mutation_check.py")   # breaks each rule in turn; restores the source
        Invoke-Step @("tests/summarize_results.py", "reports/test-results.xml", "reports/test-summary.md")
    }
    "all"      { foreach ($t in "data", "qa", "verify", "evidence", "test") { & $PSCommandPath $t } }
    default    { throw "unknown target: $Target" }
}
