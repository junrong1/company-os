"""The bench: what a director is shown, what it says, and the guards over both.

Nothing in this package reads the store. `context.retrieve` is the only door onto the log and it
takes an authorized scope it does not compute, which is what makes R23's "no reachable unscoped
variant from bench code" a property of the package rather than a rule somebody has to remember —
`tests/test_pending_input.py` reads these files to keep it true.

U11 fills in the personas, the prompts and the two output guards. The guards themselves live in
`simcore.statement`, because the kernel runs them too and one implementation with two call sites is
the only shape that cannot drift (execution decision §1).
"""
