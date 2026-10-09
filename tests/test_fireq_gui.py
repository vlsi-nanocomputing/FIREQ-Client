"""Tests for the Qt-free core of FIREQ_GUI (run with ``python -m unittest`` or pytest)."""

from __future__ import annotations

import glob
import itertools
import os
import tempfile
import threading
import unittest
from pathlib import Path

import yaml

from FIREQ_CLIENT.yaml_preprocessor import resolve_placeholders
from FIREQ_GUI.core.expressions import evaluate, subtract, to_lin
from FIREQ_GUI.core.model import Experiment, default_experiment
from FIREQ_GUI.core.validation import ERROR, validate
from FIREQ_GUI.core.yaml_export import export_yaml
from FIREQ_GUI.core.yaml_import import import_yaml_file

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = sorted(glob.glob(str(ROOT / "yaml_experiment_configurations_examples" / "*.yaml")))


def _corners(variables: dict) -> list[dict]:
    axes = []
    for n, v in variables.items():
        if v.get("mode", "lin") == "lin":
            axes.append([(n, v["start"]), (n, v["stop"])])
        elif v["mode"] == "list":
            axes.append([(n, min(v["values"])), (n, max(v["values"]))])
        else:
            axes.append([(n, v["value"])])
    return [dict(c) for c in itertools.product(*axes)] or [{}]


def _ev(x, pt):  # noqa: ANN001, ANN202
    if isinstance(x, str) and x.startswith("#"):
        return evaluate(x, {}, pt)
    return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) else x


def _canon(cfg: dict, pt: dict) -> dict:
    """Semantic view of a resolved config: node parameters and absolute trigger times."""
    out = {"shots": cfg["sys_config"]["$shots"]}
    for k, v in cfg["sys_config"].items():
        if k.startswith("/axisGeneratorIP"):
            pulses = sorted(
                (p["_name"], p.get("_readout", False), p["_envelope"], p.get("_dac_target", 1), _ev(p["$duration"], pt), _ev(p["$gain"], pt))
                for p in v.get("pulse", [])
            )
            has_drive = any(not p.get("_readout", False) for p in v.get("pulse", []))
            out[k] = (
                _ev(v["$dfrequency"], pt) if has_drive else None,
                _ev(v["$rfrequency"], pt) if v.get("$rchannel") else None,
                v.get("$rchannel"),
                v.get("$dchannel") if has_drive else None,
                tuple(pulses),
                tuple(v.get("$drive_order", [])),
            )
        elif k.startswith("/axisAcquisitionIP"):
            out[k] = {kk: _ev(vv, pt) for kk, vv in v.items()}
        elif k.startswith("/axisTrigger"):
            t, ab = 0.0, []
            for d in sorted(v["delay"], key=lambda d: d["_index"]):
                t += _ev(d["$delay"], pt)
                ab.append((d["_ttype"], d.get("_channel_mask"), round(t, 6)))
            out["trig"] = (_ev(v["$experiment_duration"], pt), sorted(ab))
    return out


class ExpressionTests(unittest.TestCase):
    def test_linear_delays(self) -> None:
        pre, names = {"d": 200}, {"tau"}
        self.assertEqual(subtract("#2*tau + 500", "#tau + 300", pre, names), "#tau + 200")
        self.assertEqual(subtract("%d", 50, pre, names), 150)
        self.assertEqual(to_lin("#tau + d", pre, names).to_param(), "#tau + 200")

    def test_evaluate(self) -> None:
        self.assertEqual(evaluate("#tau*2 + 1", {}, {"tau": 3}), 7)
        self.assertEqual(evaluate("%x", {"x": "%y", "y": 4}, {}), 4)
        self.assertIsNone(evaluate("#nope", {}, {}))


