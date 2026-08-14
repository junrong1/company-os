"""The kernel library: pure simulation, no service, transport or store.

R5 is the constraint that shapes this package. Nothing here may import a service
module, a transport library, or the store — the whole thing has to be importable
and runnable headless. That is what lets the determinism, parity and replay suites
run without Docker, and it is asserted by `tests/test_import_boundaries.py` rather
than left to good intentions.

Contents arrive in order: determinism primitives (U3), the ported simulation
(U4), fold and snapshots (U15), then the new mechanics (U7) and lifecycle (U8).
"""
