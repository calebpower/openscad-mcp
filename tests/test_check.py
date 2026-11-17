"""
Tests for the ``check`` tool and the rule engine over real geometry.

These need OpenSCAD (skipped otherwise): the per-part export path, the
mesh-first classification ladder, contact classification, sweeps, the
full-turn certificate, predicates, probes, rays, check files, and the CLI.
"""

import json
import shutil
from pathlib import Path

import pytest

from openscad_mcp import server
from openscad_mcp.checks import Quality, exit_code, summarize
from openscad_mcp.utils.config import CacheConfig, Config, SecurityConfig, set_config

check_fn = server.check.fn
measure_fn = server.measure.fn
export_fn = server.export_model.fn

HAVE_OPENSCAD = shutil.which("openscad") is not None
needs_openscad = pytest.mark.skipif(not HAVE_OPENSCAD, reason="OpenSCAD not installed")

MODEL = """
GAP = 0.5;
LIFT = 0;
module a() { cube(10); }
module b() { translate([10 + GAP, 0, 0]) cube(10); }
module c() { translate([5, 5, 9.8]) cube(4); }
module post() { translate([20, 0, 0]) cylinder(d = 4, h = 12, $fn = 32); }
module bar() { translate([-2, -2, 5]) cube([14, 4, 2]); }
"""
PARTS = [
    {"name": "a", "code": "a();"},
    {"name": "b", "code": "b();"},
    {"name": "c", "code": "c();"},
]


@pytest.fixture
def project(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "asm.scad").write_text(MODEL)
    set_config(
        Config(
            temp_dir=tmp_path / "tmp",
            cache=CacheConfig(enabled=True, directory=tmp_path / "cache"),
            security=SecurityConfig(allowed_paths=[str(proj)]),
        )
    )
    server._reset_openscad_cache()
    server._measure_cache.clear()
    server._mesh_cache.clear()
    return proj


class TestQualityAndSummary:
    def test_quality_bound(self):
        q = Quality(fn=24, curved_radius_mm=16.0)
        d = q.to_dict()
        assert d["fn"] == 24 and d["curved_features"] is True
        assert abs(d["error_bound_mm"] - 0.137) < 0.002
        assert Quality(fn=None).to_dict()["curved_features"] is False

    def test_exit_code_and_summary(self):
        rows = [{"status": "PASS"}, {"status": "FAIL"}, {"status": "UNRESOLVED"}]
        assert exit_code(rows) == 1
        assert exit_code([{"status": "PASS"}, {"status": "UNRESOLVED"}]) == 2
        assert exit_code([{"status": "PASS"}]) == 0
        assert summarize(rows) == {"pass": 1, "fail": 1, "unresolved": 1}


