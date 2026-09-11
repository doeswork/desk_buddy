"""Opt-in Windows-host network access for a broker running inside WSL.

The Linux broker is useful without this module: Studio and other processes in
the distribution connect over loopback.  A physical robot needs a route
through the Windows host as well.  This module inspects, creates, repairs and
removes that route without making Windows policy part of broker setup.

Every mutation is made in an explicit UAC prompt.  Ownership metadata is kept
under HKLM so a later repair or removal touches only entries Studio created.
MQTT credentials are never passed to PowerShell.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import queue
import subprocess
import sys
import threading
import traceback
from dataclasses import dataclass, replace
from pathlib import Path

from ..broker.setup_platform import Environment, executable, run
from ...wsl import windows as host_windows


@dataclass(frozen=True)
class AccessStatus:
    state: str = "idle"
    message: str = "Windows robot access has not been checked."
    host: str = ""
    ready: bool = False
    detail: str = ""
    action: str = ""


AuthorizationCancelled = host_windows.AuthorizationCancelled


def _trace(message: str) -> None:
    """Make host-boundary failures visible in source and packaged launches."""
    print(f"[Studio WSL network] {message}", file=sys.stderr, flush=True)


def _result_detail(result: subprocess.CompletedProcess) -> str:
    parts = [f"exit={result.returncode}"]
    for name in ("stdout", "stderr"):
        value = (getattr(result, name, "") or "").strip()
        if value:
            parts.append(f"{name}={value[:2000]!r}")
    return ", ".join(parts)


def powershell(script: str, *, timeout: int = 30) -> subprocess.CompletedProcess:
    return host_windows.powershell(script, timeout=timeout, runner=run, locator=executable)


quote = host_windows.quote


def decode(result: subprocess.CompletedProcess) -> dict:
    if result.returncode:
        raise RuntimeError(
            "Windows network inspection failed. Check Windows "
            "interoperability and network settings."
        )
    try:
        data = json.loads(result.stdout.lstrip("\ufeff").strip())
    except (AttributeError, ValueError) as error:
        raise RuntimeError(
            "Windows did not return its network configuration."
        ) from error
    if data.get("error"):
        raise RuntimeError(data["error"])
    return data


def _rule_name(env: Environment) -> str:
    identity = env.distribution or env.distro or "wsl"
    return "DeskBuddy-" + hashlib.sha256(identity.encode()).hexdigest()[:16]


def _validate_name(name: str) -> str:
    if not name.startswith("DeskBuddy-") or not name[10:].isalnum():
        raise ValueError("Invalid network rule name.")
    return name


def network_script(
    *,
    name: str,
    mode: str,
    host: str,
    target: str,
    subnet: str,
    port: int,
    apply: bool,
    profile: str = "Private",
) -> str:
    """Return a fixed PowerShell program containing only validated values."""
    name = _validate_name(name)
    host = str(ipaddress.IPv4Address(host))
    target = str(ipaddress.IPv4Address(target))
    network = ipaddress.IPv4Network(subnet, strict=False)
    subnet = str(network)
    subnet_mask = f"{network.network_address}/{network.netmask}"
    if mode not in ("nat", "mirrored") or not 1 <= port <= 65535:
        raise ValueError("Unsupported WSL network configuration.")
    if profile not in ("Private", "Public", "Domain"):
        raise ValueError("Unsupported Windows network profile.")
    return f"""
