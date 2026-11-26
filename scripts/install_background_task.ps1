# ============================================================================
# OTONOM KARİYER ASİSTANI — WINDOWS GÖREV ZAMANLAYICI KURULUM BETİĞİ
# (scripts/install_background_task.ps1)
# ============================================================================
# Bu betik, Otonom Kariyer Asistanı'nı Windows Görev Zamanlayıcı'ya (Task Scheduler)
# kaydeder. Böylece:
# 1. Hiçbir terminal penceresi açık kalmak zorunda OLMAZ (-WindowStyle Hidden).
# 2. Bilgisayar sabah 09:00'da kapalı olsa bile, bilgisayarı gün içinde açtığınız
#    ilk anda (-StartWhenAvailable) arka planda sessizce çalışır ve günlük
#    başvuruları tamamlayıp kapanır.
# ============================================================================

param(
    [string]$RunTime = "09:00",
    [int]$DailyLimit = 5
)

$ErrorActionPreference = "Stop"
$TaskName = "OtonomKariyerAsistani_Gunluk"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$PythonExe = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$MainPy = Join-Path $ProjectRoot "main.py"

if (-not (Test-Path $PythonExe)) {
    Write-Error "Sanal ortam Python bulunamadı: $PythonExe"
    exit 1
}

# Arka planda penceresiz (Hidden) çalışacak komut argümanları
$ActionArgs = "-NoProfile -WindowStyle Hidden -Command `"Set-Location '$ProjectRoot'; `$env:PYTHONPATH=''; & '$PythonExe' '$MainPy' --no-dry-run --limit $DailyLimit --keywords 'ROS 2' 'SLAM' 'Otonom Sistemler' 'Bilgisayarlı Görü'`""

$Action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $ActionArgs -WorkingDirectory $ProjectRoot
$Trigger = New-ScheduledTaskTrigger -Daily -At $RunTime
$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RunOnlyIfNetworkAvailable `
    -MultipleInstances IgnoreNew

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -Description "Muhammed Enes BAL - Yapay Zeka Destekli Otonom Kariyer Asistani (Arka Plan Gorevi)" `
    -Force | Out-Null

Write-Host "✅ '$TaskName' Windows Görev Zamanlayıcı'ya başarıyla kaydedildi!" -ForegroundColor Green
Write-Host "   • Çalışma Saati : Her gün $RunTime (Bilgisayar kapalıysa açıldığı ilk an otomatik çalışır)" -ForegroundColor Cyan
Write-Host "   • Görünüm       : Tamamen arka planda (Terminal penceresi açılmaz)" -ForegroundColor Cyan
Write-Host "   • Log Dosyası   : $ProjectRoot\logs\career_assistant.log" -ForegroundColor Cyan