@needs_openscad
class TestCheckReal:
    async def test_interference_ladder(self, project):
        r = await check_fn(scad_file=str(project / "asm.scad"), mode="interference", parts=PARTS, quality=24)
        assert r["success"] is True, r
        by = {tuple(f["subject"]): f for f in r["findings"]}
        assert by[("a", "b")]["state"] == "clear"
        assert by[("a", "b")]["magnitude"]["distance_mm"] == pytest.approx(0.5, abs=1e-6)
        assert by[("a", "c")]["state"] == "interference"
        assert by[("a", "c")]["magnitude"]["penetration_mm"] == pytest.approx(0.2, abs=1e-6)
        assert by[("a", "c")]["status"] == "FAIL"
        assert r["exit_code"] == 1
        assert r["frame"] == "assembly"
        assert r["quality"]["fn"] == 24
        assert r["cache"]["misses"] == 3
        again = await check_fn(scad_file=str(project / "asm.scad"), mode="interference", parts=PARTS, quality=24)
        assert again["cache"]["hits"] == 3

    async def test_flush_contact_is_contact_with_area_and_plane(self, project):
        r = await check_fn(
            scad_file=str(project / "asm.scad"), mode="contact", parts=PARTS[:2],
            variables={"GAP": 0}, kind="static",
        )
        f = r["findings"][0]
        assert f["state"] == "contact"
        assert f["magnitude"]["contact_area_mm2"] == pytest.approx(100.0, rel=1e-6)
        assert f["plane"] == "x = 10.000"
        assert f["status"] == "PASS"

    async def test_sliding_contact_fails_when_touching(self, project):
        r = await check_fn(
            scad_file=str(project / "asm.scad"), mode="contact", parts=PARTS[:2],
            variables={"GAP": 0}, kind="sliding", min_mm=0.2,
        )
        assert r["findings"][0]["status"] == "FAIL"
        r2 = await check_fn(
            scad_file=str(project / "asm.scad"), mode="contact", parts=PARTS[:2],
            variables={"GAP": 0.5}, kind="sliding", min_mm=0.2,
        )
        assert r2["findings"][0]["status"] == "PASS"

    async def test_clearance_with_requirement(self, project):
        r = await check_fn(
            scad_file=str(project / "asm.scad"), mode="clearance", parts=PARTS[:2], min_mm=1.0
        )
        f = r["findings"][0]
        assert f["status"] == "FAIL"
        assert f["magnitude"]["required_mm"] == 1.0
        assert f["magnitude"]["distance_mm"] == pytest.approx(0.5, abs=1e-6)

    async def test_volume_cross_check(self, project):
        r = await check_fn(
            scad_file=str(project / "asm.scad"), mode="interference", parts=[PARTS[0], PARTS[2]],
            volume=True,
        )
        f = r["findings"][0]
        assert f["magnitude"]["intersection_volume_mm3"] == pytest.approx(4 * 4 * 0.2, rel=1e-6)

    async def test_motion_sweep_and_certificate(self, project):
        parts = [{"name": "bar", "code": "bar();"}, {"name": "post", "code": "post();"}]
        r = await check_fn(
            scad_file=str(project / "asm.scad"), mode="motion", parts=parts, moving="bar",
            axis=[0, 0, 1], center=[0, 0, 0], range=[0, 360], steps=36,
        )
        assert r["success"] is True, r
        f = r["findings"][0]
        # bar spans x in [-2, 12] (radius <= 12.2): it never reaches the post at r=18..22
        assert f["all_angles"]["post"]["can_ever_touch"] is False
        assert f["all_angles"]["post"]["min_gap_mm"] > 5
        assert f["status"] == "PASS"

    async def test_rules_from_check_file_and_cli(self, project, capsys):
        check_file = project / "checks.yaml"
        check_file.write_text(
            "version: 1\n"
            "model: asm.scad\n"
            "quality: {fn: 24}\n"
            "parts:\n"
            "  a: {code: 'a();'}\n"
            "  b: {code: 'b();'}\n"
            "  c: {code: 'c();'}\n"
            "checks:\n"
            "  - {rule: no_intersect, pairs: [[a, b]]}\n"
            "  - {rule: interference, pairs: [[a, c]], why: 'c must not sink into a'}\n"
            "  - {rule: clearance, pairs: [[a, b]], min_mm: 0.4}\n"
            "  - {rule: predicate, expr: 'GAP >= 0.4', why: 'gap floor'}\n"
            "  - {rule: probe, point: [5, 5, 5], expect: SOLID}\n"
            "  - {rule: probe, point: [10.25, 5, 5], expect: AIR}\n"
            "  - {rule: ray, origin: [5, 5, 50], direction: [0, 0, -1], first_hit: c}\n"
        )
        r = await check_fn(check_file=str(check_file), mode="rules")
        assert r["success"] is True, r
        statuses = {(f["rule"], tuple(f["subject"])): f["status"] for f in r["findings"]}
        assert statuses[("interference", ("a", "b"))] == "PASS"
        assert statuses[("interference", ("a", "c"))] == "FAIL"
        assert statuses[("clearance", ("a", "b"))] == "PASS"
        assert statuses[("predicate", ("GAP >= 0.4",))] == "PASS"
        assert statuses[("ray", ("c",))] == "PASS"
        probes = [f for f in r["findings"] if f["rule"] == "probe"]
        assert [p["status"] for p in probes] == ["PASS", "PASS"]
        assert r["exit_code"] == 1

        code = server._cli_check([str(check_file), "--allow", str(project)])
        out = capsys.readouterr().out
        assert code == 1
        assert "FAIL" in out and "interference" in out

    async def test_probe_mode_and_polyline(self, project):
        r = await measure_fn(
            scad_file=str(project / "asm.scad"), mode="probe", parts=PARTS[:2],
            points=[[5, 5, 5], [10.25, 5, 5]], rays=[[5, 5, 50, 0, 0, -1]],
            polyline=[[-5, 5, 5], [30, 5, 5]],
        )
        assert r["success"] is True, r
        assert r["points"][0]["state"] == "solid" and r["points"][0]["parts"] == ["a"]
        assert r["points"][1]["state"] == "air"
        assert r["rays"][0]["first_hit"]["part"] == "a"
        assert r["rays"][0]["first_hit"]["distance_mm"] == pytest.approx(40.0, abs=1e-6)
        assert r["polyline"]["clear"] is False and r["polyline"]["blocked_by"] == "a"

    async def test_export_parts_bundle_3mf(self, project):
        r = await export_fn(scad_file=str(project / "asm.scad"), parts=PARTS, output_format="3mf",
                            output_path=str(project / "asm.3mf"))
        assert r["success"] is True, r
        assert r["object_count"] == 3
        from openscad_mcp.threemf import read_3mf_summary

        summary = read_3mf_summary(project / "asm.3mf")
        assert sorted(o["name"] for o in summary["objects"]) == ["a", "b", "c"]

    async def test_ghost_parts_excluded_from_probes_but_not_pairs(self, project):
        parts = [PARTS[0], {"name": "c", "code": "c();", "ghost": True}]
        r = await check_fn(scad_file=str(project / "asm.scad"), mode="interference", parts=parts)
        assert r["findings"][0]["state"] == "interference"
        p = await measure_fn(scad_file=str(project / "asm.scad"), mode="probe", parts=parts, points=[[7, 7, 12]])
        assert p["points"][0]["state"] == "air"  # inside the ghost only

    async def test_bad_inputs(self, project):
        assert (await check_fn(scad_file=str(project / "asm.scad"), mode="wat", parts=PARTS))["success"] is False
        assert (await check_fn(scad_file=str(project / "asm.scad"), mode="motion", parts=PARTS))["success"] is False
        assert (await check_fn(scad_file=str(project / "asm.scad"), mode="rules", parts=PARTS))["success"] is False
        r = await check_fn(scad_file=str(project / "asm.scad"), mode="interference", parts=[{"name": "x", "code": "nope();"}, PARTS[0]])
        assert r["success"] is True  # unknown module warns and yields empty geometry
        assert "x" in r.get("empty_parts", [])
        # An empty part must never produce a silent green: it is UNRESOLVED
        empty_rows = [f for f in r["findings"] if f.get("state") == "empty"]
        assert empty_rows and empty_rows[0]["subject"] == ["x"]
        assert r["exit_code"] == 2


