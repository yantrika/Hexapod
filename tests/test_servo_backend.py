"""ServoBackend with fake drivers and a fake clock: the single path to a servo, the staggered
attach, the slew limit over time, the idle release and the refusals."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np
import pytest

import config
from body import poses, servo_backend
from body import servo_calibration as sc
from body.servo_backend import ServoBackend
from body.servo_calibration import CalibrationError
from body.servo_drivers import PulseDriver
from tests.servo_fakes import FakeDriver, FakeOe, World, calibrated, good_data

RATE_US_S = config.SERVO_SLEW_DEG_S * config.SERVO_US_PER_DEG


def make(world: World | None = None, **kwargs: Any) -> tuple[ServoBackend, World,
                                                              dict[str, FakeDriver]]:
    world = world or World()
    fakes = {"pca": FakeDriver(world, "pca"), "gpio": FakeDriver(world, "gpio")}
    drivers: dict[str, PulseDriver] = dict(fakes)
    kwargs.setdefault("calibration", calibrated())
    backend = ServoBackend(drivers=drivers, clock=world.now, sleep=world.sleep, **kwargs)
    return backend, world, fakes


def pose(**degrees: float) -> np.ndarray:
    """Targets in radians, zeros except the named joints (degrees)."""
    target = np.zeros(config.DOF)
    for name, value in degrees.items():
        target[config.JOINT_NAMES.index(name)] = math.radians(value)
    return target


def run(backend: ServoBackend, world: World, target: np.ndarray, seconds: float,
        step: float = 0.02) -> None:
    for _ in range(round(seconds / step)):
        backend.set_joint_targets(target)
        world.tick(step)


# --- refusals ------------------------------------------------------------------------------------
def test_it_refuses_to_start_without_a_calibration_file(monkeypatch: pytest.MonkeyPatch,
                                                        tmp_path: Path) -> None:
    monkeypatch.setattr(config, "SERVO_CAL_FILE", tmp_path / "missing.json")
    with pytest.raises(CalibrationError, match="not found"):
        ServoBackend(drivers={})


def test_it_refuses_a_joint_that_is_not_calibrated() -> None:
    data = good_data()
    data["joints"]["LM_femur"]["calibrated"] = False
    cal = sc.parse(data)
    with pytest.raises(CalibrationError, match="LM_femur"):
        make(calibration=cal)
    backend, _, _ = make(calibration=cal, joints=["RF_coxa"])  # a calibrated subset is fine
    assert backend.joint_names == ("RF_coxa",)


def test_the_committed_file_starts_only_when_every_joint_is_calibrated() -> None:
    cal = sc.load()
    world = World()
    drivers: dict[str, PulseDriver] = {"pca": FakeDriver(world, "pca"),
                                       "gpio": FakeDriver(world, "gpio")}
    if cal.uncalibrated():
        with pytest.raises(CalibrationError):
            ServoBackend(cal, drivers=drivers, clock=world.now)
    else:
        ServoBackend(cal, drivers=drivers, clock=world.now)


def test_uncalibrated_joints_need_the_flag_and_an_explicit_list() -> None:
    cal = sc.parse(good_data(False))
    with pytest.raises(CalibrationError):
        make(calibration=cal, joints=["RF_coxa"])
    with pytest.raises(CalibrationError, match="explicit"):
        make(calibration=cal, allow_uncalibrated=True)
    backend, _, _ = make(calibration=cal, joints=["RF_coxa"], allow_uncalibrated=True)
    assert backend.joint_names == ("RF_coxa",)


def test_bad_joint_lists_are_refused() -> None:
    for joints in ([], ["nope"], ["RF_coxa", "nope"]):
        with pytest.raises(ValueError):
            make(joints=joints)


# --- attach ----------------------------------------------------------------------------------
def test_nothing_is_written_until_the_first_command() -> None:
    backend, world, _ = make()
    assert backend.released and world.events == []


def test_servos_attach_one_leg_at_a_time_each_at_its_centre_before_any_motion() -> None:
    backend, world, _ = make()
    start = world.now()
    backend.set_joint_targets(poses.SIT_ANGLES)
    cal = calibrated()
    attach = [(t, d, ch, us) for t, d, kind, ch, us in world.events if kind == "pulse"]
    assert len(attach) == config.DOF  # every joint written once while attaching ...
    for leg_index, leg in enumerate(config.LEG_NAMES):
        for joint in config.JOINTS_PER_LEG:
            name = f"{leg}_{joint}"
            driver, channel = cal[name].output
            [(t, us)] = world.writes(driver, channel)
            assert t == pytest.approx(start + leg_index * config.SERVO_STAGGER_S)
            assert us == pytest.approx(cal[name].pulse_us(0.0))  # the calibrated centre
    assert world.writes("gpio", 0)[0][0] == pytest.approx(  # RR_coxa waits for the RR turn
        start + 2 * config.SERVO_STAGGER_S)
    assert not backend.released


def test_the_first_pose_is_reached_by_slewing_from_the_centre() -> None:
    backend, world, drivers = make()
    run(backend, world, poses.SIT_ANGLES, 3.0)
    assert backend.get_joint_angles() == pytest.approx(poses.SIT_ANGLES.reshape(-1), abs=1e-6)
    cal = calibrated()
    name = "RF_femur"
    driver, channel = cal[name].output
    assert drivers[driver].pulses[channel] == pytest.approx(
        cal[name].pulse_us(math.degrees(poses.SIT_ANGLES[0, 1])))


def test_stagger_covers_only_the_legs_of_the_selected_joints() -> None:
    backend, world, _ = make(joints=["RF_coxa", "LF_tibia"])
    start = world.now()
    backend.set_joint_targets(np.zeros(config.DOF))
    times = sorted(t for t, *_ in world.events)
    assert times == [pytest.approx(start), pytest.approx(start + config.SERVO_STAGGER_S)]


def test_joints_not_selected_stay_released() -> None:
    backend, world, _ = make(joints=["RF_coxa"])
    run(backend, world, pose(RF_coxa=20, RM_coxa=30), 1.0)
    cal = calibrated()
    written = {(d, ch) for _, d, kind, ch, _ in world.events if kind == "pulse"}
    assert written == {cal["RF_coxa"].output}
    assert backend.get_joint_angles()[config.JOINT_NAMES.index("RM_coxa")] == 0.0


# --- the clamp layers ----------------------------------------------------------------------------
def test_hard_limits_clamp_before_the_backend_and_close_releases_everything() -> None:
    backend, world, drivers = make()
    applied = backend.set_joint_targets(np.full(config.DOF, 10.0))
    assert np.all(np.abs(applied) <= np.radians(90.0) + 1e-12)
    backend.close()
    assert all(d.releases == 1 for d in drivers.values())
    backend.close()  # idempotent
    assert all(d.releases == 1 for d in drivers.values())
    with pytest.raises(RuntimeError):
        backend.set_joint_targets(np.zeros(config.DOF))


def test_the_joint_window_limits_the_pulse_not_only_the_angle() -> None:
    data = good_data()
    data["joints"]["RF_coxa"].update(min_us=1400, max_us=1600)
    backend, world, drivers = make(calibration=sc.parse(data))
    run(backend, world, pose(RF_coxa=80), 3.0)
    sign = data["joints"]["RF_coxa"]["sign"]
    assert drivers["pca"].pulses[0] == (1600 if sign == 1 else 1400)  # 80 deg is far past it


def test_every_pulse_stays_inside_the_absolute_window() -> None:
    backend, world, _ = make()
    rng = np.random.default_rng(3)
    for _ in range(300):
        backend.set_joint_targets(rng.uniform(-3.0, 3.0, config.DOF))
        world.tick()
    pulses = [us for *_, kind, _ch, us in world.events if kind == "pulse"]
    assert min(pulses) >= config.SERVO_PULSE_ABS_MIN_US
    assert max(pulses) <= config.SERVO_PULSE_ABS_MAX_US


@pytest.mark.parametrize("name", config.JOINT_NAMES)
def test_each_joint_follows_its_own_sign_through_the_backend(name: str) -> None:
    cal = calibrated()
    backend, world, drivers = make(joints=[name], calibration=cal)
    run(backend, world, pose(**{name: 12.0}), 2.0)
    driver, channel = cal[name].output
    assert drivers[driver].pulses[channel] == pytest.approx(cal[name].pulse_us(12.0))
    assert math.degrees(backend.get_joint_angles()[config.JOINT_NAMES.index(name)]) == \
        pytest.approx(12.0)


# --- slew over time -------------------------------------------------------------------------
def assert_rate_never_exceeded(world: World, driver: str, channel: int) -> None:
    writes = world.writes(driver, channel)
    for (t0, p0), (t1, p1) in zip(writes, writes[1:], strict=False):
        allowed = RATE_US_S * min(t1 - t0, config.SERVO_SLEW_MAX_DT_S)
        assert abs(p1 - p0) <= allowed + 1e-6, (t0, t1, p0, p1)


@pytest.mark.parametrize("step", [0.02, 0.005, 0.001])
def test_the_rate_is_never_exceeded_whatever_the_call_rate(step: float) -> None:
    backend, world, _ = make(joints=["RF_coxa"])
    far = pose(RF_coxa=70)
    run(backend, world, np.zeros(config.DOF), 0.5, step)  # attach at the centre
    run(backend, world, far, 0.3, step)
    assert_rate_never_exceeded(world, "pca", 0)
    moved = abs(backend.pulse_of("RF_coxa") - calibrated()["RF_coxa"].pulse_us(0.0))
    assert moved == pytest.approx(RATE_US_S * 0.3, rel=0.02)  # saturated: neither more nor less


def test_a_stalled_loop_cannot_make_one_big_jump() -> None:
    backend, world, _ = make(joints=["RF_coxa"])
    backend.set_joint_targets(np.zeros(config.DOF))
    world.tick(5.0)  # the loop froze for five seconds
    backend.set_joint_targets(pose(RF_coxa=80))
    assert_rate_never_exceeded(world, "pca", 0)
    step = abs(backend.pulse_of("RF_coxa") - calibrated()["RF_coxa"].pulse_us(0.0))
    assert step <= RATE_US_S * config.SERVO_SLEW_MAX_DT_S + 1e-6


def test_many_calls_at_the_same_instant_do_not_add_up() -> None:
    backend, world, _ = make(joints=["RF_coxa"])
    backend.set_joint_targets(np.zeros(config.DOF))
    world.tick(0.02)
    for _ in range(50):
        backend.set_joint_targets(pose(RF_coxa=80))
    moved = abs(backend.pulse_of("RF_coxa") - calibrated()["RF_coxa"].pulse_us(0.0))
    assert moved <= RATE_US_S * 0.02 + 1e-6


# --- idle release ---------------------------------------------------------------------------------
def test_sitting_idle_releases_every_channel_and_the_next_command_reattaches_staggered() -> None:
    backend, world, drivers = make()
    run(backend, world, poses.SIT_ANGLES, config.SERVO_IDLE_RELEASE_S - 2.0)
    assert not backend.released  # not yet
    run(backend, world, poses.SIT_ANGLES, 4.0)
    assert backend.released
    assert all(d.releases == 1 for d in drivers.values())
    held = backend.get_joint_angles()
    count = len(world.events)
    run(backend, world, poses.SIT_ANGLES, 3.0)  # still sitting: stays released, writes nothing
    assert len(world.events) == count and backend.released

    before = len(world.events)
    start = world.now()
    backend.set_joint_targets(poses.STAND_ANGLES)  # stand up: re-attach
    attach = world.events[before:]
    assert not backend.released
    legs_t = sorted({round(t - start, 3) for t, _, kind, *_ in attach if kind == "pulse"})
    assert legs_t == [round(i * config.SERVO_STAGGER_S, 3) for i in range(6)]  # one leg at a time
    cal = calibrated()
    for _t, d, _kind, ch, us in attach:  # re-attached at the HELD sit pose, not at the centre
        name = next(n for n in config.JOINT_NAMES if cal[n].output == (d, ch))
        assert us == pytest.approx(cal[name].pulse_us(
            math.degrees(held[config.JOINT_NAMES.index(name)])))
    run(backend, world, poses.STAND_ANGLES, 3.0)
    assert backend.get_joint_angles() == pytest.approx(poses.STAND_ANGLES.reshape(-1), abs=1e-6)


def test_a_standing_robot_is_never_released() -> None:
    backend, world, drivers = make()
    run(backend, world, poses.STAND_ANGLES, config.SERVO_IDLE_RELEASE_S * 3)
    assert not backend.released and all(d.releases == 0 for d in drivers.values())


def test_a_moving_robot_is_not_released_on_the_way_to_sit() -> None:
    backend, world, _ = make()
    run(backend, world, poses.SIT_ANGLES, 1.0)
    run(backend, world, pose(RF_coxa=5), 1.0)  # a small move resets the idle timer
    assert not backend.released


def test_idle_release_can_be_switched_off() -> None:
    backend, world, _ = make(idle_release_s=None)
    run(backend, world, poses.SIT_ANGLES, config.SERVO_IDLE_RELEASE_S * 2)
    assert not backend.released


# --- output enable and failures ----------------------------------------------------------------
def test_oe_is_enabled_after_setup_and_disabled_first_on_close() -> None:
    world = World()
    oe = FakeOe(world)
    backend, _, drivers = make(world, output_enable=oe)
    assert oe.log == ["enable"]
    backend.set_joint_targets(np.zeros(config.DOF))
    backend.close()
    assert oe.log == ["enable", "disable", "close"]
    kinds = [kind for _, _, kind, *_ in world.events if kind in ("disable", "release")]
    assert kinds[0] == "disable"  # OE goes first: the quickest way to limp
    assert drivers["pca"].releases == 1


def test_a_failing_setup_releases_what_was_created() -> None:
    class BadOe(FakeOe):
        def enable(self) -> None:
            raise OSError("gpio busy")

    world = World()
    oe = BadOe(world)
    with pytest.raises(OSError):
        make(world, output_enable=oe)
    assert oe.log[:2] == ["disable", "close"]


def test_building_the_drivers_uses_only_what_the_joints_need(
        monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[set[str], int | None]] = []
    world = World()

    def fake_build(used: set[str], oe_gpio: int | None = None):  # type: ignore[no-untyped-def]
        seen.append((set(used), oe_gpio))
        return {name: FakeDriver(world, name) for name in used}, None

    monkeypatch.setattr(servo_backend, "build_drivers", fake_build)
    monkeypatch.setattr(config, "PCA9685_OE_GPIO", 17)
    ServoBackend(calibrated(), joints=["RF_coxa"], clock=world.now)
    ServoBackend(calibrated(), joints=["RR_coxa"], clock=world.now)
    ServoBackend(calibrated(), clock=world.now)
    assert seen == [({"pca"}, 17), ({"gpio"}, 17), ({"pca", "gpio"}, 17)]


def test_close_never_raises_even_when_a_driver_fails() -> None:
    backend, _, drivers = make()
    backend.set_joint_targets(np.zeros(config.DOF))
    drivers["pca"].fail_release = True
    backend.close()
    assert drivers["gpio"].releases == 1  # the other driver was still released


def test_release_outputs_reports_a_failed_driver_but_releases_the_rest() -> None:
    backend, _, drivers = make()
    backend.set_joint_targets(np.zeros(config.DOF))
    drivers["pca"].fail_release = True
    with pytest.raises(RuntimeError):
        backend.release_outputs()
    assert drivers["gpio"].releases == 1 and backend.released


# --- calibration mode -----------------------------------------------------------------------
def test_calibration_mode_starts_at_the_stored_centre_and_slews_a_raw_pulse() -> None:
    cal = sc.parse(good_data(False))
    backend, world, drivers = make(calibration=cal, calibration_joint="RF_femur")
    backend.attach()
    [(_, first)] = world.writes("pca", 1)
    assert first == cal["RF_femur"].centre_us  # from its centre, with no offset applied
    backend.set_raw_pulse("RF_femur", 1700)
    for _ in range(100):
        world.tick(0.02)
        backend.refresh()
    assert drivers["pca"].pulses[1] == pytest.approx(1700)
    assert_rate_never_exceeded(world, "pca", 1)


def test_calibration_mode_drives_one_joint_only_and_by_raw_pulse_only() -> None:
    cal = sc.parse(good_data(False))
    backend, world, _ = make(calibration=cal, calibration_joint="RF_femur")
    with pytest.raises(RuntimeError):
        backend.set_joint_targets(np.zeros(config.DOF))
    with pytest.raises(RuntimeError):
        backend.set_raw_pulse("RF_coxa", 1500)
    for bad in (100.0, 3000.0, float("nan")):
        with pytest.raises(ValueError):
            backend.set_raw_pulse("RF_femur", bad)
    other, _, _ = make(calibration=cal, joints=["RF_femur"], allow_uncalibrated=True)
    with pytest.raises(RuntimeError):
        other.set_raw_pulse("RF_femur", 1500)  # raw pulses only in calibration mode
    assert world.events == []
