"""Step 10c: the torque report. Parsing, units, statistics and rendering need no simulation;
one short DIRECT-mode run checks that the script itself works end to end."""

from __future__ import annotations

import json
import math
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("pybullet")  # the simulator is not installed on the Pi

import config  # noqa: E402
from body.sim_backend import SimBackend  # noqa: E402
from scripts import torque_report as tr  # noqa: E402


# --- units -----------------------------------------------------------------------------------
def test_one_newton_metre_is_10_197_kg_cm() -> None:
    assert tr.nm_to_kgcm(1.0) == pytest.approx(10.197)
    assert tr.nm_to_kgcm(0.5) == pytest.approx(5.0985)


def test_kg_cm_round_trips() -> None:
    for value in (0.0, 0.3, 1.0, 12.5):
        assert tr.kgcm_to_nm(tr.nm_to_kgcm(value)) == pytest.approx(value)


def test_seconds_per_60_degrees() -> None:
    assert tr.seconds_per_60deg(math.pi / 3) == pytest.approx(1.0)
    assert tr.seconds_per_60deg(2 * math.pi / 3) == pytest.approx(0.5)
    assert math.isinf(tr.seconds_per_60deg(0.0))


def test_the_config_has_the_placeholders_and_the_safety_factor() -> None:
    assert config.TORQUE_SAFETY_FACTOR == 2.0
    expected = config.BODY_MASS_KG + len(config.LEG_NAMES) * sum(config.LINK_MASS_KG.values())
    assert config.TOTAL_MASS_KG == pytest.approx(expected)
    assert set(config.LINK_MASS_KG) == {"coxa", "femur", "tibia", "foot"}
    assert "MASSES ARE PLACEHOLDERS" in Path(config.__file__).read_text()


# --- statistics on synthetic samples ---------------------------------------------------------
def samples(torque: np.ndarray, velocity: np.ndarray | None = None,
            angle: np.ndarray | None = None) -> tr.Samples:
    zeros = np.zeros_like(torque)
    return tr.Samples(
        angles=zeros if angle is None else angle,
        velocities=zeros if velocity is None else velocity,
        torques=torque,
    )


def test_summarize_groups_columns_by_joint_type() -> None:
    torque = np.zeros((100, config.DOF))
    torque[10, 0] = -2.0  # RF_coxa (column 0), negative: only |torque| counts
    torque[20, 4] = 1.0  # RM_femur (column 4)
    torque[30, 8] = 0.5  # RR_tibia (column 8)
    stats = tr.summarize(samples(torque), cap_nm=3.0)
    assert stats["coxa"].peak_nm == pytest.approx(2.0)
    assert stats["femur"].peak_nm == pytest.approx(1.0)
    assert stats["tibia"].peak_nm == pytest.approx(0.5)


def test_summarize_rms_speed_and_angle_range() -> None:
    torque = np.full((50, config.DOF), 2.0)
    velocity = np.zeros((50, config.DOF))
    velocity[5, 1] = -4.0  # a femur
    angle = np.zeros((50, config.DOF))
    angle[0, 2] = math.radians(-20.0)  # a tibia
    angle[1, 2] = math.radians(35.0)
    stats = tr.summarize(samples(torque, velocity, angle), cap_nm=3.0)
    assert stats["femur"].rms_nm == pytest.approx(2.0)
    assert stats["femur"].peak_speed_rad_s == pytest.approx(4.0)
    assert stats["femur"].s_per_60deg == pytest.approx(math.radians(60) / 4.0)
    assert stats["tibia"].angle_min_deg == pytest.approx(-20.0)
    assert stats["tibia"].angle_max_deg == pytest.approx(35.0)


def test_p99_ignores_one_spike() -> None:
    torque = np.full((1000, config.DOF), 0.5)
    torque[500, 0] = 9.0
    stats = tr.summarize(samples(torque), cap_nm=30.0)["coxa"]
    assert stats.peak_nm == pytest.approx(9.0)
    assert stats.p99_nm == pytest.approx(0.5)


def test_saturation_is_reported() -> None:
    torque = np.zeros((100, config.DOF))
    torque[:10, 4] = 3.0  # a femur pinned at the 3 N*m cap for 10 samples
    stats = tr.summarize(samples(torque), cap_nm=3.0)
    assert tr.hit_cap(stats["femur"])
    assert not tr.hit_cap(stats["coxa"])
    # one joint of six, 10 of 100 samples: 10 / (100 * 6) of the femur samples
    assert stats["femur"].at_cap_fraction == pytest.approx(10 / 600)


