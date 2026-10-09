"""Timeline model -> FIREQ YAML tree.

Mapping rules
-------------
* ``axisGeneratorIP_<g>`` hosts at most one drive track (its drive path) and at most one
  readout tone (its readout path). Drive blocks become ``pulse`` entries with
  ``_readout: false`` and the ``_dac_target`` of the drive DAC; a tone becomes a
  ``_RECTANGULAR`` pulse with ``_readout: true`` and the ``_dac_target`` of its readout DAC.
* Every acquisition window becomes ``axisAcquisitionIP_<a>``, demodulating at the
  frequency of its tone, with ``$tof`` = window delay and ``$rchannel`` = tone channel.
* Drive pulses fire on the drive trigger channel of their track; tones that start at the
  same time share one readout trigger channel. The absolute start times are sorted and
  converted into the *relative* delays of the trigger generator FIFO.
* Non-rectangular envelopes become ``envelope`` children with ``$samples``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import yaml

from .envelopes import SYMMETRY, samples_to_yaml
from .envelopes import envelope as make_envelope
from .expressions import Lin, Param, add, inline_expr, referenced_names, subtract, to_lin
from .model import AcqWindow, DriveBlock, DriveTrack, Experiment, ReadoutTrack, Tone


@dataclass
class TriggerEvent:
    """One entry of the trigger generator FIFO (before converting to relative delays).

    :param time: absolute time in ns (number or expression)
    :type time: Param
    :param ttype: ``drive`` or ``readout``.
    :type ttype: str
    :param mask: ``_channel_mask`` (one bit per trigger channel)
    :type mask: int
    :param labels: pulses/tones fired by the event.
    :type labels: list[str]
    :param preview: time evaluated at the preview point.
    :type preview: float
    :param end: end of the longest pulse or window started by the event.
    :type end: Param
    """

    time: Param
    ttype: str  # "drive" | "readout"
    mask: int
    labels: list[str] = field(default_factory=list)
    preview: float = 0.0
    end: Param = 0.0


def gen_node(g: int) -> str:
    """Return the generator node path.

    :param g: generator index.
    :type g: int
    :return: ``/axisGeneratorIP_<g>``.
    :rtype: str
    """
    return f"/axisGeneratorIP_{g}"


def acq_node(a: int) -> str:
    """Return the acquisition node path.

    :param a: acquisition index.
    :type a: int
    :return: ``/axisAcquisitionIP_<a>``.
    :rtype: str
    """
    return f"/axisAcquisitionIP_{a}"


def _num(v: Param) -> Param:
    """Round floats so that tick-multiples print cleanly.

    :param v: parameter value.
    :type v: Param
    :return: the value, with floats rounded to 6 decimals (ints when integral)
    :rtype: Param
    """
    if isinstance(v, float):
        r = round(v, 6)
        return int(r) if r.is_integer() else r
    return v


class Exporter:
    """Build the YAML tree for an experiment."""

    def __init__(self, exp: Experiment) -> None:
        """Store the experiment.

        :param exp: experiment to export.
        :type exp: Experiment
        """
        self.exp = exp
        self.vars = exp.var_names
        self.pre = exp.preprocess

    # ------------------------------------------------------------------ values
    def norm(self, v: Param | None) -> Param | None:
        """Prepare a value for the YAML (inline macros used inside sweep expressions).

        :param v: parameter value.
        :type v: Param | None
        :return: the value to write in the YAML.
        :rtype: Param | None
        """
        # numbers and %macros are written as they are (the client resolves %macros)
        if not isinstance(v, str) or not v.startswith("#"):
            return _num(v) if isinstance(v, float) else v
        # a #expression that only uses sweep variables is valid for the server as is
        _, names = referenced_names(v)
        if names <= self.vars:
            return v
        # the server does not know the macros: inline their values inside the expression
        lin = to_lin(v, self.pre, self.vars)
        if lin is not None:
            return lin.to_param()
        return "#" + inline_expr(v, self.pre)

    def lin(self, v: Param) -> Lin | None:
        """Linearise a value.

        :param v: parameter value.
        :type v: Param
        :return: the linear expression, or None if not linear.
        :rtype: Lin | None
        """
        return to_lin(v, self.pre, self.vars)

    def value_at(self, v: Param, point: dict[str, float]) -> float:
        """Evaluate a value at a sweep point (NaN if impossible).

        :param v: parameter value.
        :type v: Param
        :param point: sweep point.
        :type point: dict[str, float]
        :return: the value, or NaN.
        :rtype: float
        """
        r = self.exp.ev(v, point)
        return math.nan if r is None else r

    def add(self, a: Param, b: Param) -> Param:
        """Return ``a + b`` simplified.

        :param a: first term.
        :type a: Param
        :param b: second term.
        :type b: Param
        :return: the sum as a number or a ``#`` expression.
        :rtype: Param
        """
        return add(a, b, self.pre, self.vars)

    # ------------------------------------------------------------------ schedule
    def window_start(self, w: AcqWindow) -> Param:
        """Return the absolute start of an acquisition window.

        :param w: acquisition window.
        :type w: AcqWindow
        :return: start of its tone + time of flight.
        :rtype: Param
        """
        _, t = self.exp.tone(w.tone_id)
        return self.add(t.start if t else 0, w.tof)

    def events(self, point: dict[str, float] | None = None) -> list[TriggerEvent]:
        """Return the merged and time-sorted trigger events.

        :param point: sweep point used to sort the events (None = preview point)
        :type point: dict[str, float] | None
        :return: the trigger events; simultaneous events of the same type are merged.
        :rtype: list[TriggerEvent]
        """
        exp = self.exp
        point = exp.preview_values() if point is None else point
        raw: list[TriggerEvent] = []
        # one drive trigger per pulse, on the channel of its track (VZ gates need no trigger)
        for d in exp.drive_tracks:
            bit = 1 << (max(d.trigger_channel, 1) - 1)
            for b in d.blocks:
                if b.kind != "pulse":
                    continue
                raw.append(TriggerEvent(b.start, "drive", bit, [f"{d.dac}:{b.name}"], end=self.add(b.start, b.duration)))
        # one readout trigger per tone; its end includes the acquisition windows (ToF + duration)
        for r, t in exp.all_tones():
            bit = 1 << (exp.readout_channel_of(t) - 1)
            end = self.add(t.start, t.duration)
            for _, w in exp.windows_of(t.id):
                wend = self.add(self.window_start(w), w.duration)
                if all(self.value_at(wend, c) >= self.value_at(end, c) for c in exp.corner_values()):
                    end = wend
            raw.append(TriggerEvent(t.start, "readout", bit, [f"{r.dac}:{t.name}"], end=end))
        for e in raw:
            e.preview = self.value_at(e.time, point)
        # events of the same type at the same time become one FIFO entry with an OR-ed channel mask
        merged: list[TriggerEvent] = []
        for e in raw:
            twin = next((m for m in merged if m.ttype == e.ttype and self._same_time(m.time, e.time)), None)
            if twin is not None:
                twin.mask |= e.mask
                twin.labels += e.labels
                if all(self.value_at(e.end, c) >= self.value_at(twin.end, c) for c in exp.corner_values()):
                    twin.end = e.end
            else:
                merged.append(e)
        # chronological order; at equal times drive triggers come first
        merged.sort(key=lambda e: (math.inf if math.isnan(e.preview) else e.preview, e.ttype != "drive"))
        return merged

    def _same_time(self, a: Param, b: Param) -> bool:
        """Tell whether two times are always equal over the sweep.

        :param a: first time.
        :type a: Param
        :param b: second time.
        :type b: Param
        :return: True if their difference is identically zero.
        :rtype: bool
        """
        if a == b:
            return True
        # equal for every sweep point only if the symbolic difference is the constant 0
        la, lb = self.lin(a), self.lin(b)
        if la is None or lb is None:
            return False
        diff = la - lb
        return diff.is_const and abs(diff.const) < 1e-6

    def auto_duration(self) -> float:
        """Return the automatic shot duration: latest end over the sweep + margin, on the tick grid.

        :return: the shot duration in ns.
        :rtype: float
        """
        # worst case over the sweep corners (timing is linear in the variables)
        ends = [0.0]
        for c in self.exp.corner_values():
            for e in self.events(c):
                v = self.value_at(e.end, c)
                if not math.isnan(v):
                    ends.append(v)
        total = max(ends) + float(self.exp.auto_margin)
        # round up to a whole number of ticks
        tick = self.exp.tick
        return float(_num(math.ceil(total / tick) * tick))

    # ------------------------------------------------------------------ nodes
    def build(self) -> dict:
        """Return the full YAML tree (preprocess / variables / sys_config).

        :return: the tree written as YAML.
        :rtype: dict
        """
        exp = self.exp
        board = exp.board_profile
        gens: dict[int, dict] = {}
        acqs: dict[int, dict] = {}

        def gen(g: int) -> dict:
            """Return the node of a generator, creating it with default values.

            :param g: generator index.
            :type g: int
            :return: the generator node.
            :rtype: dict
            """
            # a generator used only for drive or only for readout keeps the other path disabled (channel 0)
            if g not in gens:
                gens[g] = {"$dfrequency": 0, "$rfrequency": 0, "$rphase": 0.0, "$rchannel": 0, "$dchannel": 0}
            return gens[g]

        # fill the generator nodes (drive path, readout path) and one acquisition node per window
        for d in exp.drive_tracks:
            if d.blocks:
                self._drive(gen(d.generator), d)
        for r, t in exp.all_tones():
            self._tone(gen(t.generator), r, t)
        for _, w in exp.all_windows():
            _, t = exp.tone(w.tone_id)
            if t is not None and t.enabled:
                acqs[w.acquisition] = self._acq(w, t)

        # optionally silence the IPs left configured by previous experiments
        if exp.disable_unused_ips:
            for g in range(board.generators):
                gens.setdefault(g, {"$rchannel": 0, "$dchannel": 0})
            for a in range(board.acquisitions):
                acqs.setdefault(a, {"$rchannel": 0})

        # same layout as the official examples: shots, generators, acquisitions, trigger generator
        sys_config: dict = {"$shots": self.norm(exp.shots)}
        for g in sorted(gens):
            sys_config[gen_node(g)] = self._order_gen(gens[g])
        for a in sorted(acqs):
            sys_config[acq_node(a)] = acqs[a]
        sys_config[exp.trigger_node] = self._trigger()
        return {
            "preprocess": dict(exp.preprocess),
            "variables": {v.name: v.to_yaml() for v in exp.variables},
            "sys_config": sys_config,
        }

    def _drive(self, node: dict, d: DriveTrack) -> None:
        """Write a drive track into its generator node (envelopes, pulses, VZ gates, drive order).

        :param node: generator node.
        :type node: dict
        :param d: drive track.
        :type d: DriveTrack
        """
        # crossbar mask of the DAC of the track (1 if the channel is unknown on this board)
        dac = self.exp.board_profile.dac(d.dac)
        mask = dac.dac_mask if dac else 1
        pulses = [b for b in d.blocks if b.kind == "pulse"]
        # one carrier per generator: the first pulse defines it (the checks flag differing carriers)
        node["$dfrequency"] = self.norm(pulses[0].carrier if pulses else d.blocks[0].carrier)
        node["$dchannel"] = int(d.trigger_channel)
        # $drive_order lists the gates in time order: each drive trigger plays the next one
        point = self.exp.preview_values()
        ordered = sorted(d.blocks, key=lambda b: self.value_at(b.start, point))
        emitted: set[str] = set()
        for b in ordered:
            # blocks sharing a name are repetitions of the same gate (e.g. Ramsey): define once
            if b.name in emitted:
                continue
            emitted.add(b.name)
            if b.kind == "vz":
                node.setdefault("vzgate", []).append({"_name": b.name, "_readout": False, "$vz_rotation": self.norm(b.vz_rotation)})
                continue
            # rectangular pulses use the built-in _RECTANGULAR envelope, the others their own samples
            if b.shape != "rect":
                node.setdefault("envelope", []).append(self._envelope(b))
            entry: dict = {"_name": b.name, "_readout": False, "_envelope": b.envelope_name, "_dac_target": mask}
            # optional flags are written only when set, like in the examples
            if b.switch_iq:
                entry["_switch_iq"] = True
            if b.keep_last:
                entry["_keep_last"] = True
            entry["$duration"] = self.norm(b.duration)
            entry["$gain"] = self.norm(b.gain)
            node.setdefault("pulse", []).append(entry)
        if ordered:
            node["$drive_order"] = [b.name for b in ordered]

    def _envelope(self, b: DriveBlock) -> dict:
        """Build the ``envelope`` node of a non-rectangular pulse.

        :param b: drive pulse.
        :type b: DriveBlock
        :return: the envelope node with ``$samples``.
        :rtype: dict
        """
        samples = make_envelope(b.shape, b.n_samples, b.sigma, b.beta, b.rise, b.expr)
        env: dict = {"_name": b.envelope_name, "_for_interpolation": bool(b.for_interpolation)}
        # symmetric envelopes: the hardware mirrors the first half, so only that half is sent
        if b.for_interpolation and b.symmetric:
            i_even, q_even = SYMMETRY.get(b.shape) or (True, True)
            env.update({"_is_symmetric": True, "_i_even": i_even, "_q_even": q_even})
            samples = samples[: (len(samples) + 1) // 2]
        env["$samples"] = samples_to_yaml(samples, self.exp.sample_format)
        return env

    def _tone(self, node: dict, r: ReadoutTrack, t: Tone) -> None:
        """Write a readout tone into its generator node.

        :param node: generator node.
        :type node: dict
        :param r: readout track.
        :type r: ReadoutTrack
        :param t: readout tone.
        :type t: Tone
        """
        dac = self.exp.board_profile.dac(r.dac)
        # readout path of the generator: frequency, phase and trigger channel of the tone
        node["$rfrequency"] = self.norm(t.frequency)
        node["$rphase"] = self.norm(t.phase)
        node["$rchannel"] = self.exp.readout_channel_of(t)
        # the readout wave is a rectangular pulse sent to the readout DAC
        node.setdefault("pulse", []).append(
            {
                "_name": t.name,
                "_readout": True,
                "_envelope": "_RECTANGULAR",
                "_dac_target": dac.dac_mask if dac else 1,
                "$duration": self.norm(t.duration),
                "$gain": self.norm(t.gain),
            }
        )

    def _acq(self, w: AcqWindow, t: Tone) -> dict:
        """Build the acquisition node of a window.

        :param w: acquisition window.
        :type w: AcqWindow
        :param t: its readout tone.
        :type t: Tone
        :return: the acquisition node.
        :rtype: dict
        """
        # demodulation at the tone frequency; same trigger channel as the tone, delayed by $tof
        return {
            "$duration": self.norm(w.duration),
            "$output_type": w.output_type,
            "$rfrequency": self.norm(t.frequency),
            "$rphase": self.norm(w.demod_phase),
            "$rchannel": self.exp.readout_channel_of(t),
            "$tof": self.norm(w.tof),
        }

    @staticmethod
    def _order_gen(node: dict) -> dict:
        """Order the keys of a generator node like the official examples.

        :param node: generator node.
        :type node: dict
        :return: the ordered node.
        :rtype: dict
        """
        order = ["$dfrequency", "$rfrequency", "$rphase", "$rchannel", "$dchannel", "$lfsr_seed", "envelope", "pulse", "vzgate", "$drive_order"]
        out = {k: node[k] for k in order if k in node}
        # any other key keeps its position after the known ones
        out.update({k: v for k, v in node.items() if k not in out})
        return out

    def _trigger(self) -> dict:
        """Build the trigger generator node (shot duration and relative delays).

        :return: the trigger generator node.
        :rtype: dict
        """
        exp = self.exp
        dur = exp.experiment_duration if exp.experiment_duration is not None else self.auto_duration()
        # each FIFO delay counts from the previous trigger: convert absolute times into differences
        delays = []
        prev: Param = 0
        for i, e in enumerate(self.events()):
            delays.append(
                {
                    "_name": f"{e.ttype}_delay_{i}",
                    "_ttype": e.ttype,
                    "_channel_mask": e.mask,
                    "_index": i + 1,
                    "$delay": self.norm(subtract(e.time, prev, self.pre, self.vars)),
                }
            )
            prev = e.time
        return {"$experiment_duration": self.norm(dur), "delay": delays}


def build_tree(exp: Experiment) -> dict:
    """Return the YAML tree for an experiment.

    :param exp: experiment to export.
    :type exp: Experiment
    :return: the tree (``preprocess``, ``variables``, ``sys_config``)
    :rtype: dict
    """
    return Exporter(exp).build()


# --------------------------------------------------------------------------- dumping
class _Dumper(yaml.SafeDumper):
    """Safe YAML dumper with the representers below (flow lists, quoted macros/expressions)."""


# lists rendered as [a, b, c] on one line (long sample lists would otherwise take one line per value)
class _Flow(list):
    """List rendered in flow style (envelope samples, sweep values)."""


def _repr_flow(dumper: yaml.SafeDumper, data: list) -> yaml.Node:
    """Represent a list in flow style (``[a, b]``).

    :param dumper: YAML dumper.
    :type dumper: yaml.SafeDumper
    :param data: list to represent.
    :type data: list
    :return: the YAML node.
    :rtype: yaml.Node
    """
    return dumper.represent_sequence("tag:yaml.org,2002:seq", data, flow_style=True)


def _repr_str(dumper: yaml.SafeDumper, data: str) -> yaml.Node:
    """Represent a string, quoting ``%macro`` and ``#expression`` values.

    :param dumper: YAML dumper.
    :type dumper: yaml.SafeDumper
    :param data: string to represent.
    :type data: str
    :return: the YAML node.
    :rtype: yaml.Node
    """
    # "#..." would start a YAML comment and "%..." a directive: always quote them
    if data[:1] in ("%", "#"):
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style='"')
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


# register the custom representers on our dumper only (the global yaml dumpers are untouched)
_Dumper.add_representer(_Flow, _repr_flow)
_Dumper.add_representer(str, _repr_str)


def _flowify(node: object, key: str = "") -> object:
    """Mark numeric lists and ``$samples`` for flow-style output.

    :param node: YAML tree (or sub-tree)
    :type node: object
    :param key: key of ``node`` in its parent.
    :type key: str
    :return: the tree with :class:`_Flow` lists.
    :rtype: object
    """
    if isinstance(node, dict):
        return {k: _flowify(v, k) for k, v in node.items()}
    if isinstance(node, list):
        if key in ("$samples", "values") or (node and all(isinstance(x, (int, float)) for x in node)):
            return _Flow([_flowify(x) for x in node])
        return [_flowify(x) for x in node]
    return node


def dump_yaml(tree: dict, header: str = "") -> str:
    """Render the tree as YAML text with the same layout as the official examples.

    :param tree: YAML tree.
    :type tree: dict
    :param header: comment lines written at the top.
    :type header: str
    :return: the YAML text.
    :rtype: str
    """
    # keep insertion order (sort_keys=False) and never wrap long flow lists
    body = yaml.dump(_flowify(tree), Dumper=_Dumper, sort_keys=False, default_flow_style=False, width=100000, allow_unicode=True)
    prefix = "".join(f"# {line}\n" for line in header.splitlines()) if header else ""
    return prefix + body


def export_yaml(exp: Experiment) -> str:
    """Return the YAML text for an experiment.

    :param exp: experiment to export.
    :type exp: Experiment
    :return: the YAML text, with a comment header.
    :rtype: str
    """
    b = exp.board_profile
    header = f"{exp.name}\ngenerated by FIREQ GUI - board {b.title}, time step {b.tick_ns:.6f} ns"
    return dump_yaml(build_tree(exp), header)
