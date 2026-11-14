<#
.SYNOPSIS
  Collect everything a hand import in the VR-Forces Simulation Object Editor produced, so it can be
  compared with the editor's MAK export (mak/) on another machine.

.DESCRIPTION
  Read-only on the VR-Forces install: files are copied into a temporary folder and zipped.
  Windows PowerShell 5.1 or later; Python is NOT required.

  The zip contains:
    sms/<name>.sms, sms/<name>/...      each test SMS in full (vrfSim\*.entity, gui\visuals\*.ommx/.leaf/.metx, ...)
    model/<category>/<ModelName>/...    the model files the editor was pointed at (.flt/.fbx + textures)
    changed/...                         any other file in the install changed since -Since (definitions,
                                        importConfig, other SMSs), with its path relative to C:\MAK
    mak/node_info_<file>.txt            the model as MAK's own reader sees it (medfTool --writeNodeInfo)
    logs/...                            Simulation Object Editor and VR-Forces logs written since -Since
    version/...                         VR-Forces build information
    export_info.json                    parameters, file list with SHA-256 hashes, entity summary

.PARAMETER SmsName
  One or more test SMS names, e.g. PipelineTest or PipelineTest,PipelineAuto.

.PARAMETER Since
  When the hand import started. Files changed after it are collected under changed/ and logs/.
  Default: 24 hours ago.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File export_hand_import.ps1 -SmsName PipelineTest -Since "2026-11-15 09:30"

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File export_hand_import.ps1 -SmsName PipelineTest,PipelineAuto `
      -ModelName HarmonyOfTheSeas -EntityName "Harmony of the Seas" -ModelCategory Vehicles/Surface
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)] [string[]] $SmsName,
    [string]   $ModelName     = "HarmonyOfTheSeas",
    [string]   $EntityName    = "Harmony of the Seas",
    [string]   $ModelCategory = "Vehicles/Surface",
    [datetime] $Since         = (Get-Date).AddDays(-1),
    [string]   $MakRoot       = "C:\MAK",
    [string]   $VrfRoot       = "C:\MAK\vrforces5.1.1",
    [string]   $SharedData    = "C:\MAK\SharedData\18\latest",
    [string]   $OutDir        = [Environment]::GetFolderPath("Desktop"),
    [int]      $MaxSmsMB      = 500
)

$ErrorActionPreference = "Stop"
# `powershell -File script.ps1 -SmsName A,B` passes the single string "A,B" (only -Command parses arrays), so split it here.
$SmsName = @($SmsName | ForEach-Object { $_ -split ',' } | ForEach-Object { $_.Trim().Trim('"', "'") } | Where-Object { $_ })
$stamp   = Get-Date -Format "yyyyMMdd-HHmmss"
$work    = Join-Path ([IO.Path]::GetTempPath()) "vrf-hand-import-$stamp"
$zipPath = Join-Path $OutDir "vrf-hand-import-$ModelName-$stamp.zip"
$notes   = New-Object System.Collections.Generic.List[string]
New-Item -ItemType Directory -Force -Path $work | Out-Null

function Copy-Into([string] $src, [string] $destRel) {
    $dest = Join-Path $work $destRel
    New-Item -ItemType Directory -Force -Path (Split-Path $dest -Parent) | Out-Null
    Copy-Item -LiteralPath $src -Destination $dest -Force
}

function Copy-Tree([string] $src, [string] $destRel) {
    $root = (Resolve-Path -LiteralPath $src).Path.TrimEnd('\')
    Get-ChildItem -LiteralPath $root -Recurse -File | ForEach-Object {
        Copy-Into $_.FullName (Join-Path $destRel $_.FullName.Substring($root.Length + 1))
    }
}

function Rel([string] $path, [string] $base) {
    $b = (Resolve-Path -LiteralPath $base).Path.TrimEnd('\') + '\'
    if ($path.StartsWith($b, [StringComparison]::OrdinalIgnoreCase)) { return $path.Substring($b.Length) }
    # outside the base (e.g. SharedData installed elsewhere): keep the full path, drive letter as a folder
    return ($path -replace '^([A-Za-z]):\\', '$1\' -replace '^\\\\', 'UNC\')
}

if (-not (Test-Path -LiteralPath $VrfRoot)) { throw "VR-Forces not found at $VrfRoot (use -VrfRoot)" }
$smsDir = Join-Path $VrfRoot "data\simulationModelSets"
Write-Host "Collecting hand-import results for '$EntityName' (since $Since)"

# ---------------------------------------------------------------- 1. the test SMSs
$entitySummary = @()
foreach ($sms in $SmsName) {
    $file = Join-Path $smsDir "$sms.sms"
    $dir  = Join-Path $smsDir $sms
    if (-not (Test-Path -LiteralPath $file)) {
        $available = (Get-ChildItem -LiteralPath $smsDir -Filter *.sms -File | ForEach-Object { $_.BaseName }) -join ', '
        $notes.Add("SMS file not found: $file (available: $available)")
        Write-Warning "SMS '$sms' not found in $smsDir. Available: $available"
        continue
    }
    Copy-Into $file "sms\$sms.sms"
    if (Test-Path -LiteralPath $dir) {
        $mb = [math]::Round(((Get-ChildItem -LiteralPath $dir -Recurse -File | Measure-Object Length -Sum).Sum) / 1MB, 1)
        if ($mb -gt $MaxSmsMB) {
            $notes.Add("SMS '$sms' is $mb MB (> $MaxSmsMB MB): copied vrfSim\*.entity, gui\ and *.sms/*.opd only")
            Get-ChildItem -LiteralPath $dir -Recurse -File | Where-Object {
                $_.Extension -in ".entity", ".opd", ".sms", ".ommx", ".leaf", ".hier", ".metx", ".elem" -or $_.FullName -like "*\gui\*"
            } | ForEach-Object { Copy-Into $_.FullName (Join-Path "sms\$sms" (Rel $_.FullName $dir)) }
        } else {
            Copy-Tree $dir "sms\$sms"
        }
        # the hand-made entity: by file name or by gui-label
        Get-ChildItem -LiteralPath $dir -Recurse -Filter *.entity -File | ForEach-Object {
            $text = Get-Content -LiteralPath $_.FullName -Raw
            if ($_.BaseName -eq $EntityName -or $text -match [regex]::Escape("paramName=`"gui-label`">$EntityName<")) {
                $ot = [regex]::Match($text, 'objectType="([^"]+)"').Groups[1].Value
                $entitySummary += [pscustomobject]@{ sms = $sms; file = (Rel $_.FullName $smsDir); objectType = $ot
                                                     modified = $_.LastWriteTime.ToString("s") }
            }
        }
    } else {
        $notes.Add("SMS '$sms' has no folder $dir (nothing saved yet?)")
    }
}
if ($entitySummary.Count -eq 0) { $notes.Add("no .entity named or labelled '$EntityName' found in: $($SmsName -join ', ')") }

