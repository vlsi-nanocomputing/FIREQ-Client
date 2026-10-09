"""Consistency checks between the timeline, the board and the FIREQ firmware limits."""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass

from .boards import nyquist_zone
from .envelopes import envelope
from .expressions import Param, is_identifier, referenced_names
from .model import Experiment
from .yaml_export import Exporter

ERROR, WARNING, INFO = "error", "warning", "info"


@dataclass
class Issue:
    """A validation message; ``item_id`` points at the track/item to select.

    :param level: ``error``, ``warning`` or ``info``.
    :type level: str
    :param message: description of the problem.
    :type message: str
    :param where: track/item description.
    :type where: str
    :param item_id: id of the track/item to select.
    :type item_id: str
    """

    level: str
    message: str
    where: str = ""
    item_id: str = ""

    def __str__(self) -> str:
        """Return a one-line description.

        :return: a one-line description.
        :rtype: str
        """
        return f"[{self.level}] {self.where}: {self.message}" if self.where else f"[{self.level}] {self.message}"


@dataclass
class NyquistHint:
    """Suggested ``set_nyquist`` command for a converter.

    :param kind: ``dac`` or ``adc``.
    :type kind: str
    :param name: channel name.
    :type name: str
    :param tile: converter tile.
    :type tile: int
    :param block: converter block.
    :type block: int
    :param zone: suggested Nyquist zone.
    :type zone: int
    :param freq: highest frequency used on the channel (MHz)
    :type freq: float
    """

    kind: str
    name: str
    tile: int
    block: int
    zone: int
    freq: float


