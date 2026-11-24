<#
  Builds Send to TV without Gradle: self-test -> art -> aapt2 -> javac -> d8 -> zipalign -> apksigner.
  Usage:  powershell -ExecutionPolicy Bypass -File build.ps1 [-Install] [-Device 192.168.100.156:5555]
  Output: dist\SendToTV.apk
#>
param(
    [switch]$Install,
    [string]$Device = '192.168.100.156:5555',
    [string]$Jdk = 'C:\Users\jasme\dev-tools\jdk17',
    [string]$Sdk = 'C:\Users\jasme\dev-tools\android-sdk',
    [string]$Adb = "$env:USERPROFILE\platform-tools\adb.exe",
    # Default: minutes since 2026-01-01, so every build is newer than the installed one (adb refuses downgrades).
    [int]$VersionCode = [int](((Get-Date).ToUniversalTime() - [datetime]'2026-01-01').TotalMinutes),
    [string]$VersionName = '1.4'
)

Set-StrictMode -Version Latest
# Native tools print warnings on stderr; failures are detected through exit codes instead.
$ErrorActionPreference = 'Continue'

$Root = $PSScriptRoot
$Build = Join-Path $Root 'build'
$Dist = Join-Path $Root 'dist'
$Java = Join-Path $Jdk 'bin\java.exe'
$Javac = Join-Path $Jdk 'bin\javac.exe'
$Keytool = Join-Path $Jdk 'bin\keytool.exe'
$Bt = Join-Path $Sdk 'build-tools\34.0.0'
$AndroidJar = Join-Path $Sdk 'platforms\android-28\android.jar'
# d8.bat and apksigner.bat find Java through JAVA_HOME.
$env:JAVA_HOME = $Jdk

function Step([string]$name) { Write-Host "`n== $name" -ForegroundColor Cyan }

function Run([string]$exe, [string[]]$argList) {
    & $exe @argList
    if ($LASTEXITCODE -ne 0) { throw "$([IO.Path]::GetFileName($exe)) failed with exit code $LASTEXITCODE" }
}

foreach ($p in @($Java, $Javac, $Keytool, $AndroidJar, (Join-Path $Bt 'aapt2.exe'))) {
    if (-not (Test-Path -LiteralPath $p)) { throw "Missing tool: $p" }
}

Step 'clean'
if (Test-Path -LiteralPath $Build) { Remove-Item -LiteralPath $Build -Recurse -Force -ErrorAction Stop }
foreach ($d in 'test', 'res', 'assets', 'gen', 'classes', 'dex') {
    New-Item -ItemType Directory -Force -Path (Join-Path $Build $d) -ErrorAction Stop | Out-Null
}
New-Item -ItemType Directory -Force -Path $Dist -ErrorAction Stop | Out-Null

$Src = Join-Path $Root 'src\local\sendtotv'
$PureSources = @('Urls', 'Http', 'Guard', 'Server', 'Text', 'Store', 'AdBlock' | ForEach-Object { Join-Path $Src "$_.java" }) +
    @(Get-ChildItem -Path (Join-Path $Root 'src\io\nayuki\qrcodegen') -Filter '*.java' -ErrorAction Stop | ForEach-Object FullName)

Step 'self-test (pure Java parts, on this PC)'
Run $Javac (@('-encoding', 'UTF-8', '-Xlint:all', '-Werror', '-d', (Join-Path $Build 'test')) + $PureSources +
    @((Join-Path $Root 'test\local\sendtotv\SelfTest.java'), (Join-Path $Root 'tools\AdListBuilder.java')))
Run $Java @('-cp', (Join-Path $Build 'test'), 'local.sendtotv.SelfTest')

Step 'artwork'
Copy-Item -Path (Join-Path $Root 'assets\*') -Destination (Join-Path $Build 'assets') -Recurse -ErrorAction Stop
Run $Java @('-Djava.awt.headless=true', (Join-Path $Root 'tools\MakeArt.java'), (Join-Path $Build 'res'), (Join-Path $Build 'assets'))

Step 'ad block list'
# adlists\*.txt are HaGeZi lists (github.com/hagezi/dns-blocklists, wildcard/*-onlydomains.txt); refresh by re-downloading them.
$Lists = @(Get-ChildItem -Path (Join-Path $Root 'adlists') -Filter '*.txt' -ErrorAction Stop | ForEach-Object FullName)
Run $Java (@('-cp', (Join-Path $Build 'test'), 'local.sendtotv.AdListBuilder') + $Lists + @((Join-Path $Build 'assets\adhosts.bin')))

