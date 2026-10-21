#Requires -RunAsAdministrator
<#
.SYNOPSIS
  Install LE SPOOLER as printers on Windows (talks to the CUPS queue on the spooler box).

.DESCRIPTION
  Creates two printers, both pointing at  http://<server>:631/printers/LE_SPOOLER :

    "LE SPOOLER"               Microsoft IPP Class driver - use this one for everything:
                               Word, browsers, PDFs keep their text (extracted on the
                               spooler), photos print as ASCII art, scans are read with OCR.
    "LE SPOOLER (plain text)"  Generic / Text Only driver - for Notepad and command-line
                               output: the raw characters are sent, nothing is rendered.
                               (Proportional fonts come out jumbled through it.)

  Any older LE SPOOLER printers on this PC are removed first (redirected Remote
  Desktop printers are left alone - they belong to the RDP session).

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File install-windows-printer.ps1 -Server 192.168.5.156
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File install-windows-printer.ps1 -Uninstall
#>
param(
    [string]$Server = "192.168.5.156",
    [string]$Queue = "LE_SPOOLER",
    [switch]$Uninstall
)
$ErrorActionPreference = "Stop"
$TextName = "LE SPOOLER (plain text)"
$PicName = "LE SPOOLER"
$OldNames = @("LE SPOOLER Pictures", $Queue)
$Url = "http://${Server}:631/printers/$Queue"
Start-Transcript -Path "$env:TEMP\le-spooler-install.log" -Force | Out-Null

function Say($m) { Write-Host "==> $m" -ForegroundColor Cyan }

# --- remove previous installs (not the redirected RDP ones) -----------------
Get-Printer | Where-Object {
    ($_.Name -in (@($TextName, $PicName) + $OldNames) -or $_.PortName -like "*$Server*" -or
     ($_.DriverName -eq "Microsoft IPP Class Driver" -and $_.Name -like "*SPOOLER*")) -and
    $_.Name -notlike "*(redirected*"
} | ForEach-Object { Say "Removing old printer '$($_.Name)'"; Remove-Printer -Name $_.Name }

if ($Uninstall) { Say "Uninstalled."; Stop-Transcript | Out-Null; exit 0 }

# --- reachability ------------------------------------------------------------
if (-not (Test-NetConnection -ComputerName $Server -Port 631 -InformationLevel Quiet)) {
    throw "Can't reach CUPS at ${Server}:631. Is the La Frite on, and was install-cups-printer.sh run with --share?"
}

# --- 1. text printer: Generic / Text Only over an Internet (IPP) port -------
$feature = "Printing-Foundation-InternetPrinting-Client"
if ((Get-WindowsOptionalFeature -Online -FeatureName $feature).State -ne "Enabled") {
    Say "Enabling Windows' Internet Printing Client (needed for http:// printer ports)"
    Enable-WindowsOptionalFeature -Online -FeatureName $feature -NoRestart | Out-Null
}
Say "Adding driver 'Generic / Text Only' (built into Windows)"
Add-PrinterDriver -Name "Generic / Text Only"
$inf = (Get-PrinterDriver -Name "Generic / Text Only").InfPath
Say "Creating printer '$TextName' -> $Url"
& rundll32.exe printui.dll,PrintUIEntry /if /b "$TextName" /r "$Url" /m "Generic / Text Only" /f "$inf" /q /u
for ($i = 0; $i -lt 20 -and -not (Get-Printer -Name $TextName -ErrorAction SilentlyContinue); $i++) { Start-Sleep 1 }
if (-not (Get-Printer -Name $TextName -ErrorAction SilentlyContinue)) { throw "printui did not create '$TextName'" }

# --- 2. pictures printer: Microsoft IPP Class driver ------------------------
Say "Creating printer '$PicName' (IPP: documents, photos, scans)"
$before = (Get-Printer).Name
Add-Printer -IppURL $Url
Start-Sleep 2
$new = (Get-Printer).Name | Where-Object { $_ -notin $before } | Select-Object -First 1
if (-not $new) { throw "Add-Printer -IppURL did not create a printer" }
if ($new -ne $PicName) { Rename-Printer -Name $new -NewName $PicName }

Say "Done:"
Get-Printer -Name $TextName, $PicName | Format-Table Name, DriverName, PortName -AutoSize | Out-String | Write-Host
Stop-Transcript | Out-Null