try {{
$ErrorActionPreference = 'Stop'
$name = {quote(name)}
$hyperName = $name + '-HyperV'
$key = 'HKLM:\\SOFTWARE\\DeskBuddy\\Broker\\' + $name
$hostAddress = {quote(host)}
$target = {quote(target)}
$port = {port}
$subnet = {quote(subnet)}
$subnetMask = {quote(subnet_mask)}
$mode = {quote(mode)}
$profile = {quote(profile)}
$apply = ${str(apply).lower()}
$netsh = Join-Path $env:WINDIR 'System32\\netsh.exe'
$proxyKey = 'HKLM:\\SYSTEM\\CurrentControlSet\\Services\\PortProxy\\v4tov4\\tcp'
$hyperAvailable = [bool](Get-Command Get-NetFirewallHyperVRule -ErrorAction SilentlyContinue)
$old = Get-ItemProperty -Path $key -ErrorAction SilentlyContinue
$owned = [bool]$old
$rule = Get-NetFirewallRule -Name $name -ErrorAction SilentlyContinue
$needs = $false
$reason = ''
if ($rule -and -not $old) {{ throw 'A firewall rule with this name already exists and is not owned by Studio.' }}
if (-not $old -or $old.HostAddress -ne $hostAddress -or $old.Target -ne $target -or $old.Port -ne $port -or $old.Mode -ne $mode -or $old.Profile -ne $profile -or $old.Subnet -ne $subnet) {{ $needs = $true }}
$filter = $rule | Get-NetFirewallPortFilter
$addresses = $rule | Get-NetFirewallAddressFilter
if (-not $rule -or $rule.Enabled -ne 'True' -or $rule.Action -ne 'Allow' -or $rule.Direction -ne 'Inbound' -or $rule.Profile -ne $profile -or $filter.LocalPort -ne "$port" -or $filter.Protocol -ne 'TCP' -or ($addresses.RemoteAddress -notcontains $subnet -and $addresses.RemoteAddress -notcontains $subnetMask)) {{ $needs = $true }}
$proxyValues = Get-ItemProperty -Path $proxyKey -ErrorAction SilentlyContinue
$entries = @($proxyValues.PSObject.Properties | ForEach-Object {{
    if ($_.Name -match '^(\\d+\\.\\d+\\.\\d+\\.\\d+)/(\\d+)$') {{
        $listenAddress = $matches[1]
        $listenPort = [int]$matches[2]
        if ([string]$_.Value -match '^(\\d+\\.\\d+\\.\\d+\\.\\d+)/(\\d+)$') {{
            [PSCustomObject]@{{ Address=$listenAddress; Port=$listenPort; Target=$matches[1]; TargetPort=[int]$matches[2] }}
        }}
    }}
}})
if ($mode -eq 'nat') {{
    $current = @($entries | Where-Object {{ $_.Port -eq $port -and ($_.Address -eq $hostAddress -or $_.Address -eq '0.0.0.0') }})
    foreach ($entry in $current) {{
        $isOurs = $old -and (($entry.Address -eq $old.HostAddress -and $entry.Port -eq $old.Port -and $entry.Target -eq $old.Target -and $entry.TargetPort -eq $old.Port) -or ($entry.Address -eq $old.PendingHost -and $entry.Port -eq $old.PendingPort -and $entry.Target -eq $old.PendingTarget -and $entry.TargetPort -eq $old.PendingPort))
        if (-not $isOurs) {{ throw 'An existing Windows forwarding entry uses the broker port. It was preserved.' }}
    }}
    if (-not ($current | Where-Object {{ $_.Address -eq $hostAddress -and $_.Target -eq $target -and $_.TargetPort -eq $port }})) {{ $needs = $true }}
    $listener = @(Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue | Where-Object {{ $_.LocalAddress -in @($hostAddress, '0.0.0.0', '::') }})
    if ($listener.Count -and -not $current.Count) {{ throw 'Another Windows application is using the broker port. It was preserved.' }}
    # A registry entry can survive after its live listener has disappeared.
    # Reapply our entry in that case, even when all recorded values match.
    if (-not $listener.Count) {{
        $needs = $true
        $reason = "Windows has no forwarding listener on ${{hostAddress}}:$port. Retry robot access to recreate Studio's forwarding entry."
    }}
    $ipHelper = Get-Service -Name iphlpsvc -ErrorAction Stop
    if ($ipHelper.Status -ne 'Running') {{
        $needs = $true
        $reason = 'Windows IP Helper is stopped. Retry robot access to start it and repair forwarding.'
    }}
}} else {{
    if (-not $hyperAvailable) {{ throw 'This Windows version cannot configure mirrored WSL firewall rules. Use NAT mode or configure the firewall manually.' }}
    $hyper = Get-NetFirewallHyperVRule -Name $hyperName -ErrorAction SilentlyContinue
    if ($hyper -and -not $old) {{ throw 'An existing Hyper-V rule is not owned by Studio.' }}
    if (-not $hyper -or $hyper.LocalPorts -notcontains "$port" -or ($hyper.RemoteAddresses -notcontains $subnet -and $hyper.RemoteAddresses -notcontains $subnetMask)) {{ $needs = $true }}
}}
if ($needs -and $apply) {{
    if ($mode -eq 'nat' -and $ipHelper.Status -ne 'Running') {{
        Start-Service -Name iphlpsvc -ErrorAction Stop
        (Get-Service -Name iphlpsvc).WaitForStatus('Running', [TimeSpan]::FromSeconds(10))
    }}
    # All conflicts are checked before the first mutation. Pending values make
    # an interrupted write recognizable and safely repairable on the next run.
    New-Item -Path $key -Force | Out-Null
    foreach ($item in @{{PendingHost=$hostAddress; PendingTarget=$target; PendingPort=$port}}.GetEnumerator()) {{
        Set-ItemProperty -Path $key -Name $item.Key -Value $item.Value
    }}
    if ($old -and $old.Mode -eq 'nat') {{
        $previous = $entries | Where-Object {{ $_.Address -eq $old.HostAddress -and $_.Port -eq $old.Port -and $_.Target -eq $old.Target -and $_.TargetPort -eq $old.Port }}
        if ($previous) {{
            & $netsh interface portproxy delete v4tov4 listenaddress=$($old.HostAddress) listenport=$($old.Port) | Out-Null
            if ($LASTEXITCODE -ne 0) {{ throw 'Could not update the old Studio forwarding entry.' }}
        }}
    }}
    $oldHyper = if ($hyperAvailable -and $old -and $old.Mode -eq 'mirrored') {{ Get-NetFirewallHyperVRule -Name $hyperName -ErrorAction SilentlyContinue }} else {{ $null }}
    if ($oldHyper) {{ Remove-NetFirewallHyperVRule -Name $hyperName }}
    if ($mode -eq 'nat') {{
        $proxyOutput = & $netsh interface portproxy add v4tov4 listenaddress=$hostAddress listenport=$port connectaddress=$target connectport=$port protocol=tcp 2>&1
        if ($LASTEXITCODE -ne 0) {{ throw ('Windows could not create the forwarding entry: ' + ($proxyOutput -join ' ')) }}
    }} else {{
        New-NetFirewallHyperVRule -Name $hyperName -DisplayName 'Desk Buddy MQTT for WSL' -Direction Inbound -VMCreatorId '{{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}}' -Protocol TCP -LocalPorts $port -RemoteAddresses $subnet -Action Allow | Out-Null
    }}
    if ($rule) {{ Remove-NetFirewallRule -Name $name }}
    New-NetFirewallRule -Name $name -DisplayName 'Desk Buddy MQTT' -Direction Inbound -Action Allow -Protocol TCP -LocalPort $port -LocalAddress $hostAddress -RemoteAddress $subnet -Profile $profile | Out-Null
    foreach ($item in @{{HostAddress=$hostAddress; Target=$target; Port=$port; Mode=$mode; Profile=$profile; Subnet=$subnet}}.GetEnumerator()) {{
        Set-ItemProperty -Path $key -Name $item.Key -Value $item.Value
    }}
    Remove-ItemProperty -Path $key -Name PendingHost,PendingTarget,PendingPort -ErrorAction SilentlyContinue
    if ($mode -eq 'nat') {{
        $written = Get-ItemProperty -Path $proxyKey -ErrorAction SilentlyContinue
        $property = $written.PSObject.Properties["$hostAddress/$port"]
        $proxyReady = $property -and [string]$property.Value -eq "$target/$port"
        if (-not $proxyReady) {{ throw ('Windows accepted the forwarding command but no entry was created: ' + ($proxyOutput -join ' ')) }}
    }}
    $owned = $true
    $needs = $false
}}
$reachable = $false
if (-not $needs) {{
    # PortProxy may take a moment to establish its listener after netsh exits.
    $attempts = if ($apply) {{ 5 }} else {{ 1 }}
    for ($attempt = 0; $attempt -lt $attempts -and -not $reachable; $attempt++) {{
        if ($attempt -gt 0) {{ Start-Sleep -Milliseconds 400 }}
        $client = New-Object System.Net.Sockets.TcpClient
        try {{
            $task = $client.ConnectAsync($hostAddress, $port)
            if ($task.Wait(3000) -and $client.Connected) {{ $reachable = $true }}
        }} catch {{ $reachable = $false }} finally {{ $client.Dispose() }}
    }}
}}
@{{ needed=$needs; owned=$owned; host=$hostAddress; reachable=$reachable; reason=$reason }} | ConvertTo-Json -Compress
}} catch {{ @{{ error=$_.Exception.Message }} | ConvertTo-Json -Compress }}
"""


def remove_script(*, name: str) -> str:
    """Remove only resources backed by Studio's administrator-owned journal."""
    name = _validate_name(name)
    return f"""
try {{
$ErrorActionPreference = 'Stop'
$name = {quote(name)}
$hyperName = $name + '-HyperV'
$key = 'HKLM:\\SOFTWARE\\DeskBuddy\\Broker\\' + $name
$netsh = Join-Path $env:WINDIR 'System32\\netsh.exe'
$proxyKey = 'HKLM:\\SYSTEM\\CurrentControlSet\\Services\\PortProxy\\v4tov4\\tcp'
$hyperAvailable = [bool](Get-Command Get-NetFirewallHyperVRule -ErrorAction SilentlyContinue)
$old = Get-ItemProperty -Path $key -ErrorAction SilentlyContinue
$rule = Get-NetFirewallRule -Name $name -ErrorAction SilentlyContinue
$hyper = if ($hyperAvailable) {{ Get-NetFirewallHyperVRule -Name $hyperName -ErrorAction SilentlyContinue }} else {{ $null }}
if (($rule -or $hyper) -and -not $old) {{ throw 'Windows network resources with this name are not owned by Studio. They were preserved.' }}
if (-not $old) {{
    @{{ removed=$false }} | ConvertTo-Json -Compress
}} else {{
    $proxyValues = Get-ItemProperty -Path $proxyKey -ErrorAction SilentlyContinue
    foreach ($property in $proxyValues.PSObject.Properties) {{
        if ($property.Name -match '^(\\d+\\.\\d+\\.\\d+\\.\\d+)/(\\d+)$') {{
            $listenAddress = $matches[1]
            $listenPort = [int]$matches[2]
            $targetValue = [string]$property.Value
            $committed = $old.Mode -eq 'nat' -and $listenAddress -eq $old.HostAddress -and $listenPort -eq $old.Port -and $targetValue -eq "$($old.Target)/$($old.Port)"
            $pending = $old.PendingHost -and $listenAddress -eq $old.PendingHost -and $listenPort -eq $old.PendingPort -and $targetValue -eq "$($old.PendingTarget)/$($old.PendingPort)"
            if ($committed -or $pending) {{
                & $netsh interface portproxy delete v4tov4 listenaddress=$listenAddress listenport=$listenPort | Out-Null
                if ($LASTEXITCODE -ne 0) {{ throw 'Windows could not remove the Studio forwarding entry.' }}
            }}
        }}
    }}
    if ($hyper) {{ Remove-NetFirewallHyperVRule -Name $hyperName }}
    if ($rule) {{ Remove-NetFirewallRule -Name $name }}
    Remove-Item -Path $key -Force
    @{{ removed=$true }} | ConvertTo-Json -Compress
}}
}} catch {{ @{{ error=$_.Exception.Message }} | ConvertTo-Json -Compress }}
"""


