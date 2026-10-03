"""``HexapodBackend`` abstract base class.

The swappable seam between simulation and real hardware. Both the PyBullet
backend and the servo backend implement this interface; the rest of ``body/``
does not care which one is loaded.
"""
