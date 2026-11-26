<#
  apply_with_creds.ps1
  Purpose: inject AK/SK into the 4 FunctionGraph functions' env vars (local.function_env)
           to fix the detection -> analysis async call "no Huawei Cloud credentials" error.
  Only changes function user_data (env vars). Does NOT recreate functions or break URN/triggers/subscriptions.
  Usage: in your own PowerShell/CMD, cd to the infra dir, then run  .\apply_with_creds.ps1
#>
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Definition
Set-Location $root

# Credentials are configured locally by the operator; never stored in files or code.
# If missing, stop immediately instead of falling back to any embedded value.
if (-not $env:HW_ACCESS_KEY)  { Write-Error "HW_ACCESS_KEY is not set. Export it before running this script."; exit 1 }
if (-not $env:HW_SECRET_KEY) { Write-Error "HW_SECRET_KEY is not set. Export it before running this script."; exit 1 }
$ak = $env:HW_ACCESS_KEY
$sk = $env:HW_SECRET_KEY

# CCE node login password: also from environment, never inline.
if (-not $env:HW_CCE_NODE_PASSWORD) { Write-Error "HW_CCE_NODE_PASSWORD is not set. Export it before running this script."; exit 1 }
$nodePwd = $env:HW_CCE_NODE_PASSWORD

# Extract the already-deployed MAAS_API_KEY from state (avoid manual copy)
$st   = Get-Content terraform.tfstate -Raw | ConvertFrom-Json
$maas = ($st.resources | Where-Object {
          $_.type -eq 'huaweicloud_fgs_function' -and $_.instances[0].attributes.name -like '*analysis*'
        }).instances[0].attributes.user_data | ConvertFrom-Json | ForEach-Object { $_.MAAS_API_KEY }

# APIG approval URL is deployment-specific; pass via env if apig_enabled=true.
$apigUrl = if ($env:HW_APIG_APPROVAL_URL) { $env:HW_APIG_APPROVAL_URL } else { "" }

$common = @(
  "-var=create_cce_cluster=true",
  "-var=cce_node_password=$nodePwd",
  "-var=apig_enabled=false",
  "-var=apig_approval_url=$apigUrl",
  "-var=approver_email=2440926961@qq.com",
  "-var=maas_base_url=https://api.modelarts-maas.com/v2",
  "-var=maas_api_key=$maas",
  "-var=maas_model_id=deepseek-v4-flash",
  "-var=hw_access_key=$ak",
  "-var=hw_secret_key=$sk"
)

Write-Host "===== INIT (first run pulls provider, returns quickly) ====="
# dev_overrides still does a registry version query that can fail offline; that error is
# non-fatal because the local override binary is used at plan/apply time. Do not stop on it.
& terraform init
if ($LASTEXITCODE -ne 0) { Write-Host "init returned non-zero (likely offline registry query under dev_overrides). Continuing - the local provider override will be used." }

Write-Host "`n===== PLAN ====="
& terraform plan @common
if ($LASTEXITCODE -ne 0) { Write-Host "plan failed, check the error above"; exit 1 }

Write-Host "`nPlease confirm the plan output shows ONLY 4 huaweicloud_fgs_function user_data changes (+ HW_ACCESS_KEY / + HW_SECRET_KEY), all other resources No changes."
$go = Read-Host "Type y to run apply -auto-approve"
if ($go -eq 'y') {
  & terraform apply -auto-approve @common
  Write-Host "`napply done. Go to the FunctionGraph console, test the detection function with the alarm JSON to re-test the business chain."
  Write-Host "Expected: success, log shows 'async call function OK' and no longer reports 'no Huawei Cloud credentials'."
}
