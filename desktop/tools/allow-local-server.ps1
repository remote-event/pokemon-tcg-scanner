#Requires -RunAsAdministrator
param(
    [Parameter(Mandatory=$true)][string]$DesktopIp,
    [Parameter(Mandatory=$true)][string]$Subnet
)
$ErrorActionPreference = "Stop"
$report = Join-Path $PSScriptRoot "../../artifacts"
New-Item -ItemType Directory -Force $report | Out-Null
# WSL mirrored networking has its own inbound firewall. Limit these rules to
# this desktop address and LAN; do not change any default firewall policies.
$creator = "{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}"
$rules = @(
    @{Name="TCGScanner-WSL-Frames";DisplayName="TCG Scanner local Wi-Fi frames";Protocol="TCP";LocalPorts="8765";LocalAddresses=$DesktopIp},
    @{Name="TCGScanner-WSL-Discovery";DisplayName="TCG Scanner local Wi-Fi discovery";Protocol="UDP";LocalPorts="5353";LocalAddresses=@($DesktopIp,"224.0.0.251")}
)
try {
    foreach ($rule in $rules) {
        if (Get-NetFirewallHyperVRule -Name $rule.Name -ErrorAction SilentlyContinue) {
            Set-NetFirewallHyperVRule -Name $rule.Name -Protocol $rule.Protocol -LocalPorts $rule.LocalPorts -LocalAddresses $rule.LocalAddresses -RemoteAddresses $Subnet -Direction Inbound -Action Allow -Enabled True -Profiles Any | Out-Null
        } else {
            New-NetFirewallHyperVRule @rule -VMCreatorId $creator -RemoteAddresses $Subnet -Direction Inbound -Action Allow -Enabled True -Profiles Any | Out-Null
        }
    }
    Get-NetFirewallHyperVRule -Name "TCGScanner-WSL-*" | Select-Object Name,Protocol,LocalPorts,LocalAddresses,RemoteAddresses,Enabled,Action | ConvertTo-Json -Depth 4 | Set-Content -Encoding UTF8 "$report/local-firewall.json"
    Write-Host "TCG Scanner allowed from $Subnet to $DesktopIp only."
} catch {
    $_ | Out-String | Set-Content -Encoding UTF8 "$report/local-firewall-error.txt"
    throw
}