@needs_openscad
class TestReviewFixes:
    async def test_rays_ignore_ghost_parts(self, project):
        parts = [PARTS[0], {"name": "c", "code": "c();", "ghost": True}]
        r = await check_fn(
            scad_file=str(project / "asm.scad"), mode="rules", parts=parts,
            checks=[{"rule": "ray", "origin": [7, 7, 50], "direction": [0, 0, -1], "first_hit": "a"}],
        )
        row = [f for f in r["findings"] if f["rule"] == "ray"][0]
        assert row["status"] == "PASS", row  # the ghost c above a is not hit
        assert row["first_hit"]["part"] == "a"

    async def test_quality_note_when_model_owns_fn(self, project):
        r = await check_fn(scad_file=str(project / "asm.scad"), mode="clearance", parts=PARTS[:2])
        q = r["findings"][0]["quality"]
        assert q["fn"] is None and "model's own" in q["note"]
        r2 = await check_fn(scad_file=str(project / "asm.scad"), mode="clearance", parts=PARTS[:2], quality=32)
        assert r2["findings"][0]["quality"]["fn"] == 32

    async def test_check_openscad_has_success_key(self, project):
        out = await server.check_openscad.fn()
        assert out["success"] is True and out["installed"] is True

    async def test_render_accepts_integer_quality(self, project):
        out = await server.render.fn(scad_file=str(project / "asm.scad"), quality=16)
        assert json.loads([x for x in out if isinstance(x, str)][-1])["success"] is True

    async def test_measure_parts_honours_place(self, project):
        r = await measure_fn(
            scad_file=str(project / "asm.scad"), mode="parts",
            parts=[{"name": "a", "code": "a();", "place": "translate([50, 0, 0])"}],
        )
        assert r["success"] is True, r
        assert r["parts"][0]["bbox_min"] == pytest.approx([50, 0, 0])
        assert r["parts"][0]["placed"] is True
        assert r["frame"] == "assembly"