def test_no_saturation_below_the_cap() -> None:
    torque = np.full((10, config.DOF), 2.9)
    assert not any(tr.hit_cap(s) for s in tr.summarize(samples(torque), cap_nm=3.0).values())


def test_speed_cap_is_flagged() -> None:
    velocity = np.zeros((10, config.DOF))
    velocity[0, 0] = config.JOINT_MAX_VELOCITY_RAD_S
    stats = tr.summarize(samples(np.zeros((10, config.DOF)), velocity), cap_nm=3.0)
    assert tr.speed_capped(stats["coxa"])
    assert not tr.speed_capped(stats["femur"])


def test_simultaneous_counts_joints_near_peak() -> None:
    torque = np.zeros((20, config.DOF))
    torque[3, :] = 1.0  # all 18 joints loaded at once at step 3
    torque[7, 0] = 1.0  # a lone joint later
    stats = tr.summarize(samples(torque), cap_nm=30.0)
    near = tr.simultaneous(samples(torque), stats)
    assert near.max_vs_peak == config.DOF
    assert near.max_vs_p99 >= 1


# --- parsing ---------------------------------------------------------------------------------
def test_parse_floats() -> None:
    assert tr.parse_floats("0.7, 1.0,1.3") == [0.7, 1.0, 1.3]
    assert tr.parse_floats("2") == [2.0]


@pytest.mark.parametrize("text", ["", " , ", "abc", "1,x", "0", "-1", "nan", "inf"])
def test_parse_floats_rejects_bad_input(text: str) -> None:
    with pytest.raises(tr.ReportError):
        tr.parse_floats(text)


def test_parse_motions() -> None:
    assert tr.parse_motions("walk, turn") == ["walk", "turn"]
    assert tr.parse_motions(",".join(tr.MOTIONS)) == list(tr.MOTIONS)
    for bad in ("", "fly", "walk,fly"):
        with pytest.raises(tr.ReportError):
            tr.parse_motions(bad)


def type_stats(
    peak: float = 1.0, at_cap: float = 0.0, speed: float = 3.0
) -> dict[str, tr.TypeStats]:
    return {
        kind: tr.TypeStats(peak, peak / 2, peak * 0.8, speed, -10.0, 20.0, at_cap)
        for kind in tr.JOINT_TYPES
    }


def a_report(saturated: bool = False) -> tr.Report:
    near = tr.Simultaneous(12, 9, 14, 12)
    motion = tr.MotionResult(
        "walk", 8.0, raised=type_stats(1.0), nominal=type_stats(1.0, 0.1 if saturated else 0.0),
        near_peak=near)
    return tr.Report(
        nominal_cap_nm=3.0, raised_cap_nm=30.0, velocity_gain=0.3, velocity_cap_rad_s=6.0,
        total_mass_kg=1.44, safety_factor=2.0, motions=[motion],
        sensitivity=[tr.SensitivityRow(
            "mass x1.3", 1.87, 0.13, {k: 1.2 for k in tr.JOINT_TYPES},
            {k: 0.4 for k in tr.JOINT_TYPES}, 0)],
        gain_walk=motion)


def test_report_survives_json() -> None:
    report = a_report(saturated=True)
    assert tr.report_from_dict(json.loads(json.dumps(asdict(report)))) == report


def test_report_without_the_optional_parts() -> None:
    data = asdict(a_report())
    del data["sensitivity"]
    data["gain_walk"] = None
    report = tr.report_from_dict(data)
    assert report.sensitivity == [] and report.gain_walk is None


@pytest.mark.parametrize("breakage", ["not a dict", "missing", "bad_stats", "bad_number"])
def test_bad_report_files_are_rejected_with_the_reason(breakage: str) -> None:
    data: object = asdict(a_report())
    assert isinstance(data, dict)
    if breakage == "not a dict":
        data = [1, 2]
    elif breakage == "missing":
        del data["safety_factor"]
    elif breakage == "bad_stats":
        del data["motions"][0]["raised"]["femur"]
    else:
        data["total_mass_kg"] = "heavy"
    with pytest.raises(tr.ReportError):
        tr.report_from_dict(data)


