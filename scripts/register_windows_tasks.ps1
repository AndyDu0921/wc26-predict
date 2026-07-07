# WC26 Predict — Windows Task Scheduler Registration
#
# This script is intentionally disabled in V4.9. The previous scheduler pointed
# to a removed daily automation helper, which is no longer a valid production entry.
# Registering partial scheduled tasks would break traceability during the live
# tournament window.

$ErrorActionPreference = "Stop"

$ProjectRoot = "D:\hermes agent\2026世界杯分析"
$CurrentEntrypoints = @(
    "backend\scripts\predict_match_full.py",
    "backend\scripts\run_postmatch_complete.py",
    "backend\scripts\run_accuracy_experiments.py",
    "backend\scripts\preflight_accuracy_experiments.py",
    "backend\scripts\audit_db_integrity.py",
    "backend\scripts\audit_public_outputs.py",
    "backend\scripts\collect_match_evidence.py",
    "backend\scripts\extract_information_signals.py",
    "backend\scripts\score_information_signals.py",
    "backend\scripts\audit_match_information_state.py"
)

Write-Host "WC26 Predict — Windows scheduled task registration is disabled." -ForegroundColor Yellow
Write-Host ""
Write-Host "Current operational entrypoints:" -ForegroundColor Cyan

$missing = @()
foreach ($entrypoint in $CurrentEntrypoints) {
    $path = Join-Path $ProjectRoot $entrypoint
    if (Test-Path $path) {
        Write-Host "  [OK] $entrypoint" -ForegroundColor Green
    } else {
        Write-Host "  [MISSING] $entrypoint" -ForegroundColor Red
        $missing += $entrypoint
    }
}

Write-Host ""
Write-Host "No Windows tasks were registered. Use the explicit entrypoints above until a new scheduler is designed." -ForegroundColor Yellow

if ($missing.Count -gt 0) {
    Write-Error "Missing current entrypoint(s): $($missing -join ', ')"
}

exit 1