# ---------------------------------------------------------------- 2. the model the editor used
$modelDir = Join-Path $SharedData ("ModelData\" + ($ModelCategory -replace '/', '\') + "\$ModelName")
$modelFiles = @()
if (Test-Path -LiteralPath $modelDir) {
    Copy-Tree $modelDir ("model\" + ($ModelCategory -replace '/', '\') + "\$ModelName")
    $modelFiles = Get-ChildItem -LiteralPath $modelDir -File | Where-Object { $_.Extension -in ".flt", ".fbx", ".medf", ".ive", ".osgb" }
} else {
    $notes.Add("model folder not found: $modelDir")
}

# ---------------------------------------------------------------- 3. MAK's reader on that model
$medf = Join-Path $VrfRoot "bin64\medfTool.exe"
if ((Test-Path -LiteralPath $medf) -and $modelFiles) {
    foreach ($m in $modelFiles) {
        $tmp  = Join-Path $work "_medf_tmp"
        New-Item -ItemType Directory -Force -Path $tmp, (Join-Path $work "mak") | Out-Null
        $info = Join-Path $work ("mak\node_info_" + $m.Name + ".txt")
        $log  = Join-Path $work ("mak\medfTool_" + $m.Name + ".log")
        & $medf -f $m.FullName --writeNodeInfo $info --outputDirectory $tmp *> $log
        if ($LASTEXITCODE -ne 0) { $notes.Add("medfTool failed on $($m.Name) (exit $LASTEXITCODE); see mak\medfTool_$($m.Name).log") }
        Remove-Item -LiteralPath $tmp -Recurse -Force
    }
} elseif (-not (Test-Path -LiteralPath $medf)) {
    $notes.Add("medfTool not found at $medf; MAK-side node info not collected")
}

# ---------------------------------------------------------------- 4. anything else changed since the start
$watch = @(
    @{ path = (Join-Path $SharedData "ModelData\Configuration\Definitions"); filter = $null },
    @{ path = (Join-Path $VrfRoot "appData\importConfig");                 filter = $null },
    @{ path = $smsDir;                                                      filter = $null },
    @{ path = (Join-Path $VrfRoot "appData\settings");                      filter = $null }
)
$skipSms = $SmsName | ForEach-Object { (Join-Path $smsDir $_) + '\'; (Join-Path $smsDir "$_.sms") }
foreach ($w in $watch) {
    if (-not (Test-Path -LiteralPath $w.path)) { continue }
    Get-ChildItem -LiteralPath $w.path -Recurse -File | Where-Object { $_.LastWriteTime -gt $Since } | ForEach-Object {
        $full = $_.FullName
        $inTestSms = $false
        foreach ($s in $skipSms) { if ($full.StartsWith($s, [StringComparison]::OrdinalIgnoreCase)) { $inTestSms = $true } }
        if (-not $inTestSms) { Copy-Into $full (Join-Path "changed" (Rel $full $MakRoot)) }
    }
}

# ---------------------------------------------------------------- 5. logs and version
$logDir = Join-Path $MakRoot "logs"
if (Test-Path -LiteralPath $logDir) {
    Get-ChildItem -LiteralPath $logDir -File | Where-Object { $_.LastWriteTime -gt $Since } |
        ForEach-Object { Copy-Into $_.FullName (Join-Path "logs" $_.Name) }
}
Get-ChildItem -LiteralPath $VrfRoot -Filter "*Build_Information.txt" -File -ErrorAction SilentlyContinue |
    ForEach-Object { Copy-Into $_.FullName (Join-Path "version" $_.Name) }

# ---------------------------------------------------------------- 6. index and zip
$files = Get-ChildItem -LiteralPath $work -Recurse -File | ForEach-Object {
    [pscustomobject]@{ path = (Rel $_.FullName $work); bytes = $_.Length
                       sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLower() }
}
$info = [pscustomobject]@{
    schema         = "me3d.mak_hand_import_export/1"
    exported_at    = (Get-Date).ToString("s")
    since          = $Since.ToString("s")
    sms            = $SmsName
    model_name     = $ModelName
    entity_name    = $EntityName
    model_category = $ModelCategory
    vrforces_root  = $VrfRoot
    shared_data    = $SharedData
    powershell     = $PSVersionTable.PSVersion.ToString()
    entities       = $entitySummary
    model_files    = @($modelFiles | ForEach-Object { $_.Name })
    notes          = $notes
    files          = $files
}
$info | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $work "export_info.json") -Encoding UTF8

if (Test-Path -LiteralPath $zipPath) { Remove-Item -LiteralPath $zipPath -Force }
Compress-Archive -Path (Join-Path $work "*") -DestinationPath $zipPath
Remove-Item -LiteralPath $work -Recurse -Force

Write-Host ""
Write-Host "Wrote $zipPath ($([math]::Round((Get-Item -LiteralPath $zipPath).Length / 1KB)) KB, $($files.Count + 1) files)"
foreach ($e in $entitySummary) { Write-Host "  entity: $($e.file)  objectType=$($e.objectType)" }
foreach ($n in $notes) { Write-Warning $n }
