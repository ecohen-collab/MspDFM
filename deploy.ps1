# deploy.ps1 — build + push + update Container App + smoke prod (silencieux si OK)
# Usage : .\deploy.ps1
#
# Tag = YYYYMMDDHHMM. Rollback : az containerapp ingress traffic set ... --revision-weight <old>=100
$ErrorActionPreference = 'Stop'
$env:PYTHONIOENCODING = 'utf-8'

$start = Get-Date
$tag   = $start.ToString('yyyyMMddHHmm')
$repo  = 'dfmplateformeprocess.azurecr.io/msp-dashboard'
$img   = "${repo}:${tag}"
$fqdn  = 'msp-dashboard.gentlesand-80781e11.francecentral.azurecontainerapps.io'

function Step($name, [scriptblock]$block) {
    $out = & $block 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Host "FAIL · $name" -ForegroundColor Red
        $out | Select-Object -Last 12 | ForEach-Object { Write-Host "  $_" }
        exit 1
    }
}

Step "acr login"      { az acr login --name dfmplateformeprocess }
Step "docker build"   { docker build --platform linux/amd64 -t $img -t "${repo}:latest" . }
Step "docker push $tag" { docker push $img }
Step "docker push latest" { docker push "${repo}:latest" }

$rev = az containerapp update `
    --name msp-dashboard `
    --resource-group GRP-RES-CONTENEURS `
    --image $img `
    --query "properties.latestRevisionName" -o tsv 2>$null
if (-not $rev) { Write-Host "FAIL · containerapp update" -ForegroundColor Red; exit 1 }

try {
    $code = (Invoke-WebRequest -Uri "https://$fqdn/index.html" -UseBasicParsing -MaximumRedirection 0).StatusCode
} catch {
    $code = $_.Exception.Response.StatusCode.value__
}

$elapsed = [int]((Get-Date) - $start).TotalSeconds
if ($code -in 200, 302) {
    Write-Host "OK · $rev (tag $tag) · ${elapsed}s · smoke HTTP $code" -ForegroundColor Green
} else {
    Write-Host "WARN · $rev deployed mais smoke HTTP $code (attendu 200 ou 302)" -ForegroundColor Yellow
    exit 2
}