def _windows_info(*, require_network: bool = True) -> dict:
    condition = "if (-not $config) { throw 'No Windows LAN connection.' }" if require_network else ""
    fields = (
        "address=$config.IPv4Address.IPAddress; prefix=$config.IPv4Address.PrefixLength; "
        "profile=[string]$profile.NetworkCategory; temp=$env:TEMP"
        if require_network else "temp=$env:TEMP"
    )
    setup = """
$config = Get-NetIPConfiguration | Where-Object { $_.IPv4DefaultGateway -and $_.NetAdapter.Status -eq 'Up' } | Sort-Object { $_.NetIPv4Interface.InterfaceMetric } | Select-Object -First 1
$profile = if ($config) { Get-NetConnectionProfile -InterfaceIndex $config.InterfaceIndex } else { $null }
""" if require_network else ""
    return decode(powershell(f"""
try {{
$ErrorActionPreference = 'Stop'
{setup}
{condition}
@{{ {fields} }} | ConvertTo-Json -Compress
}} catch {{ @{{ error=$_.Exception.Message }} | ConvertTo-Json -Compress }}
"""))


def _context(env: Environment, port: int) -> dict:
    if not env.wsl:
        raise RuntimeError("Windows-host robot access is only available inside WSL.")
    windows = _windows_info()
    host = str(ipaddress.IPv4Address(windows["address"]))
    subnet = str(
        ipaddress.IPv4Network(
            f"{host}/{int(windows['prefix'])}", strict=False
        )
    )
    profile = (
        "Domain"
        if windows["profile"] == "DomainAuthenticated"
        else windows["profile"]
    )
    if profile not in ("Private", "Public", "Domain"):
        raise RuntimeError("Windows could not identify the active network profile.")

    from ..broker.setup import local_network

    target, _ = local_network()
    if not target:
        raise RuntimeError("WSL has no network address.")
    target = str(ipaddress.IPv4Address(target))
    mode_tool = executable("wslinfo") or (
        "/usr/lib/wsl/wslinfo"
        if Path("/usr/lib/wsl/wslinfo").exists()
        else ""
    )
    mode = (
        run([mode_tool, "--networking-mode"]).stdout.strip().lower()
        if mode_tool
        else ("mirrored" if target == host else "nat")
    )
    if mode not in ("nat", "mirrored"):
        raise RuntimeError(
            f"WSL networking mode {mode!r} requires manual setup."
        )
    return {
        "name": _rule_name(env),
        "mode": mode,
        "host": host,
        "target": target,
        "subnet": subnet,
        "port": port,
        "profile": profile,
        "temp": windows["temp"],
    }


