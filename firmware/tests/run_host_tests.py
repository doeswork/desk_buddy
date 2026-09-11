"""Compile the actual vendored MQTT client against a deterministic socket."""
from pathlib import Path
import subprocess
import tempfile
root = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix="desk-buddy-host-") as directory:
    binary = str(Path(directory) / "transport")
    subprocess.run([
        "g++", "-std=c++17", "-Wall", "-Wextra", "-fsanitize=address,undefined", "-g",
        "-I", str(root / "tests/host_stubs"), "-I", str(root),
        "-I", str(root / "vendor/PubSubClient/src"),
        str(root / "tests/transport.cpp"), str(root / "vendor/PubSubClient/src/PubSubClient.cpp"),
        "-o", binary,
    ], check=True)
    subprocess.run([binary], check=True)