Step 'resources (aapt2)'
$ResZip = Join-Path $Build 'res.zip'
$BaseApk = Join-Path $Build 'base.apk'
Run (Join-Path $Bt 'aapt2.exe') @('compile', '--dir', (Join-Path $Build 'res'), '-o', $ResZip)
Run (Join-Path $Bt 'aapt2.exe') @('link', '-o', $BaseApk, '-I', $AndroidJar,
    '--manifest', (Join-Path $Root 'AndroidManifest.xml'), '-A', (Join-Path $Build 'assets'),
    '--java', (Join-Path $Build 'gen'), '--min-sdk-version', '28', '--target-sdk-version', '28',
    '--version-code', "$VersionCode", '--version-name', $VersionName, $ResZip)

Step 'compile (javac)'
# src\io\nayuki\qrcodegen is the QR Code generator library (MIT License, nayuki.io), kept as source.
$AllSources = @(Get-ChildItem -Path (Join-Path $Root 'src') -Recurse -Filter '*.java' -ErrorAction Stop | ForEach-Object FullName) +
    @(Get-ChildItem -Path (Join-Path $Build 'gen') -Recurse -Filter '*.java' -ErrorAction Stop | ForEach-Object FullName)
Run $Javac (@('--release', '8', '-encoding', 'UTF-8', '-Xlint:all,-options', '-Werror', '-cp', $AndroidJar,
    '-d', (Join-Path $Build 'classes')) + $AllSources)

Step 'dex (d8)'
$Classes = @(Get-ChildItem -Path (Join-Path $Build 'classes') -Recurse -Filter '*.class' -ErrorAction Stop | ForEach-Object FullName)
Run (Join-Path $Bt 'd8.bat') (@('--release', '--min-api', '28', '--lib', $AndroidJar, '--output', (Join-Path $Build 'dex')) + $Classes)

Step 'package'
$Unsigned = Join-Path $Build 'unsigned.apk'
$Aligned = Join-Path $Build 'aligned.apk'
Copy-Item -LiteralPath $BaseApk -Destination $Unsigned -ErrorAction Stop
Add-Type -AssemblyName System.IO.Compression, System.IO.Compression.FileSystem
$zip = [System.IO.Compression.ZipFile]::Open($Unsigned, [System.IO.Compression.ZipArchiveMode]::Update)
try {
    [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile($zip, (Join-Path $Build 'dex\classes.dex'), 'classes.dex') | Out-Null
} finally {
    $zip.Dispose()
}
Run (Join-Path $Bt 'zipalign.exe') @('-p', '-f', '4', $Unsigned, $Aligned)

Step 'sign'
$KeyDir = Join-Path $Root 'keystore'
$KeyStore = Join-Path $KeyDir 'sendtotv.p12'
$KeyPass = Join-Path $KeyDir 'password.txt'
if (-not (Test-Path -LiteralPath $KeyStore)) {
    New-Item -ItemType Directory -Force -Path $KeyDir -ErrorAction Stop | Out-Null
    $bytes = New-Object byte[] 24
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    [IO.File]::WriteAllText($KeyPass, [Convert]::ToBase64String($bytes))
    Run $Keytool @('-genkeypair', '-keystore', $KeyStore, '-storetype', 'PKCS12', '-alias', 'sendtotv',
        '-keyalg', 'RSA', '-keysize', '2048', '-validity', '10000', '-dname', 'CN=Send to TV',
        "-storepass:file", $KeyPass)
    Write-Host 'Created a signing key in keystore\ (keep it: updates must be signed with the same key).'
}
$Apk = Join-Path $Dist 'SendToTV.apk'
Run (Join-Path $Bt 'apksigner.bat') @('sign', '--ks', $KeyStore, '--ks-pass', "file:$KeyPass", '--out', $Apk, $Aligned)
Run (Join-Path $Bt 'apksigner.bat') @('verify', $Apk)
Write-Host ("Built {0} ({1:N0} KB)" -f $Apk, ((Get-Item -LiteralPath $Apk).Length / 1KB)) -ForegroundColor Green

if ($Install) {
    Step "install on $Device"
    Run $Adb @('connect', $Device)
    Run $Adb @('-s', $Device, 'install', '-r', $Apk)
}
