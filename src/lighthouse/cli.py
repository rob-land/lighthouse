"""Entry-point dispatcher.

A single ``lighthouse`` binary runs three roles, selected by the first
argument (matching the cohort's one-launcher convention):

* ``lighthouse``            → the GUI (readiness + device viewer)
* ``lighthouse agent``      → the headless ``lighthoused`` daemon
* ``lighthouse beam ...``   → the "I'm here" ring surface

Splitting these into separately-installed binaries is a later cleanup
(TODO.md); for now they share the launcher and gi.require_version block.
"""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    sub = argv[1] if len(argv) > 1 else None

    if sub == "agent":
        from lighthouse.agent import run_agent
        return run_agent(argv[2:])
    if sub == "beam":
        from lighthouse.beam import run_beam
        return run_beam(argv[2:])

    # Default role: the GUI. Allow an explicit "gui" subcommand too.
    from lighthouse import main as gui
    gui_argv = [argv[0], *argv[2:]] if sub == "gui" else argv
    return gui.main(gui_argv)
