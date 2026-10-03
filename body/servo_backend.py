"""Real-servo backend for the Raspberry Pi. Not implemented yet."""

from __future__ import annotations


class ServoBackend:
    """Placeholder for the Pi servo backend; implemented at the hardware step."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        raise NotImplementedError(
            "ServoBackend is not implemented yet; use the simulation backend."
        )