def validate(exp: Experiment) -> list[Issue]:  # noqa: PLR0912, PLR0915
    """Run all checks and return the issues (errors first).

    :param exp: experiment being edited.
    :type exp: Experiment
    :return: the issues, errors first.
    :rtype: list[Issue]
    """
    out: list[Issue] = []
    board = exp.board_profile
    ex = Exporter(exp)
    # values that depend on sweep variables are checked at every corner of the sweep
    corners = exp.corner_values()

    def add(level: str, msg: str, where: str = "", item_id: str = "") -> None:
        """Append an issue.

        :param level: ``error``, ``warning`` or ``info``.
        :type level: str
        :param msg: message.
        :type msg: str
        :param where: track/item description shown to the user.
        :type where: str
        :param item_id: id of the item to select when the issue is clicked.
        :type item_id: str
        """
        out.append(Issue(level, msg, where, item_id))

    def check_refs(v: Param | None, where: str, item_id: str = "") -> None:
        """Report macros and sweep variables used by a value but not defined.

        :param v: parameter value (None is ignored)
        :type v: Param | None
        :param where: track/item description.
        :type where: str
        :param item_id: id of the item.
        :type item_id: str
        """
        if v is None:
            return
        # %macros must exist in preprocess, #expression names must be sweep variables (or numeric macros)
        macros, names = referenced_names(v)
        for m in macros - set(exp.preprocess):
            add(ERROR, f"macro '%{m}' is not defined in preprocess", where, item_id)
        for n in names - exp.var_names - set(exp.preprocess):
            add(ERROR, f"sweep variable '{n}' is not defined", where, item_id)

    def check_range(v: Param, lo: float, hi: float, what: str, where: str, item_id: str) -> None:
        """Report a value that leaves a range at any corner of the sweep.

        :param v: parameter value.
        :type v: Param
        :param lo: lower bound.
        :type lo: float
        :param hi: upper bound.
        :type hi: float
        :param what: name of the quantity.
        :type what: str
        :param where: track/item description.
        :type where: str
        :param item_id: id of the item.
        :type item_id: str
        """
        for c in corners:
            x = exp.ev(v, c)
            if x is not None and not lo <= x <= hi:
                add(ERROR, f"{what} {x:g} outside [{lo:g}, {hi:g}]", where, item_id)
                return

    def check_tick(v: Param, what: str, where: str, item_id: str) -> None:
        """Report a numeric time that is not a multiple of the tick.

        :param v: time in ns.
        :type v: Param
        :param what: name of the quantity.
        :type what: str
        :param where: track/item description.
        :type where: str
        :param item_id: id of the item.
        :type item_id: str
        """
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            # only numeric values can be checked; the FPGA rounds to whole ticks anyway (info only)
            n = v / exp.tick
            if abs(n - round(n)) > 1e-3:
                add(INFO, f"{what} {v:g} ns is not a multiple of the {exp.tick:.4f} ns tick (FPGA uses {round(n)} ticks)", where, item_id)

    # ---- global
    seen = set()
    for v in exp.variables:
        if not is_identifier(v.name):
            add(ERROR, "invalid variable name", f"variable {v.name!r}")
        if v.name in seen:
            add(ERROR, "duplicated variable", v.name)
        seen.add(v.name)
        if v.count == 0:
            add(ERROR, "no sweep points", v.name)
    for k in exp.preprocess:
        if not is_identifier(k):
            add(ERROR, "invalid macro name", f"preprocess {k!r}")
    check_refs(exp.shots, "shots")
    shots = exp.ev(exp.shots)
    if shots is None or shots < 1:
        add(ERROR, "invalid number of shots", "shots")
    check_refs(exp.experiment_duration, "shot duration")

    # ---- resources: each generator has one drive path and one readout path, each acquisition IP one window
    drive_by_gen: dict[int, list[str]] = defaultdict(list)
    tone_by_gen: dict[int, list[str]] = defaultdict(list)
    win_by_acq: dict[int, list[str]] = defaultdict(list)
    for d in exp.drive_tracks:
        drive_by_gen[d.generator].append(d.title)
    for r, t in exp.all_tones():
        tone_by_gen[t.generator].append(f"{r.title}/{t.name}")
    for a, w in exp.all_windows():
        win_by_acq[w.acquisition].append(f"{a.title}")
    for label, table, limit, what in (
        ("axisGeneratorIP", drive_by_gen, board.generators, "drive path"),
        ("axisGeneratorIP", tone_by_gen, board.generators, "readout tone"),
        ("axisAcquisitionIP", win_by_acq, board.acquisitions, "acquisition window"),
    ):
        for idx, users in table.items():
            if idx >= limit:
                add(ERROR, f"{board.title} has only {limit} {label} instances", f"{label}_{idx}")
            if len(users) > 1:
                add(ERROR, f"one {what} per IP, used by: {', '.join(users)}", f"{label}_{idx}")
    n_tones = len(exp.all_tones())
    if n_tones > board.generators:
        add(ERROR, f"{n_tones} readout tones but only {board.generators} generators on {board.title}", "readout")

    # ---- drive tracks
    names_by_gen: dict[int, dict[str, tuple]] = defaultdict(dict)
    for d in exp.drive_tracks:
        c = board.dac(d.dac)
        if c is None:
            add(ERROR, f"DAC {d.dac} does not exist on {board.title}", d.title, d.id)
        elif not c.routed:
            add(ERROR, f"DAC {d.dac} is not connected to the crossbar in the current bitstream", d.title, d.id)
        if not 1 <= d.trigger_channel <= board.trigger_channels:
            add(ERROR, f"trigger channel must be 1..{board.trigger_channels}", d.title, d.id)
        pulses = [b for b in d.blocks if b.kind == "pulse"]
        # $dfrequency belongs to the generator, so all pulses of a track share it
        carriers = {repr(b.carrier) for b in pulses}
        if len(carriers) > 1:
            add(ERROR, "all pulses of a drive track must use the same carrier (one $dfrequency per generator)", d.title, d.id)
        for b in d.blocks:
            where = f"{d.title}/{b.name}"
            if not is_identifier(b.name):
                add(ERROR, "invalid pulse name (letters, digits, _)", where, b.id)
            # a repeated gate (same name) is written once in the YAML: its copies must be identical
            sig = tuple(getattr(b, k) for k in ("kind", "shape", "duration", "gain", "sigma", "beta", "rise", "expr", "n_samples", "vz_rotation"))
            prev = names_by_gen[d.generator].get(b.name)
            if prev is not None and prev != sig:
                add(ERROR, "pulses with the same name must be identical (repeated gate)", where, b.id)
            names_by_gen[d.generator][b.name] = sig
            for attr in ("start", "duration", "gain", "carrier", "vz_rotation"):
                check_refs(getattr(b, attr), where, b.id)
            check_range(b.start, 0, math.inf, "start", where, b.id)
            check_tick(b.start, "start", where, b.id)
            if b.kind == "vz":
                continue
            check_range(b.gain, -1, 1, "gain", where, b.id)
            check_range(b.duration, exp.tick, math.inf, "duration", where, b.id)
            check_tick(b.duration, "duration", where, b.id)
            # a custom expression must evaluate (it is compiled to samples at export)
            if b.shape == "custom":
                try:
                    envelope("custom", 16, expr=b.expr)
                except Exception as e:  # noqa: BLE001
                    add(ERROR, f"envelope expression: {e}", where, b.id)
        # pulses of one track are played one after the other: they must not overlap
        for c_ in corners:
            spans = sorted(
                (exp.ev(b.start, c_) or 0, (exp.ev(b.start, c_) or 0) + (exp.ev(b.duration, c_) or 0), b.name, b.id) for b in pulses
            )
            clash = next(((a, b) for a, b in zip(spans, spans[1:]) if b[0] < a[1] - 1e-9), None)
            if clash:
                add(ERROR, f"'{clash[1][2]}' starts before '{clash[0][2]}' ends", d.title, clash[1][3])
                break

    # ---- readout tracks
    for r in exp.readout_tracks:
        c = board.dac(r.dac)
        if c is None:
            add(ERROR, f"DAC {r.dac} does not exist on {board.title}", r.title, r.id)
        elif not c.routed:
            add(ERROR, f"DAC {r.dac} is not connected to the crossbar in the current bitstream", r.title, r.id)
        tones = [t for t in r.tones if t.enabled]
        if not tones:
            add(WARNING, "no active tone", r.title, r.id)
        if len(tones) > 1 and not board.frequency_mux:
            add(WARNING, f"{board.title}: no frequency multiplexer in the bitstream, tones on the same DAC collide", r.title, r.id)
        # multiplexed tones are summed on the DAC
        gsum = sum(abs(exp.ev(t.gain) or 0) for t in tones)
        if len(tones) > 1 and gsum > 1:
            add(WARNING, f"sum of tone gains {gsum:.2f} > 1: the DAC may saturate", r.title, r.id)
        freqs = [exp.ev(t.frequency) for t in tones]
        known = [round(f, 6) for f in freqs if f is not None]
        if len(set(known)) < len(known):
            add(WARNING, "two tones with the same frequency", r.title, r.id)
        for t in tones:
            where = f"{r.title}/{t.name}"
            if not is_identifier(t.name):
                add(ERROR, "invalid tone name", where, t.id)
            # tones and drive pulses share the pulse namespace of their generator
            if t.name in names_by_gen.get(t.generator, {}):
                add(ERROR, "name already used by a drive pulse of the same generator", where, t.id)
            for attr in ("start", "duration", "gain", "frequency", "phase"):
                check_refs(getattr(t, attr), where, t.id)
            check_range(t.gain, -1, 1, "gain", where, t.id)
            check_range(t.start, 0, math.inf, "start", where, t.id)
            check_range(t.duration, exp.tick, math.inf, "duration", where, t.id)
            check_tick(t.start, "start", where, t.id)
            check_tick(t.duration, "duration", where, t.id)
            if not exp.windows_of(t.id):
                add(WARNING, "no acquisition window: this tone is not measured (use 'Copy to acquisition')", where, t.id)
            # the readout wave of a generator is sent after its drive gates
            for d in exp.drive_tracks:
                if d.generator != t.generator:
                    continue
                for c_ in corners:
                    rs = exp.ev(t.start, c_)
                    ends = [(exp.ev(b.start, c_) or 0) + (exp.ev(b.duration, c_) or 0) for b in d.blocks if b.kind == "pulse"]
                    if rs is not None and ends and rs < max(ends) - 1e-9:
                        add(WARNING, f"starts before the end of the pulses of {d.title} on the same generator", where, t.id)
                        break

    # ---- acquisition tracks
    for a in exp.acq_tracks:
        c = board.adc(a.adc)
        if c is None:
            add(ERROR, f"ADC {a.adc} does not exist on {board.title}", a.title, a.id)
        elif not c.routed:
            add(WARNING, f"ADC {a.adc} is not connected to an acquisition IP in the current bitstream", a.title, a.id)
        for w in a.windows:
            r, t = exp.tone(w.tone_id)
            where = f"{a.title}/{t.name if t else '?'}"
            if t is None:
                add(ERROR, "window not linked to any readout tone", where, w.id)
                continue
            # the acquisition IP must be fed by this ADC in the bitstream
            if c is not None and c.acq_ips and w.acquisition not in c.acq_ips:
                add(WARNING, f"ADC {a.adc} feeds axisAcquisitionIP_{c.acq_ips}, not {w.acquisition}", where, w.id)
            for attr in ("tof", "duration", "demod_phase"):
                check_refs(getattr(w, attr), where, w.id)
            check_range(w.tof, 0, math.inf, "time of flight", where, w.id)
            check_range(w.duration, exp.tick, math.inf, "duration", where, w.id)
            check_tick(w.tof, "time of flight", where, w.id)
            dur = exp.ev(w.duration)
            # raw windows are stored sample by sample: they must fit the acquisition buffer
            if w.output_type == "raw" and dur is not None and board.adc_fs_mhz:
                n = dur * 1e-3 * board.adc_fs_mhz
                if n > board.raw_buffer_samples:
                    add(ERROR, f"raw window of {n:.0f} samples exceeds the {board.raw_buffer_samples}-sample buffer", where, w.id)

    # ---- shared DACs
    users: dict[str, list[str]] = defaultdict(list)
    for d in exp.drive_tracks:
        users[d.dac].append("drive")
    for r in exp.readout_tracks:
        users[r.dac].append("readout")
    for dac, u in users.items():
        if len(u) > 1:
            add(INFO, f"DAC {dac} is used by {len(u)} tracks ({', '.join(u)}): signals are routed by the crossbar", f"DAC {dac}")

    # ---- trigger schedule: the FIFO order is fixed, so it must be the same at every sweep corner
    orders = []
    for c_ in corners:
        evs = ex.events(c_)
        orders.append(tuple((e.ttype, tuple(e.labels)) for e in evs))
        for e in evs:
            if math.isnan(e.preview):
                add(ERROR, f"time cannot be evaluated: {e.time}", ", ".join(e.labels))
    if len(set(orders)) > 1:
        add(ERROR, "the order of the triggers changes during the sweep: the delay FIFO cannot represent it", "sequence")
    if exp.experiment_duration is not None:
        for c_ in corners:
            total = exp.ev(exp.experiment_duration, c_)
            ends = [exp.ev(e.end, c_) or 0 for e in ex.events(c_)]
            if total is not None and ends and max(ends) > total:
                add(ERROR, f"sequence ends at {max(ends):.0f} ns, after the shot duration ({total:.0f} ns)", "shot duration")
                break
    if not exp.all_windows():
        add(WARNING, "no acquisition window: no data will be acquired", "experiment")

    # errors first, then warnings and infos; the same message on the same item is reported once
    order = {ERROR: 0, WARNING: 1, INFO: 2}
    uniq, seen_k = [], set()
    for i in sorted(out, key=lambda i: order[i.level]):
        k = (i.level, i.message, i.where)
        if k not in seen_k:
            seen_k.add(k)
            uniq.append(i)
    return uniq