class TestToolUnwrapping:
    """Internal callers must work whether @mcp.tool returns a FunctionTool
    (fastmcp 2.x, exposes .fn) or the bare function (fastmcp 4.x)."""

    def test_tool_fn_unwraps_function_tool(self):
        from openscad_mcp.server import _tool_fn, check, measure

        for tool in (check, measure):
            fn = _tool_fn(tool)
            assert callable(fn)
            assert not hasattr(fn, "fn")

    def test_tool_fn_passes_bare_function_through(self):
        from openscad_mcp.server import _tool_fn

        async def bare():
            return 1

        assert _tool_fn(bare) is bare

    def test_no_direct_fn_calls_in_server(self):
        import re
        from pathlib import Path

        import openscad_mcp.server as server

        src = Path(server.__file__).read_text()
        assert not re.search(r"\b(check|measure|render|validate)\.fn\(", src)


class TestMassRule:
    """The mass rule over box meshes: no OpenSCAD needed."""

    @staticmethod
    def _engine(parts, meshes):
        from openscad_mcp.assembly import Assembly
        from openscad_mcp.checks import RuleEngine

        return RuleEngine(Assembly(parts=parts), meshes, Quality(fn=32))

    @staticmethod
    def _box(lo, hi, name):
        from tests.test_geom import box

        return box(lo, hi, name)

    def _stack(self):
        from openscad_mcp.assembly import Part

        meshes = {
            "a": self._box((0, 0, 0), (10, 10, 10), "a"),  # 1 cm3 PLA = 1.24 g
            "b": self._box((20, 0, 0), (30, 10, 10), "b"),  # 1 cm3 steel = 7.85 g
            "m": self._box((0, 0, 20), (2, 2, 22), "m"),  # purchased, 5 g
        }
        parts = [
            Part("a", "a();"),
            Part("b", "b();", material="steel"),
            Part("m", "m();", mass_g=5.0, ghost=True),
        ]
        return parts, meshes

    def test_total_mass_pass_and_fail(self):
        parts, meshes = self._stack()
        eng = self._engine(parts, meshes)
        row = eng.run([{"rule": "mass", "part": "a", "max_g": 1.3}])[0]
        assert row["check"] == "total"
        assert row["status"] == "PASS"
        assert row["magnitude"]["mass_g"] == pytest.approx(1.24)
        assert "default" in row["density_source"]["a"]
        row = eng.run([{"rule": "mass", "parts": ["a", "b"], "max_g": 5}])[0]
        assert row["status"] == "FAIL"
        assert row["magnitude"]["mass_g"] == pytest.approx(9.09)
        assert row["subject"] == ["a", "b"]

    def test_whole_assembly_includes_purchased_mass(self):
        parts, meshes = self._stack()
        row = self._engine(parts, meshes).run([{"rule": "mass", "min_g": 14, "max_g": 15}])[0]
        assert row["status"] == "PASS"
        assert row["magnitude"]["mass_g"] == pytest.approx(14.09)
        assert row["density_source"]["m"] == "mass_g=5.0 (given)"

    def test_com_offset_from_axis_and_point(self):
        parts, meshes = self._stack()
        eng = self._engine(parts, meshes)
        row = eng.run(
            [{"rule": "mass", "com_within_mm": 1, "axis": [[15, 5, 0], [0, 0, 1]]}]
        )[0]
        assert row["check"] == "com_offset"
        assert row["status"] == "FAIL"
        assert row["magnitude"]["offset_mm"] == pytest.approx(1.4462, abs=1e-3)
        assert row["at"] == row["magnitude"]["center_of_mass"]
        row = eng.run([{"rule": "mass", "part": "a", "com_within_mm": 0.5, "point": [5, 5, 5]}])[0]
        assert row["status"] == "PASS"
        assert row["magnitude"]["offset_mm"] == pytest.approx(0.0, abs=1e-9)

    def test_inertia_about_axis(self):
        parts, meshes = self._stack()
        row = self._engine(parts, meshes).run(
            [{"rule": "mass", "part": "a", "max_inertia_g_mm2": 80, "axis": [[0, 0, 0], [0, 0, 1]]}]
        )[0]
        # m a^2/6 about the central axis + m d^2 with d^2 = 50: 20.67 + 62 = 82.67 g mm^2
        assert row["check"] == "inertia"
        assert row["magnitude"]["inertia_g_mm2"] == pytest.approx(82.667, abs=1e-3)
        assert row["status"] == "FAIL"

    def test_facts_only_row_and_rule_density_override(self):
        parts, meshes = self._stack()
        eng = self._engine(parts, meshes)
        row = eng.run([{"rule": "mass"}])[0]
        assert row["check"] == "facts"
        assert row["status"] == "PASS"
        assert [p["name"] for p in row["magnitude"]["parts"]] == ["a", "b", "m"]
        row = eng.run([{"rule": "mass", "part": "a", "material": "steel", "max_g": 8}])[0]
        assert row["magnitude"]["mass_g"] == pytest.approx(7.85)
        assert row["density_source"]["a"] == "material=steel (rule)"

    def test_open_mesh_is_unresolved(self):
        from openscad_mcp.assembly import Part
        from openscad_mcp.geom import Mesh

        tris = self._box((0, 0, 0), (10, 10, 10), "a").triangles[:-1]  # drop one face
        row = self._engine([Part("a", "a();")], {"a": Mesh(tris, "a")}).run(
            [{"rule": "mass", "max_g": 100}]
        )[0]
        assert row["status"] == "UNRESOLVED"
        assert "not watertight" in row["note"]

    def test_missing_mesh_without_mass_is_unresolved(self):
        from openscad_mcp.assembly import Part

        row = self._engine([Part("a", "a();")], {}).run([{"rule": "mass", "max_g": 1}])[0]
        assert row["status"] == "UNRESOLVED"
        assert exit_code([row]) == 2

    @pytest.mark.parametrize(
        "bad, message",
        [
            ({"rule": "mass", "parts": ["zz"]}, "unknown part"),
            ({"rule": "mass", "com_within_mm": 1}, "needs axis or point"),
            ({"rule": "mass", "max_inertia_g_mm2": 1}, "needs axis"),
            ({"rule": "mass", "axis": [[0, 0, 0], [0, 0, 0]]}, "must not be zero"),
            ({"rule": "mass", "max_g": -1}, "non-negative"),
            ({"rule": "mass", "point": [1, 2]}, "mass.point must be"),
        ],
    )
    def test_grammar_rejects_bad_rules(self, bad, message):
        from openscad_mcp.assembly import AssemblyError, parse_assembly

        with pytest.raises(AssemblyError, match=message):
            parse_assembly({"parts": [{"name": "a", "code": "a();"}], "checks": [bad]})


