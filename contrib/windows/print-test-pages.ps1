<#
.SYNOPSIS
  Print one LE SPOOLER test page for each supported Windows case, then follow each
  job through the spooler until the printer (the CYD) acknowledges it.

.DESCRIPTION
  Cases:
    1. Plain text            -> "LE SPOOLER (plain text)"  (Generic / Text Only: raw text)
    2. Two-page text         -> "LE SPOOLER (plain text)"  (page break -> form feed)
    3. Formatted RTF         -> "LE SPOOLER"               (WordPad: text layer extracted)
    4. Text drawn as graphics-> "LE SPOOLER"               (IPP: PDF text layer, or OCR)
    5. Photo                 -> "LE SPOOLER"               (IPP: becomes ASCII art)

  No admin rights needed. Run install-windows-printer.ps1 first.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File print-test-pages.ps1 -Server 192.168.5.156
#>
param(
    [string]$Server = "192.168.5.156",
    [int]$TimeoutMinutes = 12
)
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Drawing
$Spooler = "http://${Server}:8765"
$Text = "LE SPOOLER (plain text)"
$Pic = "LE SPOOLER"
$stamp = Get-Date -Format "HHmmss"
$tmp = Join-Path $env:TEMP "le-spooler-tests"
New-Item -ItemType Directory -Force $tmp | Out-Null
$since = (Invoke-RestMethod "$Spooler/jobs").jobs.id   # jobs that existed before we started

function Print-Graphics($printer, $docName, [scriptblock]$draw) {
    $pd = New-Object System.Drawing.Printing.PrintDocument
    $pd.PrinterSettings.PrinterName = $printer
    $pd.DocumentName = $docName
    $pd.add_PrintPage({ param($s, $e) & $draw $e.Graphics $e.MarginBounds; $e.HasMorePages = $false }.GetNewClosure())
    $pd.Print()
    $pd.Dispose()
}

Write-Host "1. plain text -> $Text"
@"
LE SPOOLER TEST $stamp - CASE 1: PLAIN TEXT
Printed from Windows through the Generic / Text Only driver.
Accents survive: café, garçon, naïve, crème brûlée.
"@ | Out-Printer -Name $Text

Write-Host "2. two-page text -> $Text"
$page = (1..70 | ForEach-Object { "CASE 2 line $_ of a two-page document ($stamp)" }) -join "`r`n"
$page | Out-Printer -Name $Text

Write-Host "3. formatted RTF via WordPad -> $Pic"
$rtf = Join-Path $tmp "case3-$stamp.rtf"
@"
{\rtf1\ansi{\fonttbl{\f0 Times New Roman;}{\f1 Arial;}}
\f1\fs36\b LE SPOOLER TEST $stamp - CASE 3\b0\par
\f0\fs24 A \i formatted\i0  document from \b WordPad\b0 . Fonts and bold are dropped, but the\par
spooler pulls every word out of the page as real text.\par
}
"@ | Set-Content -Path $rtf -Encoding Ascii
$wp = Start-Process "$env:ProgramFiles\Windows NT\Accessories\wordpad.exe" -ArgumentList "/pt `"$rtf`" `"$Pic`"" -PassThru
$wp.WaitForExit(60000) | Out-Null

Write-Host "4. text drawn as graphics -> $Pic"
Print-Graphics $Pic "Case 4 text as graphics $stamp" {
    param($g, $m)
    $f = New-Object System.Drawing.Font("Arial", 14)
    $lines = @(
        "LE SPOOLER TEST $stamp - CASE 4: TEXT SENT AS GRAPHICS",
        "This page reaches the La Frite through the IPP class driver.",
        "If Windows sends a text layer it is extracted directly,",
        "otherwise the backend reads the words back with optical",
        "character recognition before typing them on the paper."
    )
    for ($i = 0; $i -lt $lines.Count; $i++) { $g.DrawString($lines[$i], $f, [System.Drawing.Brushes]::Black, $m.Left, $m.Top + $i * 30) }
}

Write-Host "5. photo -> $Pic"
$bmp = New-Object System.Drawing.Bitmap(600, 420)
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.SmoothingMode = "AntiAlias"
$sky = New-Object System.Drawing.Drawing2D.LinearGradientBrush((New-Object System.Drawing.Rectangle(0, 0, 600, 420)), [System.Drawing.Color]::FromArgb(250, 250, 250), [System.Drawing.Color]::FromArgb(120, 120, 120), 90)
$g.FillRectangle($sky, 0, 0, 600, 420)
$path = New-Object System.Drawing.Drawing2D.GraphicsPath
$path.AddEllipse(180, 70, 240, 240)
$ball = New-Object System.Drawing.Drawing2D.PathGradientBrush($path)
$ball.CenterPoint = New-Object System.Drawing.PointF(250, 140)
$ball.CenterColor = [System.Drawing.Color]::White
$ball.SurroundColors = @([System.Drawing.Color]::FromArgb(20, 20, 20))
$g.FillPath($ball, $path)
$g.FillEllipse((New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(70, 0, 0, 0))), 190, 330, 220, 40)
$g.Dispose()
$png = Join-Path $tmp "case5-$stamp.png"
$bmp.Save($png)
Print-Graphics $Pic "Case 5 photo $stamp" {
    param($gr, $m)
    $gr.DrawImage($bmp, $m.Left, $m.Top, $m.Width, [int]($m.Width * 420 / 600))
}.GetNewClosure()

# --- follow the jobs ---------------------------------------------------------
Write-Host "`nWaiting for the printer to collect and finish each job (the CYD types at ~30 chars/s)..."
$deadline = (Get-Date).AddMinutes($TimeoutMinutes)
do {
    Start-Sleep 10
    $jobs = (Invoke-RestMethod "$Spooler/jobs").jobs | Where-Object { $_.id -notin $since }
    $waiting = @($jobs | Where-Object status -ne "printed").Count
    $p = (Invoke-RestMethod "$Spooler/health").printers | Where-Object online | Select-Object -First 1 -ExpandProperty name
    Write-Host ("  {0:HH:mm:ss}  {1} new job(s), {2} not printed yet  (printer online: {3})" -f (Get-Date), @($jobs).Count, $waiting, $p)
} while ((@($jobs).Count -lt 5 -or $waiting -gt 0) -and (Get-Date) -lt $deadline)

foreach ($j in $jobs) {
    $body = (Invoke-RestMethod "$Spooler/jobs/$($j.id)").job.body
    Write-Host ("`n[{0}] {1}  ({2} chars, from {3})" -f $j.status.ToUpper(), $j.title, $j.chars, $j.source)
    ($body -split "`n" | Select-Object -First 14) -join "`n" | Write-Host
}
