"""Hexapod body process: kinematics, gait, controller and backends."""

import os

# Small numpy arrays gain nothing from BLAS threads, and the spinning threads starve the
# control loop (measured: 7x slower ticks on the 2-core dev laptop). Must run before numpy loads.
for _var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_var, "1")
