import os
import shutil
import subprocess
from pathlib import Path


def bootstrap_command(checkout, python=None):
    if os.name == "nt":
        shell = shutil.which("pwsh") or shutil.which("powershell")
        command = [shell, "-NoProfile", "-File", str(checkout / "bootstrap.ps1")]
        if python:
            command += ["-PythonExecutable", str(python)]
    else:
        command = ["bash", str(checkout / "bootstrap.sh")]
        if python:
            command.append(str(python))
    return command


def script_checkout(tmp_path):
    checkout = tmp_path / "checkout with spaces"
    checkout.mkdir()
    root = Path(__file__).resolve().parents[1]
    for name in ("bootstrap.ps1", "bootstrap.sh"):
        shutil.copyfile(root / name, checkout / name)
    return checkout


def test_bootstrap_stops_when_python_fails_before_installing(tmp_path):
    checkout = script_checkout(tmp_path)
    failing = tmp_path / ("failing python.cmd" if os.name == "nt" else "failing python")
    failing.write_text("@exit /b 7\n" if os.name == "nt" else "#!/usr/bin/env bash\nexit 7\n")
    failing.chmod(0o755)
    result = subprocess.run(
        bootstrap_command(checkout, failing),
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode != 0
    assert "Bootstrap completed" not in result.stdout
    assert not (checkout / ".venv").exists()


def test_bootstrap_preserves_invalid_existing_venv(tmp_path):
    checkout = script_checkout(tmp_path)
    venv = checkout / ".venv"
    venv.mkdir()
    marker = venv / "user-file.txt"
    marker.write_text("preserve me")
    result = subprocess.run(
        bootstrap_command(checkout), cwd=tmp_path, capture_output=True, text=True, timeout=30
    )
    assert result.returncode != 0
    assert "invalid" in result.stderr
    assert marker.read_text() == "preserve me"
