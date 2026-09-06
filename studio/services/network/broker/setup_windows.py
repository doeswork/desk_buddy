"""Windows forwarding helpers retained for separate tooling.

Studio's automatic broker setup does not call this module. A working WSL
broker does not depend on Windows forwarding or firewall configuration.

Only Studio-owned rules and forwarding entries can be updated. No Windows
credentials or MQTT passwords are passed to PowerShell.
"""
from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import subprocess
import tempfile
from pathlib import Path

from .setup_platform import executable, run


def powershell(script: str, *, timeout: int = 30):
    tool = executable("powershell.exe")
    if not tool:
        raise RuntimeError("Windows PowerShell is unavailable. Enable WSL Windows interoperability, then retry.")
    encoded = base64.b64encode(script.encode("utf-16le")).decode()
    return run([tool, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded], timeout=timeout)


def quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def decode(result) -> dict:
    if result.returncode:
        raise RuntimeError("Windows network inspection failed. Check Windows interoperability and network settings.")
    try:
        data = json.loads(result.stdout.lstrip("\ufeff").strip())
        if data.get("error"):
            raise RuntimeError(data["error"])
        return data
    except ValueError as error:
        raise RuntimeError("Windows did not return its network configuration.") from error


def network_script(*, name: str, mode: str, host: str, target: str, subnet: str, port: int, apply: bool, profile: str = "Private") -> str:
    """A fixed PowerShell program with validated IP addresses and integer port."""
    host = str(ipaddress.IPv4Address(host))
    target = str(ipaddress.IPv4Address(target))
    subnet = str(ipaddress.IPv4Network(subnet, strict=False))
    if mode not in ("nat", "mirrored") or not 1 <= port <= 65535:
        raise ValueError("Unsupported WSL network configuration.")
    if profile not in ("Private", "Public", "Domain"):
        raise ValueError("Unsupported Windows network profile.")
    # Names are generated from a digest, not supplied by the operating system.
    if not name.startswith("DeskBuddy-") or not name[10:].isalnum():
        raise ValueError("Invalid network rule name.")
    return f"""
try {{
$ErrorActionPreference = 'Stop'
$name = {quote(name)}
$key = 'HKLM:\\SOFTWARE\\DeskBuddy\\Broker\\' + $name
$hostAddress = {quote(host)}
$target = {quote(target)}
$port = {port}
$subnet = {quote(subnet)}
$mode = {quote(mode)}
$profile = {quote(profile)}
$apply = ${str(apply).lower()}
$old = Get-ItemProperty -Path $key -ErrorAction SilentlyContinue
$rule = Get-NetFirewallRule -Name $name -ErrorAction SilentlyContinue
$needs = $false
if ($rule -and -not $old) {{ throw 'A firewall rule with this name already exists and is not owned by Studio.' }}
if (-not $old -or $old.HostAddress -ne $hostAddress -or $old.Target -ne $target -or $old.Port -ne $port -or $old.Mode -ne $mode) {{ $needs = $true }}
$filter = $rule | Get-NetFirewallPortFilter
$addresses = $rule | Get-NetFirewallAddressFilter
if (-not $rule -or $rule.Enabled -ne 'True' -or $rule.Action -ne 'Allow' -or $rule.Direction -ne 'Inbound' -or $rule.Profile -ne $profile -or $filter.LocalPort -ne "$port" -or $filter.Protocol -ne 'TCP' -or $addresses.RemoteAddress -notcontains $subnet) {{ $needs = $true }}
if ($mode -eq 'nat') {{
    $lines = @(netsh interface portproxy show v4tov4)
    $entries = @($lines | ForEach-Object {{
        if ($_ -match '^\\s*(\\d+\\.\\d+\\.\\d+\\.\\d+)\\s+(\\d+)\\s+(\\d+\\.\\d+\\.\\d+\\.\\d+)\\s+(\\d+)\\s*$') {{
            [PSCustomObject]@{{ Address=$matches[1]; Port=[int]$matches[2]; Target=$matches[3]; TargetPort=[int]$matches[4] }}
        }}
    }})
    $current = @($entries | Where-Object {{ $_.Port -eq $port -and ($_.Address -eq $hostAddress -or $_.Address -eq '0.0.0.0') }})
    foreach ($entry in $current) {{
        $owned = $old -and (($entry.Address -eq $old.HostAddress -and $entry.Port -eq $old.Port -and $entry.Target -eq $old.Target) -or ($entry.Address -eq $old.PendingHost -and $entry.Port -eq $old.PendingPort -and $entry.Target -eq $old.PendingTarget))
        if (-not $owned) {{ throw 'An existing Windows forwarding entry uses the broker port. It was preserved.' }}
    }}
    if (-not ($current | Where-Object {{ $_.Address -eq $hostAddress -and $_.Target -eq $target -and $_.TargetPort -eq $port }})) {{ $needs = $true }}
    $listener = @(Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue | Where-Object {{ $_.LocalAddress -in @($hostAddress, '0.0.0.0', '::') }})
    if ($listener.Count -and -not $current.Count) {{ throw 'Another Windows application is using the broker port. It was preserved.' }}
}} else {{
    $hyper = Get-NetFirewallHyperVRule -Name $name -ErrorAction SilentlyContinue
    if ($hyper -and -not $old) {{ throw 'An existing Hyper-V rule is not owned by Studio.' }}
    if (-not $hyper -or $hyper.LocalPorts -notcontains "$port" -or $hyper.RemoteAddresses -notcontains $subnet) {{ $needs = $true }}
}}
if ($needs -and $apply) {{
    # Validate conflicts above before performing any mutation.
    New-Item -Path $key -Force | Out-Null
    # Journal the exact planned entry before writing it, so a failed firewall
    # update can be retried without mistaking our new proxy for somebody else's.
    foreach ($item in @{{PendingHost=$hostAddress; PendingTarget=$target; PendingPort=$port}}.GetEnumerator()) {{
        Set-ItemProperty -Path $key -Name $item.Key -Value $item.Value
    }}
    if ($mode -eq 'nat') {{
        if ($old -and $old.Mode -eq 'nat') {{
            $previous = $entries | Where-Object {{ $_.Address -eq $old.HostAddress -and $_.Port -eq $old.Port -and $_.Target -eq $old.Target }}
            if ($previous) {{
                netsh interface portproxy delete v4tov4 listenaddress=$($old.HostAddress) listenport=$($old.Port) | Out-Null
                if ($LASTEXITCODE -ne 0) {{ throw 'Could not update the old Studio forwarding entry.' }}
            }}
        }}
        netsh interface portproxy add v4tov4 listenaddress=$hostAddress listenport=$port connectaddress=$target connectport=$port | Out-Null
        if ($LASTEXITCODE -ne 0) {{ throw 'Windows could not create the forwarding entry.' }}
    }} else {{
        if ($old -and $old.Mode -eq 'nat') {{
            $oldLines = @(netsh interface portproxy show v4tov4)
            foreach ($line in $oldLines) {{
                if ($line -match '^\\s*(\\d+\\.\\d+\\.\\d+\\.\\d+)\\s+(\\d+)\\s+(\\d+\\.\\d+\\.\\d+\\.\\d+)\\s+(\\d+)\\s*$' -and $matches[1] -eq $old.HostAddress -and [int]$matches[2] -eq $old.Port -and $matches[3] -eq $old.Target) {{
                    netsh interface portproxy delete v4tov4 listenaddress=$($old.HostAddress) listenport=$($old.Port) | Out-Null
                    if ($LASTEXITCODE -ne 0) {{ throw 'Could not remove the previous Studio NAT entry.' }}
                }}
            }}
        }}
        if ($hyper) {{ Remove-NetFirewallHyperVRule -Name $name }}
        New-NetFirewallHyperVRule -Name $name -DisplayName 'Desk Buddy MQTT' -Direction Inbound -VMCreatorId '{{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}}' -Protocol TCP -LocalPorts $port -RemoteAddresses $subnet -Action Allow | Out-Null
    }}
    if ($rule) {{ Remove-NetFirewallRule -Name $name }}
    New-NetFirewallRule -Name $name -DisplayName 'Desk Buddy MQTT' -Direction Inbound -Action Allow -Protocol TCP -LocalPort $port -LocalAddress $hostAddress -RemoteAddress $subnet -Profile $profile | Out-Null
    foreach ($item in @{{HostAddress=$hostAddress; Target=$target; Port=$port; Mode=$mode}}.GetEnumerator()) {{
        Set-ItemProperty -Path $key -Name $item.Key -Value $item.Value
    }}
    $needs = $false
}}
@{{ needed=$needs; host=$hostAddress }} | ConvertTo-Json -Compress
}} catch {{ @{{ error=$_.Exception.Message }} | ConvertTo-Json -Compress }}
"""