@needs_openscad
class TestMassRuleReal:
    async def test_mass_rule_through_check_tool(self, project):
        r = await check_fn(
            scad_file=str(project / "asm.scad"),
            mode="rules",
            parts=[{"name": "a", "code": "a();", "material": "PLA"}, {"name": "post", "code": "post();"}],
            checks=[
                {"rule": "mass", "part": "a", "max_g": 1.3, "why": "the block must stay light"},
                {"rule": "mass", "com_within_mm": 0.01, "point": [5, 5, 5], "part": "a"},
                {"rule": "mass", "min_g": 1.0, "max_g": 1.2},
            ],
            quality=32,
        )
        assert r["success"] is True, r
        rows = [f for f in r["findings"] if f["rule"] == "mass"]
        assert [x["status"] for x in rows] == ["PASS", "PASS", "FAIL"]
        assert rows[0]["magnitude"]["mass_g"] == pytest.approx(1.24, abs=1e-3)
        assert rows[0]["why"] == "the block must stay light"
        assert rows[2]["magnitude"]["mass_g"] > 1.2
        assert r["exit_code"] == 1


class TestExpressionSlots:
    """Expression-valued numbers: grammar and substitution, no OpenSCAD."""

    @staticmethod
    def _asm(checks, motion=None):
        from openscad_mcp.assembly import parse_assembly

        part = {"name": "a", "code": "a();"}
        if motion:
            part["motion"] = motion
        return parse_assembly({"parts": [part], "checks": checks})

    def test_collects_strings_under_numeric_keys_only(self):
        from openscad_mcp.assembly import collect_expression_slots

        asm = self._asm(
            [
                {"rule": "probe", "point": "[X, 0, Z]", "expect": "SOLID", "why": "W"},
                {"rule": "ray", "origin": [1, 2, "TOP * 2"], "direction": [0, 0, -1]},
                {"rule": "clearance", "pairs": [["a", "a"]], "min_mm": "GAP"},
            ],
            motion={"type": "rotate", "axis": [0, 0, 1], "range": [0, "SWING"]},
        )
        slots = collect_expression_slots(asm)
        assert [(s.label, s.expr) for s in slots] == [
            ("checks[0].point", "[X, 0, Z]"),
            ("checks[1].origin[2]", "TOP * 2"),
            ("checks[2].min_mm", "GAP"),
            ("parts.a.motion.range[1]", "SWING"),
        ]

    def test_apply_substitutes_and_records(self):
        from openscad_mcp.assembly import apply_expression_values, collect_expression_slots

        asm = self._asm(
            [{"rule": "probe", "point": "[X, 0, Z]", "expect": "SOLID"}],
            motion={"type": "rotate", "axis": [0, 0, 1], "range": [0, "SWING"]},
        )
        slots = collect_expression_slots(asm)
        apply_expression_values(
            slots,
            [
                {"evaluated": True, "value": [1.5, 0, 9]},
                {"evaluated": True, "value": 90},
            ],
        )
        assert asm.checks[0]["point"] == [1.5, 0, 9]
        assert asm.checks[0]["_expressions"] == {
            "checks[0].point": {"expr": "[X, 0, Z]", "value": [1.5, 0, 9]}
        }
        assert "_unresolved" not in asm.checks[0]
        assert asm.parts[0].motion["range"] == [0, 90]

    def test_non_numeric_marks_rule_unresolved_and_engine_reports_it(self):
        from openscad_mcp.assembly import apply_expression_values, collect_expression_slots
        from openscad_mcp.checks import RuleEngine

        asm = self._asm([{"rule": "probe", "point": "[X, 0, NOPE]", "expect": "SOLID"}])
        slots = collect_expression_slots(asm)
        apply_expression_values(slots, [{"evaluated": True, "value": None}])
        assert "undef" in asm.checks[0]["_unresolved"]
        rows = RuleEngine(asm, {}, Quality(fn=32)).run()
        assert rows[0]["status"] == "UNRESOLVED"
        assert rows[0]["rule"] == "probe"
        assert "[X, 0, NOPE]" in rows[0]["note"]
        assert rows[0]["expressions"]["checks[0].point"]["value"] is None
        assert exit_code(rows) == 2

    def test_motion_expression_failure_raises(self):
        from openscad_mcp.assembly import (
            AssemblyError,
            apply_expression_values,
            collect_expression_slots,
        )

        asm = self._asm([], motion={"type": "rotate", "axis": "AXIS"})
        slots = collect_expression_slots(asm)
        with pytest.raises(AssemblyError, match="parts.a.motion.axis"):
            apply_expression_values(slots, [{"evaluated": False, "value": None}])

    @pytest.mark.parametrize(
        "bad",
        [
            {"rule": "probe", "point": "[1,2,3]; cube(9)", "expect": "AIR"},
            {"rule": "probe", "point": "echo(1)", "expect": "AIR"},
            {"rule": "ray", "origin": [0, 0, ""], "direction": [0, 0, 1]},
            {"rule": "probe", "point": "include <x.scad>", "expect": "AIR"},
        ],
    )
    def test_grammar_rejects_statements_in_expressions(self, bad):
        from openscad_mcp.assembly import AssemblyError

        with pytest.raises(AssemblyError, match="expression"):
            self._asm([bad])

    def test_text_keys_are_never_expressions(self):
        from openscad_mcp.assembly import collect_expression_slots

        asm = self._asm(
            [{"rule": "predicate", "expr": "A > B", "why": "GAP"}, {"rule": "print", "part": "a"}]
        )
        assert collect_expression_slots(asm) == []