def nyquist_hints(exp: Experiment) -> list[NyquistHint]:
    """Suggest Nyquist zones for the converters in use.

    :param exp: experiment being edited.
    :type exp: Experiment
    :return: one hint per converter channel in use (highest zone of its frequencies)
    :rtype: list[NyquistHint]
    """
    board = exp.board_profile
    hints: dict[tuple[str, str], NyquistHint] = {}

    def put(kind: str, name: str, freq: float | None, fs: float) -> None:
        """Record the zone of a frequency on a channel, keeping the highest one.

        :param kind: ``dac`` or ``adc``.
        :type kind: str
        :param name: channel name.
        :type name: str
        :param freq: frequency in MHz (None is ignored)
        :type freq: float | None
        :param fs: sampling rate in MHz.
        :type fs: float
        """
        c = board.dac(name) if kind == "dac" else board.adc(name)
        if c is None or freq is None:
            return
        z = nyquist_zone(freq, fs)
        key = (kind, name)
        # a channel used at several frequencies gets the highest zone (and its frequency)
        if key not in hints or z > hints[key].zone:
            hints[key] = NyquistHint(kind, name, c.tile, c.block, z, freq)

    # DACs: drive carriers and readout tones; ADCs: the frequency of the acquired tones
    for d in exp.drive_tracks:
        for b in d.blocks:
            if b.kind == "pulse":
                put("dac", d.dac, exp.ev(b.carrier), board.dac_fs_mhz)
    for r, t in exp.all_tones():
        put("dac", r.dac, exp.ev(t.frequency), board.dac_fs_mhz)
    for a, w in exp.all_windows():
        _, t = exp.tone(w.tone_id)
        if t is not None:
            put("adc", a.adc, exp.ev(t.frequency), board.adc_fs_mhz)
    return list(hints.values())
