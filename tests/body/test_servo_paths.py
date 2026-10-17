"""One PWM path: no script (or anything but the backend) touches the pulse drivers, the real
servos are never the default, and the systemd unit never names them."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

import config

ROOT = Path(config.PROJECT_ROOT)
DRIVER_NAMES = {"PCA9685", "GpioPwm", "GpiodOutputEnable", "PulseDriver", "OutputEnable",
                "build_drivers", "release_quietly", "make_drivers"}
ALLOWED = {"body/servo_backend.py", "body/servo_drivers.py"}
CODE_FOLDERS = ("body", "brain", "voice", "scripts", "web")


def python_files() -> list[Path]:
    found = [ROOT / name for name in ("config.py", "bridge.py", "main.py")]
    for folder in CODE_FOLDERS:
        found += sorted((ROOT / folder).rglob("*.py"))
    return found


def driver_imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text())
    bad: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            bad += [a.name for a in node.names if a.name.endswith("servo_drivers")]
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module.endswith("servo_drivers"):
                bad.append(module)
            elif module in ("body", "") and any(a.name == "servo_drivers" for a in node.names):
                bad.append("body.servo_drivers")
            elif module.endswith("servo_backend"):
                bad += [a.name for a in node.names if a.name in DRIVER_NAMES]
    return bad


def test_nothing_but_the_backend_imports_the_pulse_drivers() -> None:
    offenders = {}
    for path in python_files():
        relative = path.relative_to(ROOT).as_posix()
        if relative in ALLOWED:
            continue
        bad = driver_imports(path)
        if bad:
            offenders[relative] = bad
    assert not offenders, f"only body/servo_backend.py may use the drivers: {offenders}"


def test_the_three_servo_scripts_use_the_backend_and_call_no_driver_method() -> None:
    for name in ("servo_center.py", "servo_jog.py", "servo_teleop.py"):
        text = (ROOT / "scripts" / name).read_text()
        tree = ast.parse(text)
        assert "ServoBackend" in text, f"{name} must go through ServoBackend"
        calls = {node.func.attr for node in ast.walk(tree)
                 if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}
        assert not calls & {"set_pulse_us", "release_channel"}, name
        assert "--yes" not in text, f"{name}: the confirmation cannot be skipped"


def test_the_detector_catches_a_script_that_imports_a_driver(tmp_path: Path) -> None:
    for source in ("from body.servo_drivers import PCA9685\n",
                   "import body.servo_drivers\n",
                   "from body import servo_drivers\n",
                   "from body.servo_backend import GpioPwm\n"):
        sample = tmp_path / "sample.py"
        sample.write_text(source)
        assert driver_imports(sample), source
    sample.write_text("from body.servo_backend import ServoBackend\n")
    assert not driver_imports(sample)


def test_the_environment_alone_never_selects_the_real_servos() -> None:
    env = dict(os.environ, HEXA_BACKEND="servo")
    code = "import config; print(config.BACKEND_DEFAULT, config.ENV_REQUESTS_SERVO)"
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                            cwd=ROOT, env=env, timeout=60)
    assert result.stdout.split() == ["sim", "True"], result.stderr
    refused = subprocess.run([sys.executable, "main.py", "--no-mic", "--no-speak"],
                             capture_output=True, text=True, cwd=ROOT, env=env, timeout=60)
    assert refused.returncode == 1
    assert "HEXA_BACKEND=servo is refused" in refused.stderr


def test_the_flag_is_what_selects_the_servos(monkeypatch: pytest.MonkeyPatch) -> None:
    import main

    monkeypatch.setattr(config, "ENV_REQUESTS_SERVO", True)
    parser = main.build_parser()
    assert main.resolve_backend(parser.parse_args([])) is None
    assert main.resolve_backend(parser.parse_args(["--backend", "servo"])) == "servo"
    assert main.resolve_backend(parser.parse_args(["--backend", "dryrun"])) == "dryrun"


def test_the_servo_backend_needs_typing_yes_before_anything_starts(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
        capsys: pytest.CaptureFixture[str]) -> None:
    import brain.app
    import brain.startup
    import main
    from body import servo_safety

    asked: list[str] = []

    def refuse(prompt: str = "", **_: object) -> bool:
        asked.append(prompt)
        return False

    def never(*_: object, **__: object) -> None:
        raise AssertionError("the app must not start without the confirmation")

    monkeypatch.setattr(brain.startup, "run_checks", lambda *_a, **_k: [])
    monkeypatch.setattr(main, "servo_problems", lambda: [])
    monkeypatch.setattr(servo_safety, "confirm_hardware", refuse)
    monkeypatch.setattr(brain.app, "HexaApp", never)
    code = main.main(["--backend", "servo", "--no-mic", "--no-speak", "--chat", "off",
                      "--log-file", str(tmp_path / "hexa.log")])
    assert code == main.EXIT_STARTUP and len(asked) == 1
    assert "nothing was moved" in capsys.readouterr().err


def test_servo_problems_name_the_uncalibrated_joints_of_the_committed_file() -> None:
    import main
    from body import servo_calibration

    problems = main.servo_problems()
    if servo_calibration.load().uncalibrated():
        assert problems and "not calibrated" in problems[0]
    else:
        assert problems == []


def test_the_systemd_unit_never_uses_the_real_servos() -> None:
    lines = [line for line in (ROOT / "deploy" / "hexa.service").read_text().splitlines()
             if not line.lstrip().startswith("#")]
    text = "\n".join(lines).lower()
    assert "servo" not in text
    assert "backend dryrun" in text and "hexa_backend=dryrun" in text
