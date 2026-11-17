# Test baseline for the witness-marks branch

**NOT FOR UPSTREAM**, the same as everything else in this directory.

This lived in a session scratchpad until 2026-11-17, which defeated its whole
purpose: a baseline exists so that "did I break something" is a DIFF rather
than a recollection, and one that evaporates with the session is a
recollection with extra steps. It is here now, beside the mutation records,
because it documents this branch's state and not the project's.

# openscad-mcp baseline on this machine

Fork: calebpower/openscad-mcp @ witness-marks, bde0b23
Four commits ahead of our pin (258c8a4 = 0.6.1), all CI/test infrastructure.

    uv run pytest -m "not performance"
    7 failed, 1520 passed, 8 skipped, 24 deselected, coverage 89.41%

## The 7 are OpenSCAD VERSION DRIFT, not defects

Their CI pins OpenSCAD **2021.01**. This box has **2026.10.14**, which defaults
to the Manifold backend and emits **no CGAL banner at all** -- measured:

    $ openscad -o c.stl c.scad
    Geometries in cache: 1

No `Simple:`, no `Volumes:`. Their `diagnostics.py` keys mesh_health on those
CGAL statistics, so `manifold` is `None` -- which is correct by their own
design ("null when OpenSCAD did not perform the check") and fails a test that
asserts `is True`.

BOSL2 IS installed, at **2.0.716** (~/.local/share/OpenSCAD/libraries/BOSL2,
`BOSL_VERSION` in version.scad), so the BOSL2 failures are not a missing
library.

THE BOSL2 VERSION IS PART OF THIS BASELINE'S PROVENANCE and was missing from
it until 2026-11-17 (issue 58 step 2). Three of the seven failures are
BOSL2-related -- TestBosl2Lint x2 and TestAnchors -- so a baseline recording
only the OpenSCAD version cannot distinguish "the same 7 failures" from "7
failures on a different library". BOSL2 is pinned NOWHERE; it is whatever is
installed. Our own fixtures reach no library at all, which
tools/tests/t_fixtures_self_contained.sh now asserts, so this affects the
fork's baseline and no figure we have recorded.

## The baseline, named

    FAILED tests/test_evals.py::TestReferenceSelfTest::test_reference_command_exits_zero
    FAILED tests/test_evals.py::TestReferenceSelfTest::test_reference_solutions_score_100_percent
    FAILED tests/test_geometry_tools.py::TestToolsReal::test_composite_modes_work_on_bosl2_files
    FAILED tests/test_geometry_tools.py::TestToolsReal::test_measure_model
    FAILED tests/test_phase4_tools.py::TestAnchors::test_anchor_frames_follow_placement
    FAILED tests/test_phase4_tools.py::TestBosl2Lint::test_autofix_rewrites_when_safe
    FAILED tests/test_phase4_tools.py::TestBosl2Lint::test_lint_flags_attach_child_from_used_file

## How to use it

Any change of mine must not ADD to this list. Compare, never recall:

    uv run pytest -m "not performance" -q 2>&1 | grep '^FAILED' | sed 's/ - .*//' | sort \
      | diff - /tmp/claude-1001/-data-projects-tech-code-iot-merlin/6bf09d48-0b4c-476c-b37f-a1210adab1ad/scratchpad/provider-baseline-failures.txt

## Tool-surface headroom, measured by their own test's method

    TOTAL 20304 of 21000  ->  696 chars left
    measure 3114/3600 (486 left)   check 3176/3600 (424 left)
    validate 2202/3600 (1398 left)
