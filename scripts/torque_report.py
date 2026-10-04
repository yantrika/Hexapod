#!/usr/bin/env python3
"""Servo sizing from the simulation: the torque and speed each joint type needs (DIRECT mode).

    python scripts/torque_report.py                       # full report, printed as markdown
    python scripts/torque_report.py --markdown docs/TORQUE_REPORT.md --json logs/torque_report.json
    python scripts/torque_report.py --motions walk --seconds 3 --no-sensitivity   # quick look
    python scripts/torque_report.py --render logs/torque_report.json   # re-print, no simulation

Motions: stand (up from sit, then hold), sit (down, then hold), wave, walk (forward at the maximum
speed), strafe (maximum speed) and turn (in place at the maximum yaw rate). The motor torque is
sampled at EVERY physics step (240 Hz) from ``getJointState``.

Each motion runs twice. Once with the sim motor force cap raised far above the placeholder
``config.JOINT_MAX_FORCE_NM`` (the real need), once with the normal config (does any joint
saturate there?). A saturated torque hides the real need, so the numbers that size a servo are
always the raised-cap ones.

READ THE LIMITS in the report: the sim servo is an ideal position motor (no gear friction, no
backlash, no voltage sag), its velocity gain is lowered (``config.JOINT_VELOCITY_GAIN``, sim only,
which changes the torque readings), and all masses are placeholders until the real parts are
weighed. A real servo needs MORE than the sim says; the safety factor is the margin for that.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import math
import sys
import tempfile
from collections.abc import Iterator, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from body import gait, poses, urdf  # noqa: E402
from body.clock import ManualClock  # noqa: E402
from body.controller import Controller, State  # noqa: E402
from body.gait import BodyVelocity, GaitParams  # noqa: E402
from body.sim_backend import SimBackend  # noqa: E402

logger = logging.getLogger(__name__)

NM_TO_KGCM = 10.197  # 1 N*m = 10.197 kg*cm (servo datasheets quote kg*cm)
JOINT_TYPES = config.JOINTS_PER_LEG  # ("coxa", "femur", "tibia"); the flat joint order cycles them
MOTIONS = ("stand", "sit", "wave", "walk", "strafe", "turn")
RAISED_CAP_NM = 30.0  # "well above" the 3 N*m placeholder
CAP_REACHED = 0.98  # |torque| or |speed| at or above this share of its cap counts as "at the cap"
NEAR_PEAK = 0.8  # "near peak" = at least this share of the reference torque
DEFAULT_MASS_SCALES = (0.7, 1.0, 1.3)
DEFAULT_HEIGHTS_M = (0.10, 0.16)  # body heights beside the default stand height (0.13 m)
DEFAULT_WALK_S = 8.0
CONTROL_DT = 1.0 / config.CONTROL_HZ
SETTLE_S = 1.0  # before measuring
RAMP_S = 1.0  # a gait command ramps up over this time (as the controller does)
HOLD_S = 1.0  # measured after a stand / sit / wave finishes
TRANSITION_LIMIT_S = 10.0
_BUSY = (State.STANDING_UP, State.SITTING_DOWN, State.WAVING, State.SETTLING)


class ReportError(ValueError):
    """A report file or an argument that cannot be understood."""


# --- units and small formulas (pure) ---------------------------------------------------------
def nm_to_kgcm(newton_metres: float) -> float:
    return newton_metres * NM_TO_KGCM


def kgcm_to_nm(kg_cm: float) -> float:
    return kg_cm / NM_TO_KGCM


def seconds_per_60deg(speed_rad_s: float) -> float:
    """How long a servo needs for 60 degrees at *speed_rad_s* (datasheets quote s / 60 deg)."""
    if speed_rad_s <= 0.0:
        return math.inf
    return math.radians(60.0) / speed_rad_s


# --- measured data and its statistics (pure) -------------------------------------------------
@dataclass
class Samples:
    """Per physics step, per joint (flat ``config.JOINT_NAMES`` order): shape ``(steps, 18)``."""

    angles: np.ndarray
    velocities: np.ndarray
    torques: np.ndarray


class Recorder:
    """Attach to ``SimBackend.on_step``: stores the joint states after every physics step."""

    def __init__(self, sim: SimBackend) -> None:
        self._sim = sim
        self._rows: list[tuple[np.ndarray, np.ndarray, np.ndarray]] = []

    def __call__(self) -> None:
        self._rows.append(self._sim.joint_states())

    def samples(self) -> Samples:
        if not self._rows:
            raise ReportError("nothing was recorded")
        angles, velocities, torques = (np.array(column) for column in zip(*self._rows, strict=True))
        return Samples(angles, velocities, torques)


@dataclass(frozen=True)
class TypeStats:
    """One joint type (all six legs together) over one motion."""

    peak_nm: float
    rms_nm: float
    p99_nm: float  # 99th percentile of |torque|: a peak that is not one contact spike
    peak_speed_rad_s: float
    angle_min_deg: float
    angle_max_deg: float
    at_cap_fraction: float  # share of samples with |torque| at the torque cap of that run

    @property
    def s_per_60deg(self) -> float:
        return seconds_per_60deg(self.peak_speed_rad_s)


@dataclass(frozen=True)
class Simultaneous:
    """How many of the 18 joints are near their peak torque at the same physics step."""

    max_vs_peak: int  # near = >= NEAR_PEAK of the joint type's peak
    p95_vs_peak: int
    max_vs_p99: int  # near = >= NEAR_PEAK of the joint type's p99 (more joints qualify)
    p95_vs_p99: int


def summarize(samples: Samples, cap_nm: float) -> dict[str, TypeStats]:
    """Per joint type statistics; *cap_nm* is the torque cap the run used."""
    result: dict[str, TypeStats] = {}
    for index, kind in enumerate(JOINT_TYPES):
        torque = np.abs(samples.torques[:, index :: len(JOINT_TYPES)])
        speed = np.abs(samples.velocities[:, index :: len(JOINT_TYPES)])
        angle = np.degrees(samples.angles[:, index :: len(JOINT_TYPES)])
        result[kind] = TypeStats(
            peak_nm=float(torque.max()),
            rms_nm=float(math.sqrt(float(np.mean(torque**2)))),
            p99_nm=float(np.percentile(torque, 99)),
            peak_speed_rad_s=float(speed.max()),
            angle_min_deg=float(angle.min()),
            angle_max_deg=float(angle.max()),
            at_cap_fraction=float(np.mean(torque >= CAP_REACHED * cap_nm)),
        )
    return result


def simultaneous(samples: Samples, stats: dict[str, TypeStats]) -> Simultaneous:
    """Joints near peak at the same step, against the type's peak and against its p99."""
    torque = np.abs(samples.torques)
    counts = {}
    for name, reference in (
        ("peak", [stats[kind].peak_nm for kind in JOINT_TYPES]),
        ("p99", [stats[kind].p99_nm for kind in JOINT_TYPES]),
    ):
        per_joint = np.tile(np.array(reference), config.DOF // len(JOINT_TYPES))
        near = (torque >= NEAR_PEAK * per_joint).sum(axis=1)
        counts[name] = (int(near.max()), int(math.ceil(np.percentile(near, 95))))
    return Simultaneous(counts["peak"][0], counts["peak"][1], counts["p99"][0], counts["p99"][1])


def hit_cap(stats: TypeStats) -> bool:
    return stats.at_cap_fraction > 0.0


def speed_capped(stats: TypeStats) -> bool:
    return stats.peak_speed_rad_s >= CAP_REACHED * config.JOINT_MAX_VELOCITY_RAD_S


# --- the report (a plain data structure that survives JSON) ----------------------------------
@dataclass
class MotionResult:
    motion: str
    seconds: float  # length of the measured window
    raised: dict[str, TypeStats]  # torque cap raised: the real need
    nominal: dict[str, TypeStats]  # the normal config: did it saturate?
    near_peak: Simultaneous


@dataclass
class SensitivityRow:
    label: str
    total_mass_kg: float
    body_height_m: float
    peak_nm: dict[str, float]  # walk, raised cap, per joint type
    rms_nm: dict[str, float]
    ik_scaled_ticks: int  # ticks where the planner had to shorten the stride


@dataclass
class Report:
    nominal_cap_nm: float
    raised_cap_nm: float
    velocity_gain: float
    velocity_cap_rad_s: float
    total_mass_kg: float
    safety_factor: float
    motions: list[MotionResult] = field(default_factory=list)
    sensitivity: list[SensitivityRow] = field(default_factory=list)
    gain_walk: MotionResult | None = None  # the walk again with velocity gain 1.0 (the default)


def _type_stats_from(data: Any) -> dict[str, TypeStats]:
    try:
        return {kind: TypeStats(**data[kind]) for kind in JOINT_TYPES}
    except (KeyError, TypeError) as error:
        raise ReportError(f"bad joint statistics: {error}") from error


def _motion_from(data: Any) -> MotionResult:
    try:
        return MotionResult(
            motion=str(data["motion"]), seconds=float(data["seconds"]),
            raised=_type_stats_from(data["raised"]), nominal=_type_stats_from(data["nominal"]),
            near_peak=Simultaneous(**data["near_peak"]),
        )
    except (KeyError, TypeError) as error:
        raise ReportError(f"bad motion entry: {error}") from error


def report_from_dict(data: Any) -> Report:
    """Parse a report written by ``--json``; raises ``ReportError`` with the reason if it is bad."""
    if not isinstance(data, dict):
        raise ReportError("a report is a JSON object")
    try:
        report = Report(
            nominal_cap_nm=float(data["nominal_cap_nm"]),
            raised_cap_nm=float(data["raised_cap_nm"]),
            velocity_gain=float(data["velocity_gain"]),
            velocity_cap_rad_s=float(data["velocity_cap_rad_s"]),
            total_mass_kg=float(data["total_mass_kg"]), safety_factor=float(data["safety_factor"]),
            motions=[_motion_from(item) for item in data["motions"]],
            sensitivity=[SensitivityRow(**item) for item in data.get("sensitivity", [])],
            gain_walk=_motion_from(data["gain_walk"]) if data.get("gain_walk") else None,
        )
    except (KeyError, TypeError, ValueError) as error:
        if isinstance(error, ReportError):
            raise
        raise ReportError(f"bad report: {error}") from error
    return report


def load_report(path: Path) -> Report:
    try:
        return report_from_dict(json.loads(path.read_text()))
    except OSError as error:
        raise ReportError(f"cannot read {path}: {error.strerror}") from error
    except json.JSONDecodeError as error:
        raise ReportError(f"{path} is not valid JSON: {error}") from error


def parse_floats(text: str, name: str = "value") -> list[float]:
    """``"0.7, 1.0,1.3"`` -> ``[0.7, 1.0, 1.3]``; every value must be a positive number."""
    parts = [part.strip() for part in text.split(",") if part.strip()]
    if not parts:
        raise ReportError(f"no {name}s given")
    values = []
    for part in parts:
        try:
            value = float(part)
        except ValueError as error:
            raise ReportError(f"{name} {part!r} is not a number") from error
        if not math.isfinite(value) or value <= 0.0:
            raise ReportError(f"{name} {part!r} must be a positive number")
        values.append(value)
    return values


def parse_motions(text: str) -> list[str]:
    names = [part.strip() for part in text.split(",") if part.strip()]
    unknown = [name for name in names if name not in MOTIONS]
    if unknown or not names:
        raise ReportError(f"motions must be from {', '.join(MOTIONS)} (got {text!r})")
    return names


# --- rendering (pure) ------------------------------------------------------------------------
def _nm(value: float) -> str:
    return f"{value:.2f}"


def _kgcm(value: float) -> str:
    return f"{nm_to_kgcm(value):.1f}"


def _deg(value: float) -> str:
    rounded = round(value)
    return f"{rounded:+d}" if rounded else "0"


def _seconds(value: float) -> str:
    return "n/a" if math.isinf(value) else f"{value:.2f}"


def worst_case(report: Report) -> dict[str, dict[str, float]]:
    """Per joint type over every motion (cap raised): peak, rms, fastest need and its motion."""
    worst: dict[str, dict[str, float]] = {}
    for kind in JOINT_TYPES:
        peaks = [(m.raised[kind].peak_nm, m.motion) for m in report.motions]
        rms = [m.raised[kind].rms_nm for m in report.motions]
        speeds = [m.raised[kind].peak_speed_rad_s for m in report.motions]
        worst[kind] = {
            "peak_nm": max(p for p, _ in peaks),
            "rms_nm": max(rms),
            "speed_rad_s": max(speeds),
        }
    return worst


def _table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> list[str]:
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    lines += ["| " + " | ".join(row) + " |" for row in rows]
    return lines + [""]


def render_markdown(report: Report) -> str:
    """The whole report as markdown (tables in N*m and kg*cm, with and without the factor)."""
    factor = report.safety_factor
    out = [
        "# Servo and power sizing from the simulation",
        "",
        "Generated by `scripts/torque_report.py`. **Every number below comes from the simulation "
        "with PLACEHOLDER masses and a PLACEHOLDER servo model. Replace the placeholders "
        "(listed at the end) and run it again before buying servos.**",
        "",
        f"- Total robot mass in this run: **{report.total_mass_kg:.2f} kg** (placeholder).",
        f"- Torque cap raised to **{report.raised_cap_nm:g} N*m** for the measurement; "
        f"the normal config caps at **{report.nominal_cap_nm:g} N*m** (also a placeholder).",
        f"- Units: 1 N*m = {NM_TO_KGCM} kg*cm. Safety factor: **{factor:g}** "
        "(`config.TORQUE_SAFETY_FACTOR`).",
        f"- Torque is sampled at every physics step ({config.PHYSICS_HZ:g} Hz) from "
        "`getJointState` (applied motor torque), all six legs of a type together.",
        "",
        "## 1. What each joint type needs (cap raised)",
        "",
    ]
    rows = []
    for motion in report.motions:
        for kind in JOINT_TYPES:
            s = motion.raised[kind]
            star = "*" if speed_capped(s) else ""
            rows.append([
                motion.motion, kind, _nm(s.peak_nm), _kgcm(s.peak_nm), _nm(s.rms_nm),
                _kgcm(s.rms_nm), _nm(s.p99_nm), f"{s.peak_speed_rad_s:.2f}{star}",
                _seconds(s.s_per_60deg), f"{_deg(s.angle_min_deg)} .. {_deg(s.angle_max_deg)}",
            ])
    out += _table(
        ["motion", "joint", "peak N*m", "peak kg*cm", "RMS N*m", "RMS kg*cm", "p99 N*m",
         "peak speed rad/s", "s per 60 deg", "angle range deg"], rows)
    out += [
        "`peak` is the largest single sample (it can be one foot-contact spike); `p99` is the "
        "99th percentile of |torque|, a peak that is not one spike. `*` = the sim speed limit "
        f"({report.velocity_cap_rad_s:g} rad/s) was reached, so the real need may be higher.",
        "",
        "## 2. Did the normal config saturate?",
        "",
    ]
    rows = []
    for motion in report.motions:
        for kind in JOINT_TYPES:
            nominal, raised = motion.nominal[kind], motion.raised[kind]
            cap = report.nominal_cap_nm
            rows.append([
                motion.motion, kind, _nm(nominal.peak_nm),
                "YES" if hit_cap(nominal) else "no", f"{100 * nominal.at_cap_fraction:.1f} %",
                "YES" if raised.peak_nm > cap else "no",
            ])
    out += _table(
        ["motion", "joint", f"peak with the {report.nominal_cap_nm:g} N*m cap",
         "hit the cap", "time at the cap", f"needs more than {report.nominal_cap_nm:g} N*m"],
        rows)
    saturated = sorted({
        f"{m.motion}/{kind}" for m in report.motions for kind in JOINT_TYPES
        if hit_cap(m.nominal[kind])
    })
    out += [
        ("**Saturated in the normal config: " + ", ".join(saturated) + ".** The capped numbers "
         "hide the real need; use section 1.") if saturated
        else "**No joint hit the cap in the normal config.**",
        "",
        "## 3. Requirement per joint type (worst case over the motions, cap raised)",
        "",
    ]
    worst = worst_case(report)
    rows = []
    for kind in JOINT_TYPES:
        w = worst[kind]
        rows.append([
            kind, _nm(w["peak_nm"]), _kgcm(w["peak_nm"]),
            _nm(w["peak_nm"] * factor), _kgcm(w["peak_nm"] * factor),
            _nm(w["rms_nm"]), _nm(w["rms_nm"] * factor), _kgcm(w["rms_nm"] * factor),
            _seconds(seconds_per_60deg(w["speed_rad_s"])),
        ])
    out += _table(
        ["joint", "peak N*m", "peak kg*cm", f"peak x{factor:g} N*m", f"peak x{factor:g} kg*cm",
         "RMS N*m", f"RMS x{factor:g} N*m", f"RMS x{factor:g} kg*cm",
         "needs s per 60 deg or faster"],
        rows)
    out += [
        f"Size the servo's **rated stall torque** at or above `peak x{factor:g}` (compare the "
        "datasheet value at YOUR supply voltage). The continuous (RMS) figure is what the servo "
        "and its supply carry for a long time. Speed: the servo must do 60 degrees in at most "
        "the time shown; datasheets give seconds per 60 degrees at no load, and a loaded servo "
        "is slower.",
        "",
        "## 4. Sensitivity (walk forward at the maximum speed, cap raised)",
        "",
    ]
    rows = []
    for row in report.sensitivity:
        cells = [row.label, f"{row.total_mass_kg:.2f}", f"{row.body_height_m:.2f}"]
        for kind in JOINT_TYPES:
            cells.append(f"{_nm(row.peak_nm[kind])} / {_kgcm(row.peak_nm[kind])}")
        for kind in JOINT_TYPES:
            cells.append(_nm(row.rms_nm[kind]))
        cells.append(str(row.ik_scaled_ticks))
        rows.append(cells)
    out += _table(
        ["case", "total mass kg", "body height m"]
        + [f"{kind} peak N*m / kg*cm" for kind in JOINT_TYPES]
        + [f"{kind} RMS N*m" for kind in JOINT_TYPES] + ["stride-shortened ticks"], rows)
    out += [
        "## 5. Joints near peak torque at the same time",
        "",
    ]
    rows = [[m.motion, str(m.near_peak.max_vs_peak), str(m.near_peak.p95_vs_peak),
             str(m.near_peak.max_vs_p99), str(m.near_peak.p95_vs_p99)] for m in report.motions]
    out += _table(
        ["motion", f"max (>= {NEAR_PEAK:.0%} of peak)", "95th pct",
         f"max (>= {NEAR_PEAK:.0%} of p99)", "95th pct"], rows)
    out += [
        "Out of 18 joints, at the same physics step. The p99 reference counts more joints than "
        "the peak reference because the peak is often one contact spike; use the larger count "
        "(`docs/HARDWARE.md`).",
        "",
    ]
    if report.gain_walk is not None:
        out += [
            "## 6. Effect of the lowered velocity gain (walk)",
            "",
            f"The sim uses velocity gain {report.velocity_gain:g} (`config.JOINT_VELOCITY_GAIN`, "
            "sim only). The same walk with PyBullet's default gain of 1.0:",
            "",
        ]
        base = next((m for m in report.motions if m.motion == "walk"), None)
        rows = []
        for kind in JOINT_TYPES:
            high = report.gain_walk.raised[kind]
            rows.append([
                kind,
                _nm(base.raised[kind].peak_nm) if base else "-",
                _nm(high.peak_nm),
                _nm(base.raised[kind].rms_nm) if base else "-",
                _nm(high.rms_nm),
            ])
        out += _table(
            ["joint", f"peak N*m (gain {report.velocity_gain:g})", "peak N*m (gain 1.0)",
             f"RMS N*m (gain {report.velocity_gain:g})", "RMS N*m (gain 1.0)"], rows)
        if base is not None:
            ratios = {
                kind: report.gain_walk.raised[kind].peak_nm / base.raised[kind].peak_nm
                for kind in JOINT_TYPES if base.raised[kind].peak_nm > 0
            }
            if ratios:
                kind, ratio = max(ratios.items(), key=lambda item: item[1])
                out += [
                    f"With the default gain the walk peaks are up to **{ratio:.2f}x** the lowered-"
                    f"gain peaks ({kind}). So the lowered gain UNDERSTATES the peak torque in "
                    "section 1; allow for that on top of the safety factor.",
                    "",
                ]
    out += [
        "## Limits of these numbers (read before using them)",
        "",
        "- The sim servo is an ideal position motor: **no gear friction, no backlash, no "
        "compliance, no supply sag, no temperature effects.** A real servo needs more torque "
        "for the same motion; the safety factor is the margin for that, not a guarantee.",
        f"- The sim uses a **lowered velocity gain** ({report.velocity_gain:g}, sim only). It "
        "damps the joints less than a real servo's own friction would, and it changes the "
        "torque readings (section 6 shows by how much).",
        "- All masses are **placeholders**; torque scales roughly with mass (section 4).",
        "- `getJointState` reports the torque the motor model applied after the cap; with the "
        "cap raised that is the torque the motion asked for.",
        "- One flat floor, one friction value, no pushes or slopes. Add margin for them.",
        "",
        "## Placeholders to replace with real numbers",
        "",
        "| Where | What | Replace with |",
        "|---|---|---|",
        "| `config.BODY_MASS_KG` | body mass | weigh the plate with Pi, battery, boards, wiring |",
        "| `config.LINK_MASS_KG` | coxa / femur / tibia / foot | weigh each link with its "
        "servo and bracket |",
        "| `config.TOTAL_MASS_KG` | total mass | follows from the two above; check on a scale |",
        "| `config.JOINT_MAX_FORCE_NM` | sim torque cap | the chosen servo's stall torque at your "
        "supply voltage |",
        "| `config.JOINT_MAX_VELOCITY_RAD_S` | sim speed cap | the chosen servo's no-load speed |",
        "| `config.TORQUE_SAFETY_FACTOR` | margin | keep 2.0 unless you have measured friction |",
        "| `config.BODY_HEIGHT_STAND` / link lengths | geometry | the built robot |",
        "",
    ]
    return "\n".join(out)


# --- running the simulation ------------------------------------------------------------------
@contextlib.contextmanager
def scaled_masses(scale: float) -> Iterator[None]:
    """Temporarily scale every mass in ``config`` (the URDF is generated from them)."""
    body, links = config.BODY_MASS_KG, dict(config.LINK_MASS_KG)
    config.BODY_MASS_KG = body * scale
    config.LINK_MASS_KG = {name: mass * scale for name, mass in links.items()}
    try:
        yield
    finally:
        config.BODY_MASS_KG, config.LINK_MASS_KG = body, links


def _start_pose(sim: SimBackend, body_height_m: float) -> None:
    angles = poses.pose_angles(body_height_m)
    sim.reset_joint_angles(angles)
    sim.set_joint_targets(angles)
    sim.reset_base_pose([0.0, 0.0, body_height_m + config.SIM_SPAWN_CLEARANCE_M])


def _tick(controller: Controller, clock: ManualClock) -> None:
    clock.advance(CONTROL_DT)
    controller.heartbeat()
    controller.tick(CONTROL_DT)


def _run_controller_motion(sim: SimBackend, motion: str) -> Samples:
    """stand / sit / wave through the real ``Controller`` (the same code the robot runs)."""
    clock = ManualClock()
    initial = State.SITTING if motion == "stand" else State.STANDING
    _start_pose(sim, config.BODY_HEIGHT_SIT if motion == "stand" else config.BODY_HEIGHT_STAND)
    controller = Controller(sim, clock, initial_state=initial)
    for _ in range(round(SETTLE_S / CONTROL_DT)):
        _tick(controller, clock)
    recorder = Recorder(sim)
    sim.on_step = recorder
    try:
        result = {"stand": controller.stand, "sit": controller.sit, "wave": controller.wave}[
            motion
        ]()
        if not result.ok:
            raise ReportError(f"the controller refused {motion}: {result}")
        for _ in range(round(TRANSITION_LIMIT_S / CONTROL_DT)):
            _tick(controller, clock)
            if controller.state not in _BUSY:
                break
        else:
            raise ReportError(f"{motion} did not finish in {TRANSITION_LIMIT_S:g} s")
        for _ in range(round(HOLD_S / CONTROL_DT)):
            _tick(controller, clock)
    finally:
        sim.on_step = None
    return recorder.samples()


def _gait_command(motion: str, params: GaitParams) -> BodyVelocity:
    command = {
        "walk": BodyVelocity(vx=params.max_speed_m_s),
        "strafe": BodyVelocity(vy=params.max_speed_m_s),
        "turn": BodyVelocity(yaw_rate=params.max_yaw_rate_rad_s),
    }[motion]
    return gait.limit_command(command, params)


def _run_gait_motion(
    sim: SimBackend, motion: str, seconds: float, body_height_m: float
) -> tuple[Samples, int]:
    """walk / strafe / turn at the maximum command, ramped up as the controller does."""
    params = GaitParams(body_height_m=body_height_m)
    command = _gait_command(motion, params)
    _start_pose(sim, body_height_m)
    for _ in range(round(SETTLE_S / CONTROL_DT)):
        sim.advance(CONTROL_DT)
    recorder = Recorder(sim)
    sim.on_step = recorder
    phase, elapsed, scaled_ticks = 0.0, 0.0, 0
    try:
        while elapsed < seconds - 1e-9:
            ramp = min(1.0, elapsed / RAMP_S)
            step = gait.plan(
                phase, BodyVelocity(command.vx * ramp, command.vy * ramp, command.yaw_rate * ramp),
                params,
            )
            scaled_ticks += step.stride_scale < 1.0
            sim.set_joint_targets(step.joint_angles)
            stepped = sim.advance(CONTROL_DT)
            phase = (phase + stepped / params.period_s) % 1.0
            elapsed += stepped
    finally:
        sim.on_step = None
    return recorder.samples(), scaled_ticks


def measure(
    motion: str,
    *,
    max_force_nm: float,
    seconds: float = DEFAULT_WALK_S,
    mass_scale: float = 1.0,
    body_height_m: float = config.BODY_HEIGHT_STAND,
    velocity_gain: float | None = None,
) -> tuple[Samples, int]:
    """One run in a fresh DIRECT simulation: the samples and the stride-shortened tick count."""
    with scaled_masses(mass_scale), tempfile.TemporaryDirectory() as folder:
        urdf_path = Path(folder) / "hexapod.urdf"
        urdf_path.write_text(urdf.build_urdf())
        sim = SimBackend(
            gui=False, urdf_path=urdf_path, max_force_nm=max_force_nm, velocity_gain=velocity_gain
        )
        try:
            if motion in ("stand", "sit", "wave"):
                return _run_controller_motion(sim, motion), 0
            return _run_gait_motion(sim, motion, seconds, body_height_m)
        finally:
            sim.close()


def motion_result(
    motion: str, *, raised_cap_nm: float, nominal_cap_nm: float, seconds: float,
    velocity_gain: float | None = None,
) -> MotionResult:
    raised, _ = measure(
        motion, max_force_nm=raised_cap_nm, seconds=seconds, velocity_gain=velocity_gain
    )
    nominal, _ = measure(
        motion, max_force_nm=nominal_cap_nm, seconds=seconds, velocity_gain=velocity_gain
    )
    stats = summarize(raised, nominal_cap_nm)
    return MotionResult(
        motion=motion,
        seconds=len(raised.torques) / config.PHYSICS_HZ,
        raised=stats,
        nominal=summarize(nominal, nominal_cap_nm),
        near_peak=simultaneous(raised, stats),
    )


def sensitivity_row(
    label: str, *, mass_scale: float, body_height_m: float, raised_cap_nm: float, seconds: float
) -> SensitivityRow:
    samples, scaled = measure(
        "walk", max_force_nm=raised_cap_nm, seconds=seconds, mass_scale=mass_scale,
        body_height_m=body_height_m,
    )
    stats = summarize(samples, raised_cap_nm)
    return SensitivityRow(
        label=label,
        total_mass_kg=config.TOTAL_MASS_KG * mass_scale,
        body_height_m=body_height_m,
        peak_nm={kind: stats[kind].peak_nm for kind in JOINT_TYPES},
        rms_nm={kind: stats[kind].rms_nm for kind in JOINT_TYPES},
        ik_scaled_ticks=scaled,
    )


def build_report(
    motions: Sequence[str], *, seconds: float, raised_cap_nm: float,
    mass_scales: Sequence[float], heights_m: Sequence[float], sensitivity: bool = True,
) -> Report:
    report = Report(
        nominal_cap_nm=config.JOINT_MAX_FORCE_NM, raised_cap_nm=raised_cap_nm,
        velocity_gain=config.JOINT_VELOCITY_GAIN,
        velocity_cap_rad_s=config.JOINT_MAX_VELOCITY_RAD_S,
        total_mass_kg=config.TOTAL_MASS_KG, safety_factor=config.TORQUE_SAFETY_FACTOR,
    )
    for motion in motions:
        logger.info("measuring %s", motion)
        report.motions.append(motion_result(
            motion, raised_cap_nm=raised_cap_nm, nominal_cap_nm=config.JOINT_MAX_FORCE_NM,
            seconds=seconds))
    if sensitivity:
        for scale in mass_scales:
            logger.info("walk at %.1fx mass", scale)
            report.sensitivity.append(sensitivity_row(
                f"mass x{scale:g}", mass_scale=scale, body_height_m=config.BODY_HEIGHT_STAND,
                raised_cap_nm=raised_cap_nm, seconds=seconds))
        for height in heights_m:
            logger.info("walk at body height %.2f m", height)
            report.sensitivity.append(sensitivity_row(
                f"height {height:.2f} m", mass_scale=1.0, body_height_m=height,
                raised_cap_nm=raised_cap_nm, seconds=seconds))
        if "walk" in motions:
            logger.info("walk with velocity gain 1.0")
            report.gain_walk = motion_result(
                "walk", raised_cap_nm=raised_cap_nm, nominal_cap_nm=config.JOINT_MAX_FORCE_NM,
                seconds=seconds, velocity_gain=1.0)
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--motions", default=",".join(MOTIONS), help=f"from: {', '.join(MOTIONS)}")
    parser.add_argument("--seconds", type=float, default=DEFAULT_WALK_S,
                        help="length of each walk / strafe / turn run (sim seconds)")
    parser.add_argument("--raised-cap", type=float, default=RAISED_CAP_NM,
                        help="motor force cap for the measurement, N*m (default %(default)g)")
    parser.add_argument("--mass-scales", default=",".join(f"{s:g}" for s in DEFAULT_MASS_SCALES))
    parser.add_argument("--heights", default=",".join(f"{h:g}" for h in DEFAULT_HEIGHTS_M),
                        help="body heights (m) for the sensitivity table")
    parser.add_argument("--no-sensitivity", action="store_true",
                        help="skip the sensitivity table and the velocity-gain check")
    parser.add_argument("--json", type=Path, help="also write the raw report here")
    parser.add_argument("--markdown", type=Path, help="also write the markdown here")
    parser.add_argument("--render", type=Path,
                        help="print the report from this JSON; no simulation")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("body.gait").setLevel(logging.ERROR)  # one warning per shortened stride

    try:
        if args.render is not None:
            report = load_report(args.render)
        else:
            if args.seconds <= 0 or args.raised_cap <= config.JOINT_MAX_FORCE_NM:
                raise ReportError("--seconds must be positive, --raised-cap above the normal cap")
            report = build_report(
                parse_motions(args.motions), seconds=args.seconds, raised_cap_nm=args.raised_cap,
                mass_scales=parse_floats(args.mass_scales, "mass scale"),
                heights_m=parse_floats(args.heights, "height"),
                sensitivity=not args.no_sensitivity)
    except ReportError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    text = render_markdown(report)
    print(text)
    if args.json is not None:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(asdict(report), indent=2) + "\n")
    if args.markdown is not None:
        args.markdown.write_text(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