def prepare(env, port: int, emit) -> str:
    from .setup import SetupCancelled, local_network
    windows = decode(powershell("""
$ErrorActionPreference = 'Stop'
$config = Get-NetIPConfiguration | Where-Object { $_.IPv4DefaultGateway -and $_.NetAdapter.Status -eq 'Up' } | Sort-Object { $_.NetIPv4Interface.InterfaceMetric } | Select-Object -First 1
if (-not $config) { throw 'No Windows LAN connection.' }
$profile = Get-NetConnectionProfile -InterfaceIndex $config.InterfaceIndex
@{ address=$config.IPv4Address.IPAddress; prefix=$config.IPv4Address.PrefixLength; profile=[string]$profile.NetworkCategory; temp=$env:TEMP } | ConvertTo-Json -Compress
"""))
    host = str(ipaddress.IPv4Address(windows["address"]))
    subnet = str(ipaddress.IPv4Network(f"{host}/{int(windows['prefix'])}", strict=False))
    profile = "Domain" if windows["profile"] == "DomainAuthenticated" else windows["profile"]
    if profile not in ("Private", "Public", "Domain"):
        raise RuntimeError("Windows could not identify the active network profile.")
    target, _ = local_network()
    if not target:
        raise RuntimeError("WSL has no network address.")
    mode_tool = executable("wslinfo") or ("/usr/lib/wsl/wslinfo" if Path("/usr/lib/wsl/wslinfo").exists() else "")
    mode = run([mode_tool, "--networking-mode"]).stdout.strip().lower() if mode_tool else ("mirrored" if target == host else "nat")
    if mode not in ("nat", "mirrored"):
        raise RuntimeError(f"WSL networking mode {mode!r} requires manual setup.")
    name = "DeskBuddy-" + hashlib.sha256(env.distribution.encode()).hexdigest()[:16]
    args = dict(name=name, mode=mode, host=host, target=target, subnet=subnet, port=port, profile=profile)
    inspection = decode(powershell(network_script(**args, apply=False)))
    if not inspection["needed"]:
        return host
    emit("verifying", "Approve Windows network access in the administrator dialog…", connected=True)
    converted = run(["wslpath", "-u", windows["temp"]])
    if converted.returncode:
        raise RuntimeError("Cannot access the Windows temporary directory.")
    with tempfile.TemporaryDirectory(prefix="desk-buddy-", dir=converted.stdout.strip()) as directory:
        output = Path(directory) / "result.json"
        win_path = run(["wslpath", "-w", str(output)]).stdout.strip()
        body = network_script(**args, apply=True)
        script = "try {\n" + body + "\n} catch { @{ error=$_.Exception.Message } | ConvertTo-Json -Compress }"
        script = "& { " + script + " } | Out-File -LiteralPath " + quote(win_path) + " -Encoding utf8"
        encoded = base64.b64encode(script.encode("utf-16le")).decode()
        launcher = "try { $p = Start-Process powershell.exe -Verb RunAs -Wait -PassThru -ArgumentList '-NoProfile -NonInteractive -EncodedCommand " + encoded + "'; exit $p.ExitCode } catch { exit 1223 }"
        result = powershell(launcher, timeout=180)
        if result.returncode == 1223:
            raise SetupCancelled("Windows authorization was cancelled. Studio remains connected locally.")
        if result.returncode or not output.exists():
            raise RuntimeError("Windows network setup did not finish. Retry to repair it.")
        reply = json.loads(output.read_text(encoding="utf-8-sig"))
        if reply.get("error"):
            raise RuntimeError(reply["error"])
    check = decode(powershell(network_script(**args, apply=False)))
    if check["needed"]:
        raise RuntimeError("Windows network configuration could not be verified. Retry setup.")
    return host
