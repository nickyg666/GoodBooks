# Debug script for GoodBooks endpoints
# Usage: powershell -ExecutionPolicy Bypass -File g:\tools\check_endpoints.ps1
$base = 'http://192.168.0.9:5000'
# Debug script for GoodBooks endpoints
# Usage: powershell -ExecutionPolicy Bypass -File g:\tools\check_endpoints.ps1
$base = 'http://192.168.0.9:5000'

function Do-Get($path) {
    Write-Host "== GET $path =="
    try {
        $r = Invoke-RestMethod -Uri ("$base$path") -Method GET -ErrorAction Stop
        $json = $r | ConvertTo-Json -Depth 5
        Write-Host $json
    } catch {
        Write-Host ("ERROR GET {0}: {1}" -f $path, $_.Exception.Message)
        if ($_.Exception.Response) {
            try { $code = $_.Exception.Response.StatusCode.Value__; Write-Host ("StatusCode: {0}" -f $code) } catch {}
        }
    }
    Write-Host ""
}

function Do-Post($path) {
    Write-Host "== POST $path =="
    try {
        $r = Invoke-WebRequest -Uri ("$base$path") -Method POST -UseBasicParsing -ErrorAction Stop
        if ($r -and $r.StatusCode) { Write-Host "Status: $($r.StatusCode)" }
        $content = $r.Content
        if ($content -and $content.Length -gt 1000) { $content = $content.Substring(0,1000) + '... (truncated)'}
        if ($content) { Write-Host $content }
    } catch {
        Write-Host ("ERROR POST {0}: {1}" -f $path, $_.Exception.Message)
        if ($_.Exception.Response) { try { $code = $_.Exception.Response.StatusCode.Value__; Write-Host ("StatusCode: {0}" -f $code) } catch {} }
    }
    Write-Host ""
}

# Server-side fetch helper
function Do-Fetch($url) {
    Write-Host "== FETCH $url =="
    try {
        $enc = [uri]::EscapeDataString($url)
        Do-Get "/_fetch?u=$enc"
    } catch {
        Write-Host ("ERROR FETCH: {0}" -f $_.Exception.Message)
    }
}

# SSE sniff - read first line from an EventSource endpoint
function Do-SSE-Sniff($path) {
    Write-Host "== SSE sniff $path =="
    try {
        $req = [System.Net.WebRequest]::Create("$base$path")
        $req.Timeout = 10000
        $resp = $req.GetResponse()
        $stream = $resp.GetResponseStream()
        $sr = New-Object System.IO.StreamReader($stream)
        $line = $sr.ReadLine()
        Write-Host "LINE: $line"
        $sr.Close(); $resp.Close()
    } catch {
        Write-Host ("ERROR SSE sniff {0}: {1}" -f $path, $_.Exception.Message)
    }
    Write-Host ""
}

# Run checks
Do-Get '/_status'
Do-Get '/_routes'
Do-Get '/api/users'
# Test feeds/run via POST to ensure redirect behavior
Do-Post '/feeds/run'

# Server-side fetch test (Goodreads sample)
Do-Fetch 'https://www.goodreads.com/genres/fiction'

# Quick SSE sniff for /feeds/stream (read first line only)
Do-SSE-Sniff '/feeds/stream'

Write-Host "Diagnostics complete."
