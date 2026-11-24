param(
    [Parameter(Mandatory = $true)][string]$Repository,
    [string]$LogDirectory,
    [string]$GitExecutable = 'git'
)
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path -LiteralPath $Repository).Path
if (-not $LogDirectory) { $LogDirectory = Join-Path $repo 'download_logs' }
New-Item -ItemType Directory -Path $LogDirectory -Force | Out-Null
$logDir = (Resolve-Path -LiteralPath $LogDirectory).Path
Get-Command $GitExecutable -ErrorAction Stop | Out-Null
$env:GIT_TERMINAL_PROMPT = '0'
try {
    Set-Content -LiteralPath "$logDir\status.txt" -Value "RUNNING $(Get-Date -Format o)"
    & $GitExecutable -C $repo -c lfs.dialtimeout=30 -c lfs.tlstimeout=30 -c lfs.activitytimeout=60 -c lfs.transfer.maxretries=3 lfs pull '--include=AIOps2025/**' '--exclude=' 1>> "$logDir\download.log" 2>> "$logDir\download.err.log"
    if ($LASTEXITCODE -ne 0) { throw "LFS download exit=$LASTEXITCODE" }
    & $GitExecutable -C $repo lfs fsck 1>> "$logDir\download.log" 2>> "$logDir\download.err.log"
    if ($LASTEXITCODE -ne 0) { throw 'LFS fsck failed' }
    $dataDir = Join-Path $repo 'AIOps2025'
    $archives = @(Get-ChildItem -LiteralPath $dataDir -Filter '*.tar.gz')
    if ($archives.Count -ne 18) { throw "Expected 18 archives, found $($archives.Count)" }
    $hashes = @{}
    foreach ($line in Get-Content -LiteralPath "$dataDir\checksums.md5") {
        if ($line -match '^\s*([0-9a-fA-F]{32})\s+\*?(.+?)\s*$') {
            $hashes[[IO.Path]::GetFileName($Matches[2])] = $Matches[1]
        }
    }
    foreach ($archive in $archives) {
        if (-not $hashes.ContainsKey($archive.Name)) { throw "Missing checksum: $($archive.Name)" }
        $actual = (Get-FileHash -LiteralPath $archive.FullName -Algorithm MD5).Hash
        if ($actual -ne $hashes[$archive.Name]) { throw "MD5 mismatch: $($archive.Name)" }
        Add-Content -LiteralPath "$logDir\download.log" -Value "MD5 OK $($archive.Name)"
    }
    Set-Content -LiteralPath "$logDir\status.txt" -Value "COMPLETE $(Get-Date -Format o): 18 archives verified"
} catch {
    Add-Content -LiteralPath "$logDir\download.err.log" -Value $_.Exception.Message
    Set-Content -LiteralPath "$logDir\status.txt" -Value "FAILED $(Get-Date -Format o): $($_.Exception.Message)"
    exit 1
}