def test_load_report_rejects_invalid_json(tmp_path: Path) -> None:
    path = tmp_path / "r.json"
    path.write_text("{ nope")
    with pytest.raises(tr.ReportError, match="not valid JSON"):
        tr.load_report(path)


# --- rendering -------------------------------------------------------------------------------
def test_markdown_shows_both_units_with_and_without_the_safety_factor() -> None:
    text = tr.render_markdown(a_report())
    assert "10.2" in text  # 1.0 N*m peak in kg*cm
    assert "20.4" in text  # 1.0 N*m x 2 in kg*cm
    assert "x2" in text and "kg*cm" in text
    assert "s per 60 deg" in text


def test_markdown_states_the_limits_and_the_placeholders() -> None:
    text = tr.render_markdown(a_report())
    for phrase in ("no gear friction", "backlash", "lowered velocity gain", "PLACEHOLDER",
                   "config.BODY_MASS_KG", "config.LINK_MASS_KG", "config.TORQUE_SAFETY_FACTOR"):
        assert phrase in text, phrase


def test_markdown_reports_saturation_either_way() -> None:
    assert "Saturated in the normal config: walk/coxa" in tr.render_markdown(a_report(True))
    assert "No joint hit the cap" in tr.render_markdown(a_report(False))


def test_markdown_has_the_sensitivity_and_simultaneous_tables() -> None:
    text = tr.render_markdown(a_report())
    assert "mass x1.3" in text
    assert "Joints near peak torque at the same time" in text
    assert "velocity gain" in text.lower()


def test_worst_case_is_the_maximum_over_motions() -> None:
    report = a_report()
    report.motions.append(tr.MotionResult(
        "wave", 4.0, raised=type_stats(2.5), nominal=type_stats(2.5),
        near_peak=tr.Simultaneous(1, 1, 1, 1)))
    worst = tr.worst_case(report)
    assert worst["femur"]["peak_nm"] == pytest.approx(2.5)


def test_render_option_prints_a_saved_report_without_simulating(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "r.json"
    path.write_text(json.dumps(asdict(a_report())))
    assert tr.main(["--render", str(path)]) == 0
    assert "## 3. Requirement per joint type" in capsys.readouterr().out


def test_bad_arguments_exit_with_one(capsys: pytest.CaptureFixture[str]) -> None:
    assert tr.main(["--motions", "fly"]) == 1
    assert tr.main(["--raised-cap", "2"]) == 1
    assert tr.main(["--render", "/nonexistent/report.json"]) == 1
    assert capsys.readouterr().err.count("error:") == 3


def test_scaled_masses_are_restored() -> None:
    body, links = config.BODY_MASS_KG, dict(config.LINK_MASS_KG)
    with tr.scaled_masses(1.3):
        assert config.BODY_MASS_KG == pytest.approx(body * 1.3)
        assert config.LINK_MASS_KG["femur"] == pytest.approx(links["femur"] * 1.3)
    assert config.BODY_MASS_KG == body and config.LINK_MASS_KG == links


# --- the simulation --------------------------------------------------------------------------
def test_sim_force_cap_limits_the_applied_torque_and_the_hook_sees_every_step() -> None:
    steps = []
    capped = SimBackend(gui=False, max_force_nm=0.05)
    free = SimBackend(gui=False, max_force_nm=30.0)
    try:
        capped.on_step = lambda: steps.append(1)
        capped.run_for(0.5)
        free.run_for(0.5)
        assert len(steps) == round(0.5 * config.PHYSICS_HZ)
        assert np.abs(capped.joint_states()[2]).max() <= 0.05 + 1e-6
        assert np.abs(free.joint_states()[2]).max() > 0.05  # the stand pose needs more than that
    finally:
        capped.close()
        free.close()


def test_the_script_runs_a_short_walk_end_to_end(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "report.json"
    code = tr.main(["--motions", "walk", "--seconds", "2", "--no-sensitivity", "--json", str(out)])
    assert code == 0
    assert "## 1. What each joint type needs" in capsys.readouterr().out
    report = tr.load_report(out)
    walk = report.motions[0]
    assert walk.motion == "walk"
    for kind in tr.JOINT_TYPES:
        assert 0.0 < walk.raised[kind].peak_nm < report.raised_cap_nm
        assert walk.raised[kind].peak_speed_rad_s > 0.0
        assert walk.raised[kind].rms_nm <= walk.raised[kind].peak_nm
    assert report.total_mass_kg == pytest.approx(config.TOTAL_MASS_KG)