@needs_openscad
class TestExpressionsReal:
    async def test_expressions_follow_the_model(self, project):
        r = await check_fn(
            scad_file=str(project / "asm.scad"),
            mode="rules",
            parts=PARTS[:2],
            checks=[
                {"rule": "probe", "point": "[5, 5, 5]", "expect": "SOLID"},
                {"rule": "probe", "point": [10 + 0.25, 5, "GAP * 10"], "expect": "AIR"},
                {"rule": "clearance", "pairs": [["a", "b"]], "min_mm": "GAP"},
                {"rule": "mass", "part": "a", "com_within_mm": "GAP", "point": "[5, 5, 5]"},
                {"rule": "probe", "point": "[5, 5, NOT_A_NAME]", "expect": "SOLID"},
            ],
            quality=24,
        )
        assert r["success"] is True, r
        rows = r["findings"]
        assert [x["status"] for x in rows] == ["PASS", "PASS", "PASS", "PASS", "UNRESOLVED"]
        assert rows[0]["expressions"]["checks[0].point"]["value"] == [5, 5, 5]
        assert rows[1]["expressions"]["checks[1].point[2]"]["value"] == 5
        assert rows[2]["expressions"]["checks[2].min_mm"]["value"] == 0.5
        assert rows[2]["magnitude"]["required_mm"] == 0.5
        assert "NOT_A_NAME" in rows[4]["note"]
        assert r["exit_code"] == 2
        assert "expressions_s" in r["timings"]

    async def test_variables_reach_expressions(self, project):
        r = await check_fn(
            scad_file=str(project / "asm.scad"),
            mode="rules",
            parts=PARTS[:2],
            checks=[{"rule": "clearance", "pairs": [["a", "b"]], "min_mm": "GAP"}],
            variables={"GAP": 2},
            quality=24,
        )
        row = r["findings"][0]
        assert row["magnitude"]["required_mm"] == 2
        assert row["magnitude"]["distance_mm"] == pytest.approx(2.0, abs=1e-6)
        assert row["status"] == "PASS"




