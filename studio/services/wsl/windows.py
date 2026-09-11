"""Shared Windows interop and UAC runner, independent of USB and MQTT."""
from __future__ import annotations

import base64
import json
import os
import platform
import shutil
import subprocess
import tempfile
from pathlib import Path

WINDOWS_ENVIRONMENT = (
    "$ProgressPreference = 'SilentlyContinue';\n"
    "if (($env:PATHEXT -split ';') -notcontains '.EXE') { "
    "$env:PATHEXT=[Environment]::GetEnvironmentVariable('PATHEXT','Machine'); "
    "if (($env:PATHEXT -split ';') -notcontains '.EXE') { $env:PATHEXT='.COM;.EXE;.BAT;.CMD' } };\n"
)


class AuthorizationCancelled(RuntimeError):
    pass


def is_wsl() -> bool:
    return platform.system() == "Linux" and (
        "microsoft" in platform.release().lower() or bool(os.getenv("WSL_DISTRO_NAME"))
    )


def executable(name: str) -> str:
    found = shutil.which(name)
    if found:
        return found
    known = {
        "powershell.exe": "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe",
        "wsl.exe": "/mnt/c/Windows/System32/wsl.exe",
    }
    path = known.get(name, "")
    return path if path and Path(path).is_file() else ""


def run(argv, *, timeout=30):
    return subprocess.run(argv, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout)


def powershell(script: str, *, timeout: int = 30, runner=None, locator=None):
    tool = (locator or executable)("powershell.exe")
    if not tool:
        raise RuntimeError("Windows PowerShell is unavailable. Enable WSL Windows interoperability, then retry.")
    # WSL launch environments can replace PATHEXT (observed: only '.CPL').
    # PowerShell then treats .exe programs as documents and refuses pipelines.
    # Restore executable extensions for this process only, never host settings.
    script = WINDOWS_ENVIRONMENT + script
    encoded = base64.b64encode(script.encode("utf-16le")).decode()
    return (runner or run)([tool, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded], timeout=timeout)


def quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def decode(result) -> dict:
    if result.returncode:
        raise RuntimeError("Windows helper failed: " + _result_detail(result))
    try:
        value = json.loads(result.stdout.lstrip("\ufeff").strip())
    except (AttributeError, ValueError) as exc:
        raise RuntimeError("Windows did not return a valid setup result.") from exc
    if not isinstance(value, dict):
        raise RuntimeError("Windows did not return a setup object.")
    if value.get("error"):
        raise RuntimeError(str(value["error"]))
    return value


def _result_detail(result):
    return f"exit={result.returncode}; " + "; ".join(
        f"{name}={str(getattr(result, name, '') or '').strip()[:2000]}"
        for name in ("stdout", "stderr")
    )


def elevated(script: str, windows_temp: str, *, timeout: int = 180,
             runner=None, ps_runner=None, trace=lambda _message: None, program_name="setup.ps1") -> dict:
    runner = runner or run
    ps_runner = ps_runner or powershell
    converted = runner(["wslpath", "-u", windows_temp])
    if converted.returncode or not converted.stdout.strip():
        raise RuntimeError("Cannot access the Windows temporary directory.")
    with tempfile.TemporaryDirectory(
        prefix="desk-buddy-", dir=converted.stdout.strip()
    ) as directory:
        output = Path(directory) / "result.json"
        program = Path(directory) / program_name
        converted_paths = {
            name: runner(["wslpath", "-w", str(path)])
            for name, path in (("output", output), ("program", program))
        }
        if any(
            result.returncode or not result.stdout.strip()
            for result in converted_paths.values()
        ):
            raise RuntimeError("Cannot create the Windows setup result file.")
        win_output = converted_paths["output"].stdout.strip()
        win_program = converted_paths["program"].stdout.strip()
        program.write_text(
            "& {\n" + WINDOWS_ENVIRONMENT + script + "\n} | Out-File -LiteralPath "
            + quote(win_output) + " -Encoding utf8\n",
            encoding="utf-8-sig",
        )
        # Keep the outer command tiny. Encoding the complete network program
        # here and then encoding this launcher again exceeds Windows' process
        # command-line limit as the script grows, yielding no result file.
        launcher = (
            "$ErrorActionPreference = 'Stop'; "
            "$reply = try { "
            "$exe = Join-Path $PSHOME 'powershell.exe'; "
            f"$program = {quote(win_program)}; "
            "$args = '-NoProfile -NonInteractive -ExecutionPolicy Bypass "
            "-File \"' + $program + '\"'; "
            "$p = Start-Process -FilePath $exe -Verb RunAs -Wait -PassThru "
            "-ArgumentList $args; "
            "@{ exitCode=[int]$p.ExitCode } "
            "} catch { "
            "$failure = $_.Exception; $nativeCode = 0; "
            "while ($null -ne $failure) { "
            "if ($failure -is [System.ComponentModel.Win32Exception]) { "
            "$nativeCode = $failure.NativeErrorCode; break }; "
            "$failure = $failure.InnerException }; "
            "@{ error=$_.Exception.Message; nativeCode=[int]$nativeCode } }; "
            "$reply | ConvertTo-Json -Compress"
        )
        trace(
            "requesting Windows administrator approval "
            f"program={win_program} result={win_output}"
        )
        result = ps_runner(launcher, timeout=timeout)
        trace("administrator launcher finished: " + _result_detail(result))
        try:
            launch = json.loads((result.stdout or "").lstrip("\ufeff").strip())
        except ValueError:
            launch = {}
        if launch.get("nativeCode") == 1223:
            raise AuthorizationCancelled(
                "Windows authorization was cancelled."
            )
        if launch.get("error"):
            raise RuntimeError(
                "Windows could not start the administrator helper: "
                + str(launch["error"])
            )
        if result.returncode or launch.get("exitCode") not in (0, None):
            raise RuntimeError(
                "Windows administrator helper failed ("
                + _result_detail(result)
                + f", child exit={launch.get('exitCode')})."
            )
        if not output.exists():
            raise RuntimeError(
                "Windows administrator helper returned without a result file "
                f"({win_output}; {_result_detail(result)})."
            )
        try:
            reply = json.loads(output.read_text(encoding="utf-8-sig"))
        except ValueError as error:
            raise RuntimeError(
                "Windows did not return the setup result."
            ) from error
        if reply.get("error"):
            trace("administrator helper reported: " + str(reply["error"]))
            raise RuntimeError(reply["error"])
        trace("administrator helper completed successfully")
        return reply