class ExportImportTests(unittest.TestCase):
    def test_examples_round_trip(self) -> None:
        """Every official example survives import -> export with the same semantics."""
        self.assertTrue(EXAMPLES)
        for f in EXAMPLES:
            with self.subTest(example=os.path.basename(f)):
                orig = yaml.safe_load(Path(f).read_text())
                orig.setdefault("variables", {})
                exp, _ = import_yaml_file(f)
                new = yaml.safe_load(export_yaml(exp))
                a, b = resolve_placeholders(orig), resolve_placeholders(new)
                for pt in _corners(orig["variables"]):
                    self.assertEqual(_canon(a, pt), _canon(b, pt))

    def test_project_json_round_trip(self) -> None:
        exp = default_experiment("ZCU216")
        again = Experiment.from_json(exp.to_json())
        self.assertEqual(export_yaml(exp), export_yaml(again))
        self.assertEqual(exp.to_json(), again.to_json())

    def test_default_is_valid(self) -> None:
        for board in ("RFSoC4x2", "ZCU216"):
            errors = [i for i in validate(default_experiment(board)) if i.level == ERROR]
            self.assertEqual(errors, [], board)

    def test_multiplexed_readout_mapping(self) -> None:
        exp = default_experiment("ZCU216")
        ro = exp.readout_tracks[0]
        self.assertEqual(len(ro.tones), 2)
        sysc = yaml.safe_load(export_yaml(exp))["sys_config"]
        self.assertIn("/axisAcquisitionIP_1", sysc)
        pulses = [p for g in ("/axisGeneratorIP_0", "/axisGeneratorIP_1") for p in sysc[g]["pulse"] if p["_readout"]]
        self.assertEqual(len(pulses), 2)
        self.assertEqual({p["_dac_target"] for p in pulses}, {exp.board_profile.dac(ro.dac).dac_mask})

    def test_tick_grid_and_tof(self) -> None:
        exp = default_experiment("ZCU216")
        tick = exp.tick
        self.assertAlmostEqual(tick, 1000 / 583.68)
        t = exp.readout_tracks[0].tones[0]
        for b in exp.drive_tracks[0].blocks:
            self.assertAlmostEqual(b.start / tick, round(b.start / tick), places=6)
        w = exp.windows_of(t.id)[0][1]
        sysc = yaml.safe_load(export_yaml(exp))["sys_config"]
        self.assertEqual(sysc[f"/axisAcquisitionIP_{w.acquisition}"]["$tof"], w.tof)
        # staggered tones get different readout trigger channels
        exp.readout_tracks[0].tones[1].start = exp.snap_time(float(t.start) + 300)
        sysc = yaml.safe_load(export_yaml(exp))["sys_config"]
        ch = {sysc[g]["$rchannel"] for g in ("/axisGeneratorIP_0", "/axisGeneratorIP_1")}
        self.assertEqual(ch, {1, 2})
        masks = [d["_channel_mask"] for d in sysc["/axisTriggerGenerator_0"]["delay"] if d["_ttype"] == "readout"]
        self.assertEqual(masks, [1, 2])

    def test_resource_conflicts_detected(self) -> None:
        exp = default_experiment("RFSoC4x2")
        exp.add_drive_track()
        msgs = [i.message for i in validate(exp) if i.level == ERROR]
        self.assertTrue(any("drive path" in m for m in msgs), msgs)


class SessionTests(unittest.TestCase):
    def test_run_against_mock_server(self) -> None:
        from FIREQ_GUI.core.session import FireqSession
        from FIREQ_GUI.tools.mock_server import MockServer

        srv = MockServer(port=0, delay=0)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        points = []

        class L:
            def __getattr__(self, n):  # noqa: ANN001, ANN204
                return lambda *a, **k: None

            def on_point(self, i, d, o) -> None:  # noqa: ANN001
                points.append(i)

        with tempfile.TemporaryDirectory() as tmp:
            exp = default_experiment("ZCU216")
            exp.variables[0].num = 4
            exp.shots = 200
            yml = Path(tmp) / "rabi.yaml"
            yml.write_text(export_yaml(exp))
            s = FireqSession("127.0.0.1", srv.port, listener=L(), output_root=str(Path(tmp) / "out"), timeout=5)
            s.open()
            out = s.run_yaml_file(str(yml))
            s.close()
            self.assertEqual(points, [(0,), (1,), (2,), (3,)])
            self.assertTrue((Path(out) / "data_3.pkl").exists())
            self.assertTrue((Path(out) / "end_message.json").exists())


if __name__ == "__main__":
    unittest.main()