class TestCoincidentRule:
    """Feature boundaries with no wall left between them. No OpenSCAD needed.

    Two individually correct features can leave each other no material, and
    each reads as fine on its own -- which is why this is a check and not an
    eyeball.
    """

    @staticmethod
    def _feature(x_mm, d_mm, *, fn=64, polarity="subtractive", axis=(0.0, 0.0, 1.0)):
        """One cylindrical feature on a vertical axis at x = ``x_mm``."""
        from openscad_mcp.csgfeatures import CylFeature
        from openscad_mcp.geom import inscribed_polygon_error

        r = d_mm / 2.0
        undersize = inscribed_polygon_error(r, fn)
        return CylFeature(
            kind="cylinder",
            polarity=polarity,
            axis_point=(x_mm, 0.0, 0.0),
            axis_dir=axis,
            entry=(x_mm, 0.0, 10.0),
            exit=(x_mm, 0.0, 0.0),
            nominal_d_mm=d_mm,
            length_mm=10.0,
            segments=fn,
            fn=fn,
            fa=12.0,
            fs=2.0,
            effective_min_d_mm=d_mm - 2.0 * undersize,
            undersize_mm=undersize,
            path=["difference", "cylinder"],
            center=False,
        )

    @staticmethod
    def _engine(provider):
        from openscad_mcp.assembly import Assembly
        from openscad_mcp.checks import RuleEngine

        return RuleEngine(
            Assembly(parts=[]),
            {},
            Quality(fn=64, curved_radius_mm=3.0),
            coincident_provider=provider,
        )

    def _find(self, parts, tol=0.1):
        from openscad_mcp.csgfeatures import coincident_features

        return coincident_features(parts, tolerance_mm=tol)

    def test_a_healthy_wall_reports_nothing(self):
        """The control. Without it a rule that always fires would pass."""
        out = self._find({"p": [self._feature(0.0, 6.0), self._feature(10.0, 6.0)]})
        assert out["pairs"] == []
        assert out["counts"]["subtractive_features"] == 2

    def test_two_bores_sharing_a_wall_of_zero_are_tangent(self):
        out = self._find({"p": [self._feature(0.0, 6.0), self._feature(6.0, 6.0)]})
        assert len(out["pairs"]) == 1
        pair = out["pairs"][0]
        assert pair["state"] == "tangent"
        assert pair["wall_mm"] == 0.0

    def test_two_bores_that_overlap_are_merged(self):
        """The failing case: the wall the author drew is not there at all."""
        out = self._find({"p": [self._feature(0.0, 6.0), self._feature(5.5, 6.0)]})
        assert len(out["pairs"]) == 1
        assert out["pairs"][0]["state"] == "merged"
        assert out["pairs"][0]["wall_mm"] == -0.5

    def test_the_worst_case_wall_is_the_nominal_radius_not_the_faceted_one(self):
        """`cylinder($fn=n)` puts its vertices ON the nominal circle.

        So a bore reaches full nominal radius at every vertex, and the wall is
        thinnest where two vertices face each other. The faceted radius gives
        the THICKEST wall, not the thinnest, and reporting it alone would
        claim more material than the part has anywhere. The first version of
        this function had exactly that backwards.
        """
        out = self._find({"p": [self._feature(0.0, 6.0, fn=8), self._feature(6.05, 6.0, fn=8)]})
        pair = out["pairs"][0]
        worst, best = pair["wall_bracket_mm"]
        assert worst == pair["wall_mm"], "the reported wall is the worst case"
        assert best > worst, "the faceted wall is thicker, so it is the other end"
        # At $fn=8 the undersize is large enough to be unmistakable.
        assert best - worst > 0.3, f"bracket {pair['wall_bracket_mm']}"

    def test_features_in_one_part_are_compared(self):
        """The defect does not need two parts."""
        out = self._find({"solo": [self._feature(0.0, 6.0), self._feature(5.8, 6.0)]})
        assert len(out["pairs"]) == 1
        assert out["pairs"][0]["same_part"] is True

    def test_additive_features_are_not_compared(self):
        out = self._find(
            {
                "p": [
                    self._feature(0.0, 6.0, polarity="additive"),
                    self._feature(5.5, 6.0, polarity="additive"),
                ]
            }
        )
        assert out["pairs"] == []
        assert out["counts"]["subtractive_features"] == 0

    def test_non_parallel_pairs_are_counted_and_not_measured(self):
        """One wall figure for two skew cylinders would be wrong somewhere."""
        out = self._find(
            {
                "p": [
                    self._feature(0.0, 6.0),
                    self._feature(5.5, 6.0, axis=(1.0, 0.0, 0.0)),
                ]
            }
        )
        assert out["pairs"] == []
        assert out["counts"]["skew_pairs_not_measured"] == 1

    def test_the_rule_fails_on_a_merged_pair(self):
        rows = self._engine(
            lambda tol: self._find({"p": [self._feature(0.0, 6.0), self._feature(5.0, 6.0)]}, tol)
        ).rule_coincident({})
        assert len(rows) == 1
        assert rows[0]["rule"] == "coincident"
        assert rows[0]["status"] == "FAIL"
        assert rows[0]["state"] == "merged"
        assert "quality" in rows[0]
        assert rows[0]["magnitude"]["tolerance_mm"] == 0.1

    def test_the_rule_passes_with_one_row_when_nothing_is_coincident(self):
        rows = self._engine(
            lambda tol: self._find({"p": [self._feature(0.0, 6.0), self._feature(20.0, 6.0)]}, tol)
        ).rule_coincident({})
        assert len(rows) == 1
        assert rows[0]["status"] == "PASS"
        assert "no feature pair" in rows[0]["note"]

    def test_a_wall_inside_the_tessellation_bound_is_unresolved(self):
        """A positive wall thinner than the facet error is not a FAIL.

        It is a figure this method cannot stand behind. A MERGED pair is
        different -- no tessellation error explains a negative wall away.
        """
        from openscad_mcp.geom import inscribed_polygon_error, segments_for

        bound = inscribed_polygon_error(3.0, segments_for(3.0, 64))
        gap = bound / 2.0
        rows = self._engine(
            lambda tol: self._find(
                {"p": [self._feature(0.0, 6.0), self._feature(6.0 + gap, 6.0)]}, tol
            )
        ).rule_coincident({})
        assert len(rows) == 1
        assert rows[0]["status"] == "UNRESOLVED", rows[0]
        assert rows[0]["magnitude"]["wall_mm"] > 0.0

    def test_a_merged_pair_is_not_excused_by_the_tessellation_bound(self):
        rows = self._engine(
            lambda tol: self._find({"p": [self._feature(0.0, 6.0), self._feature(5.99, 6.0)]}, tol)
        ).rule_coincident({})
        assert rows[0]["status"] == "FAIL"
        assert rows[0]["magnitude"]["wall_mm"] < 0.0

    def test_the_rule_is_unresolved_without_a_provider(self):
        rows = self._engine(None).rule_coincident({})
        assert rows[0]["status"] == "UNRESOLVED"
        assert "no feature provider" in rows[0]["note"]

    def test_the_rule_tolerance_comes_from_the_rule(self):
        """Configurable per rule: two rules in one file may reasonably differ."""
        parts = {"p": [self._feature(0.0, 6.0), self._feature(6.5, 6.0)]}
        engine = self._engine(lambda tol: self._find(parts, tol))
        assert engine.rule_coincident({})[0]["status"] == "PASS"
        loose = engine.rule_coincident({"tolerance_mm": 1.0})
        assert loose[0]["status"] in {"FAIL", "UNRESOLVED"}
        assert loose[0]["magnitude"]["tolerance_mm"] == 1.0

    def test_coincident_is_a_known_rule(self):
        from openscad_mcp.assembly import KNOWN_RULES

        assert "coincident" in KNOWN_RULES
