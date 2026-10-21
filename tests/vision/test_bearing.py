"""Bearing maths: angle of the face centre from the camera axis (+ left, - right)."""

from __future__ import annotations

import pytest

from vision.detector import bearing_deg

WIDTH = 320.0


def box_centred_at(x: float) -> tuple[float, float, float, float]:
    return (x - 20.0, 50.0, 40.0, 40.0)


def test_a_centred_face_is_straight_ahead() -> None:
    assert bearing_deg(box_centred_at(WIDTH / 2), WIDTH, 60.0) == pytest.approx(0.0, abs=1e-9)


def test_the_image_edges_are_half_the_field_of_view() -> None:
    assert bearing_deg(box_centred_at(0.0), WIDTH, 60.0) == pytest.approx(30.0)
    assert bearing_deg(box_centred_at(WIDTH), WIDTH, 60.0) == pytest.approx(-30.0)


def test_right_of_centre_is_negative_and_left_is_positive() -> None:
    assert bearing_deg(box_centred_at(250.0), WIDTH, 60.0) < 0
    assert bearing_deg(box_centred_at(70.0), WIDTH, 60.0) > 0


def test_symmetric_and_monotonic() -> None:
    right = bearing_deg(box_centred_at(WIDTH / 2 + 60), WIDTH, 60.0)
    left = bearing_deg(box_centred_at(WIDTH / 2 - 60), WIDTH, 60.0)
    assert right == pytest.approx(-left)
    assert bearing_deg(box_centred_at(WIDTH / 2 + 100), WIDTH, 60.0) < right


def test_a_wider_field_of_view_gives_a_larger_angle_at_the_same_pixel() -> None:
    narrow = abs(bearing_deg(box_centred_at(250.0), WIDTH, 50.0))
    wide = abs(bearing_deg(box_centred_at(250.0), WIDTH, 90.0))
    assert wide > narrow


def test_the_width_of_the_frame_does_not_matter_only_the_fraction() -> None:
    small = bearing_deg((200.0, 0.0, 40.0, 40.0), 320.0, 60.0)
    big = bearing_deg((400.0, 0.0, 80.0, 80.0), 640.0, 60.0)
    assert small == pytest.approx(big)