def _inspect(context: dict) -> AccessStatus:
    args = {key: value for key, value in context.items() if key != "temp"}
    reply = decode(powershell(network_script(**args, apply=False)))
    if reply["needed"]:
        if reply.get("owned"):
            return AccessStatus(
                "repair",
                "Windows robot access needs repair.",
                detail=(
                    reply.get("reason") or
                    "The Windows or WSL network address, broker port, network "
                    "profile, or forwarding rules changed."
                ),
            )
        return AccessStatus(
            "disabled",
            "Robots cannot reach this WSL broker until Windows access is enabled.",
        )
    if not reply.get("reachable"):
        return AccessStatus(
            "failed",
            "Windows robot access needs attention.",
            detail=(
                "The forwarding and firewall rules are present, but the "
                "Windows-facing MQTT port did not answer. Check the broker "
                "and the Windows IP Helper service, then retry."
            ),
        )
    return AccessStatus(
        "ready",
        f"Robot address: {reply['host']}:{context['port']}",
        host=reply["host"],
        ready=True,
        detail=(
            "Windows access is configured and the port answered locally. "
            "A physical robot connection has not yet been confirmed."
        ),
    )


def inspect(env: Environment, port: int, emit=lambda *_args, **_fields: None) -> AccessStatus:
    """Read and validate current Windows-host access without requesting UAC."""
    emit("checking", "Checking Windows robot access…")
    context = _context(env, port)
    _trace(
        "inspect "
        f"mode={context['mode']} windows={context['host']} "
        f"wsl={context['target']} subnet={context['subnet']} "
        f"profile={context['profile']} port={context['port']}"
    )
    return _inspect(context)


def _elevated(script: str, windows_temp: str, *, timeout: int = 180) -> dict:
    return host_windows.elevated(script, windows_temp, timeout=timeout,
                                 runner=run, ps_runner=powershell, trace=_trace,
                                 program_name="network.ps1")


def enable(env: Environment, port: int, emit=lambda *_args, **_fields: None) -> AccessStatus:
    """Create or repair Studio-owned Windows access, then verify the port."""
    context = _context(env, port)
    _trace(
        "enable requested "
        f"mode={context['mode']} windows={context['host']} "
        f"wsl={context['target']} port={context['port']}"
    )
    current = _inspect(context)
    if current.ready:
        return current
    emit(
        "enabling",
        "Approve Windows robot access in the administrator dialog…",
    )
    args = {key: value for key, value in context.items() if key != "temp"}
    _elevated(network_script(**args, apply=True), context["temp"])
    checked = _inspect(context)
    if not checked.ready:
        raise RuntimeError(checked.detail or checked.message)
    return checked


def disable(env: Environment, port: int, emit=lambda *_args, **_fields: None) -> AccessStatus:
    """Remove this distribution's Studio-owned Windows network resources."""
    del port  # Ownership metadata records the exact port that must be removed.
    if not env.wsl:
        raise RuntimeError("Windows-host robot access is only available inside WSL.")
    emit(
        "disabling",
        "Approve removal of Windows robot access in the administrator dialog…",
    )
    windows = _windows_info(require_network=False)
    _elevated(remove_script(name=_rule_name(env)), windows["temp"])
    return AccessStatus(
        "disabled",
        "Windows robot access is disabled.",
    )


class AccessCoordinator:
    """Run host inspection and UAC operations away from the Qt event loop."""

    def __init__(self) -> None:
        self.status = AccessStatus()
        self.events: queue.Queue = queue.Queue()
        self.running = False

    def start(self, action: str, env: Environment, port: int) -> bool:
        if self.running:
            return False
        operations = {"inspect": inspect, "enable": enable, "disable": disable}
        if action not in operations:
            raise ValueError("Unsupported WSL network action.")
        self.running = True
        initial = {
            "inspect": ("checking", "Checking Windows robot access…"),
            "enable": ("enabling", "Preparing Windows robot access…"),
            "disable": ("disabling", "Removing Windows robot access…"),
        }[action]
        self.status = AccessStatus(*initial, action=action)

        def emit(state, message, **_fields):
            self.events.put(("progress", state, message))

        def work():
            try:
                result = operations[action](env, port, emit)
            except AuthorizationCancelled as error:
                _trace(str(error))
                try:
                    observed = inspect(env, port)
                except Exception:
                    observed = AccessStatus()
                result = replace(
                    observed,
                    state="cancelled",
                    message="Windows authorization was cancelled.",
                    detail=str(error),
                    action=action,
                )
            except Exception as error:
                _trace(f"{action} failed: {type(error).__name__}: {error}")
                traceback.print_exc(file=sys.stderr)
                result = AccessStatus(
                    "failed",
                    "Windows robot access needs attention.",
                    detail=str(error),
                    action=action,
                )
            self.events.put(("result", result))

        threading.Thread(
            target=work,
            name="wsl-robot-access",
            daemon=True,
        ).start()
        return True

    def poll(self) -> bool:
        changed = False
        while True:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                return changed
            changed = True
            if event[0] == "result":
                self.status = event[1]
                self.running = False
            else:
                self.status = replace(
                    self.status,
                    state=event[1],
                    message=event[2],
                )
