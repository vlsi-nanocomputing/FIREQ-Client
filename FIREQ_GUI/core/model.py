"""Experiment model edited by the GUI: a multi-track timeline of one shot.

The experiment is organised like an audio/video editor. Every track is one physical
converter channel, named as in the AMD RF Data Converter (``228_0`` = tile 228, block 0):

* :class:`DriveTrack` - a DAC used for qubit drive. Its :class:`DriveBlock` items are
  pulses placed on the time axis (carrier, envelope, duration, gain).
* :class:`ReadoutTrack` - a DAC used for readout. It contains one *lane* per
  frequency-multiplexed :class:`Tone`; each tone is a block with its own frequency,
  start and duration and is played by one generator IP (readout path).
* :class:`AcquisitionTrack` - an ADC. Its :class:`AcqWindow` items are copies of readout
  tones shifted by the time of flight; each window is demodulated by one acquisition IP.

All times are in ns and, when numeric, snapped to the trigger generator tick
(1 / 583.68 MHz ~ 1.713 ns on the current bitstreams). Any numeric parameter can also be
a ``%macro`` (preprocess) or a ``#expression`` of sweep variables.

:mod:`FIREQ_GUI.core.yaml_export` turns the model into the FIREQ YAML tree.
"""

from __future__ import annotations

import copy
import itertools
import json
import uuid
from dataclasses import asdict, dataclass, field, fields
from typing import Any

import numpy as np

from .boards import BOARDS, Board, register_board
from .expressions import Param, evaluate

PALETTE = ["#2f6fdf", "#e07b2a", "#2a9d6f", "#b8418f", "#7a5cd6", "#c9a227", "#3aa3c2", "#d0453b"]
OUTPUT_TYPES = ("accumulated", "decimated", "raw")


def new_id() -> str:
    """Return a short unique id used to link items (tone <-> acquisition window).

    :return: an 8-character hexadecimal id.
    :rtype: str
    """
    return uuid.uuid4().hex[:8]


# --------------------------------------------------------------------------- items
@dataclass
class DriveBlock:
    """A drive pulse (or a virtual-Z gate) on a drive track.

    :param name: gate name in the YAML (blocks with the same name are repetitions of one gate)
    :type name: str
    :param kind: ``pulse`` or ``vz`` (virtual Z gate)
    :type kind: str
    :param start: start time in ns from the beginning of the shot.
    :type start: Param
    :param duration: duration in ns.
    :type duration: Param
    :param carrier: drive frequency in MHz (``$dfrequency``; one per generator)
    :type carrier: Param
    :param shape: envelope shape, key of :data:`~FIREQ_GUI.core.envelopes.SHAPES`.
    :type shape: str
    :param gain: amplitude, -1..1.
    :type gain: Param
    :param sigma: Gaussian width as a fraction of the duration.
    :type sigma: float
    :param beta: DRAG coefficient.
    :type beta: float
    :param rise: rise/fall as a fraction of the duration (flat-top)
    :type rise: float
    :param expr: numpy expression of t in [0, 1] (custom shape)
    :type expr: str
    :param n_samples: number of envelope samples written to memory.
    :type n_samples: int
    :param for_interpolation: ``_for_interpolation`` of the envelope.
    :type for_interpolation: bool
    :param symmetric: ``_is_symmetric`` of the envelope (only half the samples are sent)
    :type symmetric: bool
    :param switch_iq: ``_switch_iq`` of the pulse.
    :type switch_iq: bool
    :param keep_last: ``_keep_last`` of the pulse.
    :type keep_last: bool
    :param vz_rotation: rotation of a VZ gate as a fraction of 2*pi.
    :type vz_rotation: Param
    :param id: unique id used by the GUI.
    :type id: str
    """

    name: str = "x180"
    kind: str = "pulse"  # "pulse" | "vz"
    start: Param = 100.0  # ns from the start of the shot
    duration: Param = 200.0  # ns
    carrier: Param = 5000.0  # MHz; one carrier per generator ($dfrequency)
    shape: str = "gaussian"
    gain: Param = 0.1
    sigma: float = 0.2
    beta: float = 0.0
    rise: float = 0.2
    expr: str = ""
    n_samples: int = 64
    for_interpolation: bool = True
    symmetric: bool = False
    switch_iq: bool = False
    keep_last: bool = False
    vz_rotation: Param = 0.25  # fraction of 2*pi (VZ gates only)
    id: str = field(default_factory=new_id)

    @property
    def envelope_name(self) -> str:
        """Return the envelope node name used in the YAML.

        :return: ``_RECTANGULAR`` for rectangular pulses, ``env_<name>`` otherwise.
        :rtype: str
        """
        return "_RECTANGULAR" if self.shape == "rect" else f"env_{self.name}"


@dataclass
class Tone:
    """One frequency of a readout track (one lane, one readout block).

    :param name: pulse name in the YAML.
    :type name: str
    :param frequency: readout frequency in MHz (``$rfrequency``, also used for demodulation)
    :type frequency: Param
    :param start: start time in ns.
    :type start: Param
    :param duration: duration in ns.
    :type duration: Param
    :param gain: amplitude, -1..1.
    :type gain: Param
    :param phase: generator readout phase in rad.
    :type phase: Param
    :param generator: generator IP whose readout path plays the tone.
    :type generator: int
    :param enabled: False to keep the tone without exporting it.
    :type enabled: bool
    :param id: unique id, referenced by acquisition windows.
    :type id: str
    """

    name: str = "ro_q0"
    frequency: Param = 7500.0  # MHz ($rfrequency of the generator)
    start: Param = 400.0  # ns
    duration: Param = 1000.0  # ns
    gain: Param = 0.2
    phase: Param = 0.0  # generator readout phase (rad)
    generator: int = 0
    enabled: bool = True
    id: str = field(default_factory=new_id)


@dataclass
class AcqWindow:
    """An acquisition window: a copy of a readout tone delayed by the time of flight.

    :param tone_id: id of the acquired tone.
    :type tone_id: str
    :param tof: delay of the window after the start of the tone in ns (``$tof``)
    :type tof: Param
    :param duration: window length in ns.
    :type duration: Param
    :param output_type: ``accumulated``, ``decimated`` or ``raw``.
    :type output_type: str
    :param demod_phase: demodulation phase in rad.
    :type demod_phase: Param
    :param acquisition: acquisition IP index.
    :type acquisition: int
    :param id: unique id.
    :type id: str
    """

    tone_id: str = ""
    tof: Param = 200.0  # ns after the start of the linked tone
    duration: Param = 1000.0  # ns
    output_type: str = "accumulated"
    demod_phase: Param = 0.0  # rad
    acquisition: int = 0
    id: str = field(default_factory=new_id)


# --------------------------------------------------------------------------- tracks
@dataclass
class DriveTrack:
    """A DAC channel used for qubit drive (one generator drive path).

    :param dac: DAC channel name (``228_0``)
    :type dac: str
    :param generator: generator IP whose drive path plays the track.
    :type generator: int
    :param trigger_channel: drive trigger channel (``$dchannel``, 1-based)
    :type trigger_channel: int
    :param color: colour on the timeline.
    :type color: str
    :param blocks: pulses and VZ gates.
    :type blocks: list[DriveBlock]
    :param id: unique id.
    :type id: str
    """

    dac: str = "228_0"
    generator: int = 0
    trigger_channel: int = 1
    color: str = PALETTE[0]
    blocks: list[DriveBlock] = field(default_factory=list)
    id: str = field(default_factory=new_id)

    @property
    def title(self) -> str:
        """Return the track label.

        :return: ``DAC <tile>_<block>``.
        :rtype: str
        """
        return f"DAC {self.dac}"


@dataclass
class ReadoutTrack:
    """A DAC channel used for readout; its tones are summed by the frequency multiplexer.

    :param dac: DAC channel name.
    :type dac: str
    :param color: colour on the timeline.
    :type color: str
    :param tones: multiplexed tones (one lane each)
    :type tones: list[Tone]
    :param id: unique id.
    :type id: str
    """

    dac: str = "229_0"
    color: str = PALETTE[2]
    tones: list[Tone] = field(default_factory=list)
    id: str = field(default_factory=new_id)

    @property
    def title(self) -> str:
        """Return the track label.

        :return: ``DAC <tile>_<block>``.
        :rtype: str
        """
        return f"DAC {self.dac}"


@dataclass
class AcquisitionTrack:
    """An ADC channel; each window is demodulated by one acquisition IP.

    :param adc: ADC channel name (``224_0``)
    :type adc: str
    :param color: colour on the timeline.
    :type color: str
    :param windows: acquisition windows.
    :type windows: list[AcqWindow]
    :param id: unique id.
    :type id: str
    """

    adc: str = "224_0"
    color: str = "#555555"
    windows: list[AcqWindow] = field(default_factory=list)
    id: str = field(default_factory=new_id)

    @property
    def title(self) -> str:
        """Return the track label.

        :return: ``ADC <tile>_<block>``.
        :rtype: str
        """
        return f"ADC {self.adc}"


@dataclass
class Variable:
    """A sweep variable (``variables`` section).

    :param name: variable name, used as ``#name`` in expressions.
    :type name: str
    :param mode: ``lin``, ``list`` or ``const``.
    :type mode: str
    :param start: first value (lin)
    :type start: float
    :param stop: last value (lin)
    :type stop: float
    :param num: number of points (lin)
    :type num: int
    :param values: values (list)
    :type values: list[float]
    :param value: value (const)
    :type value: float
    """

    name: str = "sweep"
    mode: str = "lin"  # lin | list | const
    start: float = 0.0
    stop: float = 1.0
    num: int = 11
    values: list[float] = field(default_factory=list)
    value: float = 0.0

    def to_yaml(self) -> dict:
        """Return the YAML description.

        :return: the entry of the ``variables`` section.
        :rtype: dict
        """
        if self.mode == "list":
            return {"values": list(self.values), "mode": "list"}
        if self.mode == "const":
            return {"value": self.value, "mode": "const"}
        return {"start": self.start, "stop": self.stop, "num": int(self.num), "mode": "lin"}

    def points(self) -> np.ndarray:
        """Return all the values taken by the variable.

        :return: the sweep values.
        :rtype: np.ndarray
        """
        if self.mode == "list":
            return np.asarray(self.values, dtype=float)
        if self.mode == "const":
            return np.asarray([self.value], dtype=float)
        return np.linspace(self.start, self.stop, max(int(self.num), 1))

    @property
    def count(self) -> int:
        """Return the number of sweep points.

        :return: the number of sweep points.
        :rtype: int
        """
        return len(self.points())


# --------------------------------------------------------------------------- experiment
@dataclass
class Experiment:
    """The whole experiment: tracks, sweep variables and global settings.

    :param name: name of the YAML file and of the output folder.
    :type name: str
    :param board: key of the board profile.
    :type board: str
    :param hardware: hardware description loaded from a file or received from the server (None = built-in board)
    :type hardware: dict | None
    :param shots: shots per sweep point.
    :type shots: Param
    :param experiment_duration: shot duration in ns (None = automatic)
    :type experiment_duration: Param | None
    :param auto_margin: margin in ns added after the last event in automatic mode.
    :type auto_margin: float
    :param snap: snap times to the trigger generator tick.
    :type snap: bool
    :param preprocess: preprocess macros.
    :type preprocess: dict[str, Param]
    :param variables: sweep variables.
    :type variables: list[Variable]
    :param drive_tracks: drive tracks.
    :type drive_tracks: list[DriveTrack]
    :param readout_tracks: readout tracks.
    :type readout_tracks: list[ReadoutTrack]
    :param acq_tracks: acquisition tracks.
    :type acq_tracks: list[AcquisitionTrack]
    :param sample_format: serialisation of ``$samples`` (see :func:`~FIREQ_GUI.core.envelopes.samples_to_yaml`)
    :type sample_format: str
    :param disable_unused_ips: write ``$dchannel``/``$rchannel`` = 0 for unused IPs.
    :type disable_unused_ips: bool
    :param trigger_node: path of the trigger generator node.
    :type trigger_node: str
    :param preview_fraction: sweep position (0..1) used for previews.
    :type preview_fraction: float
    """

    name: str = "experiment"
    board: str = "ZCU216"
    # hardware description loaded from a file or received from the server (None = built-in board);
    # kept in the project so that it reopens with the same channels and IP counts
    hardware: dict | None = None
    shots: Param = 1000
    experiment_duration: Param | None = None  # None -> automatic
    auto_margin: float = 2000.0  # ns added after the last event when automatic
    snap: bool = True
    preprocess: dict[str, Param] = field(default_factory=dict)
    variables: list[Variable] = field(default_factory=list)
    drive_tracks: list[DriveTrack] = field(default_factory=list)
    readout_tracks: list[ReadoutTrack] = field(default_factory=list)
    acq_tracks: list[AcquisitionTrack] = field(default_factory=list)
    sample_format: str = "iq_pairs"
    disable_unused_ips: bool = False
    trigger_node: str = "/axisTriggerGenerator_0"
    preview_fraction: float = 0.0  # sweep position used for the preview (0..1)

    # ------------------------------------------------------------------ board / time base
    @property
    def board_profile(self) -> Board:
        """Return the board profile (falls back to the first known board).

        :return: the profile of :attr:`board`.
        :rtype: Board
        """
        # an unknown key (e.g. a project made with a board file that is not loaded) falls back
        return BOARDS.get(self.board) or next(iter(BOARDS.values()))

    @property
    def tick(self) -> float:
        """Return the timeline resolution in ns (one trigger generator clock cycle).

        :return: the tick in ns.
        :rtype: float
        """
        return self.board_profile.tick_ns

    def apply_hardware(self, key: str, description: dict) -> None:
        """Switch to a hardware description (from a file or from the server).

        The board is registered under ``key``; tracks keep their channels when they still
        exist, otherwise they are moved to free channels, and IP indices are clamped.

        :param key: key under which the board is registered.
        :type key: str
        :param description: hardware description (fields of a boards.json entry, optionally with ``base``)
        :type description: dict
        """
        board = register_board(key, description)
        self.board, self.hardware = key, dict(description)
        # keep each track on its channel when the new board has it, otherwise move it to a free one
        for tr in [*self.drive_tracks, *self.readout_tracks]:
            if board.dac(tr.dac) is None:
                tr.dac = self.free_dac(exclude=tr)
        for tr in self.acq_tracks:
            if board.adc(tr.adc) is None:
                tr.adc = self.free_adc(exclude=tr)
        # IP indices beyond the new counts are clamped; conflicts are reported by the checks
        for d in self.drive_tracks:
            d.generator = min(d.generator, board.generators - 1)
        for _, t in self.all_tones():
            t.generator = min(t.generator, board.generators - 1)
        for _, w in self.all_windows():
            w.acquisition = min(w.acquisition, board.acquisitions - 1)

    def snap_time(self, t: float) -> float:
        """Snap a time to the closest tick (if snapping is enabled).

        :param t: time in ns.
        :type t: float
        :return: the snapped time, rounded to 6 decimals.
        :rtype: float
        """
        if not self.snap:
            return float(t)
        # nearest whole number of ticks, rounded to 6 decimals to avoid float noise in the YAML
        return round(round(t / self.tick) * self.tick, 6)

    # ------------------------------------------------------------------ sweep helpers
    @property
    def var_names(self) -> set[str]:
        """Return the names of the sweep variables.

        :return: the names of the sweep variables.
        :rtype: set[str]
        """
        return {v.name for v in self.variables}

    def preview_values(self, fraction: float | None = None) -> dict[str, float]:
        """Return a value for each sweep variable at a fractional sweep position.

        :param fraction: position in the sweep, 0..1 (None = :attr:`preview_fraction`)
        :type fraction: float | None
        :return: the value of each variable at that position.
        :rtype: dict[str, float]
        """
        f = self.preview_fraction if fraction is None else fraction
        out = {}
        for v in self.variables:
            pts = v.points()
            # pick the sweep point closest to the requested fraction
            out[v.name] = float(pts[int(round(f * (len(pts) - 1)))]) if len(pts) else 0.0
        return out

    def corner_values(self) -> list[dict[str, float]]:
        """Return the sweep corners (min/max of every variable) for range checks.

        :return: one dict per corner of the sweep (``[{}]`` without variables)
        :rtype: list[dict[str, float]]
        """
        # timing is linear in the variables, so checking the extremes of every variable is enough
        axes = []
        for v in self.variables:
            pts = v.points()
            axes.append([(v.name, float(pts.min())), (v.name, float(pts.max()))] if len(pts) else [(v.name, 0.0)])
        return [dict(c) for c in itertools.product(*axes)] if axes else [{}]

    def ev(self, value: Param | None, variables: dict[str, float] | None = None) -> float | None:
        """Evaluate a value at the preview point (or at the given sweep point).

        :param value: number, ``%macro`` or ``#expression`` (None returns None)
        :type value: Param | None
        :param variables: sweep point to use (None = preview point)
        :type variables: dict[str, float] | None
        :return: the numeric value, or None if it cannot be evaluated.
        :rtype: float | None
        """
        if value is None:
            return None
        return evaluate(value, self.preprocess, self.preview_values() if variables is None else variables)

    def total_points(self) -> int:
        """Return the number of sweep points.

        :return: the product of the number of points of every variable.
        :rtype: int
        """
        n = 1
        for v in self.variables:
            n *= max(v.count, 1)
        return n

    # ------------------------------------------------------------------ lookups
    def all_tones(self) -> list[tuple[ReadoutTrack, Tone]]:
        """Return all enabled tones with their track.

        :return: (readout track, tone) for every enabled tone.
        :rtype: list[tuple[ReadoutTrack, Tone]]
        """
        return [(r, t) for r in self.readout_tracks for t in r.tones if t.enabled]

    def tone(self, tone_id: str) -> tuple[ReadoutTrack | None, Tone | None]:
        """Find a tone by id.

        :param tone_id: id of a readout tone.
        :type tone_id: str
        :return: the readout track and the tone, or (None, None)
        :rtype: tuple[ReadoutTrack | None, Tone | None]
        """
        for r in self.readout_tracks:
            for t in r.tones:
                if t.id == tone_id:
                    return r, t
        return None, None

    def windows_of(self, tone_id: str) -> list[tuple[AcquisitionTrack, AcqWindow]]:
        """Return the acquisition windows linked to a tone.

        :param tone_id: id of a readout tone.
        :type tone_id: str
        :return: (acquisition track, window) for every window of the tone.
        :rtype: list[tuple[AcquisitionTrack, AcqWindow]]
        """
        return [(a, w) for a in self.acq_tracks for w in a.windows if w.tone_id == tone_id]

    def all_windows(self) -> list[tuple[AcquisitionTrack, AcqWindow]]:
        """Return every acquisition window with its track.

        :return: (acquisition track, window) pairs.
        :rtype: list[tuple[AcquisitionTrack, AcqWindow]]
        """
        return [(a, w) for a in self.acq_tracks for w in a.windows]

    def tracks(self) -> list[object]:
        """Return all tracks in display order.

        :return: drive tracks, then readout tracks, then acquisition tracks.
        :rtype: list[object]
        """
        return [*self.drive_tracks, *self.readout_tracks, *self.acq_tracks]

    def find(self, item_id: str) -> tuple[object | None, object | None]:
        """Return (track, item) for any track or item id.

        :param item_id: id of a track or of a track item.
        :type item_id: str
        :return: (track, None) for a track id, (track, item) for an item id, (None, None) if not found.
        :rtype: tuple[object | None, object | None]
        """
        for tr in self.tracks():
            if tr.id == item_id:
                return tr, None
            # each track kind has exactly one of these lists
            for it in getattr(tr, "blocks", []) + getattr(tr, "tones", []) + getattr(tr, "windows", []):
                if it.id == item_id:
                    return tr, it
        return None, None

    def item_names(self) -> set[str]:
        """Return the names of all pulses and tones (they share the pulse namespace).

        :return: the names in use.
        :rtype: set[str]
        """
        names = {b.name for d in self.drive_tracks for b in d.blocks}
        return names | {t.name for r in self.readout_tracks for t in r.tones}

    # ------------------------------------------------------------------ resource allocation
    def used_dacs(self, exclude: object = None) -> set[str]:
        """Return the DAC channels already used by tracks.

        :param exclude: track to ignore (e.g. the track being re-mapped)
        :type exclude: object
        :return: the DAC channel names in use.
        :rtype: set[str]
        """
        return {t.dac for t in [*self.drive_tracks, *self.readout_tracks] if t is not exclude}

    def free_dac(self, exclude: object = None) -> str:
        """Return a routed DAC channel not used by any track (or the first routed one).

        :param exclude: track to ignore.
        :type exclude: object
        :return: a DAC channel name.
        :rtype: str
        """
        board = self.board_profile
        used = self.used_dacs(exclude)
        # prefer channels reachable with the current bitstream
        routed = [c.name for c in board.dacs if c.routed] or [c.name for c in board.dacs]
        return next((n for n in routed if n not in used), routed[0] if routed else "")

    def free_adc(self, exclude: object = None) -> str:
        """Return a routed ADC channel not used by any acquisition track.

        :param exclude: track to ignore.
        :type exclude: object
        :return: an ADC channel name.
        :rtype: str
        """
        board = self.board_profile
        used = {a.adc for a in self.acq_tracks if a is not exclude}
        routed = [c.name for c in board.adcs if c.routed] or [c.name for c in board.adcs]
        return next((n for n in routed if n not in used), routed[0] if routed else "")

    def free_generator_for_drive(self) -> int:
        """Return a generator whose drive path is free.

        :return: a generator index (0 if all are used)
        :rtype: int
        """
        # each generator has one drive path
        used = {d.generator for d in self.drive_tracks}
        return next((g for g in range(self.board_profile.generators) if g not in used), 0)

    def free_generator_for_tone(self) -> int:
        """Return a generator whose readout path is free.

        :return: a generator index (0 if all are used)
        :rtype: int
        """
        # each generator has one readout path
        used = {t.generator for r in self.readout_tracks for t in r.tones}
        return next((g for g in range(self.board_profile.generators) if g not in used), 0)

    def free_acquisition(self) -> int:
        """Return an acquisition IP not used by any window.

        :return: an acquisition IP index (0 if all are used)
        :rtype: int
        """
        used = {w.acquisition for _, w in self.all_windows()}
        return next((a for a in range(self.board_profile.acquisitions) if a not in used), 0)

    def readout_channel_of(self, tone: Tone) -> int:
        """Return the readout trigger channel of a tone.

        Tones that start at the same time share a trigger channel; each distinct start
        time gets the next channel (1, 2, ...). Acquisition windows inherit the channel of
        their tone, so a window is always triggered together with its tone.

        :param tone: readout tone.
        :type tone: Tone
        :return: the readout trigger channel (1-based)
        :rtype: int
        """
        starts: list[Param] = []
        # distinct start times, in track order; channel = position in this list + 1
        for _, t in self.all_tones():
            if not any(_same(t.start, s) for s in starts):
                starts.append(t.start)
        for i, s in enumerate(starts):
            if _same(tone.start, s):
                return i + 1
        return 1

    # ------------------------------------------------------------------ factories
    def add_drive_track(self, dac: str | None = None) -> DriveTrack:
        """Append a drive track on a free DAC with one gaussian pulse.

        :param dac: DAC channel name (None = first free one)
        :type dac: str | None
        :return: the new track.
        :rtype: DriveTrack
        """
        idx = len(self.drive_tracks)
        # free DAC and generator; one drive trigger channel per track
        tr = DriveTrack(
            dac=dac or self.free_dac(),
            generator=self.free_generator_for_drive(),
            trigger_channel=min(idx + 1, self.board_profile.trigger_channels),
            color=PALETTE[idx % len(PALETTE)],
        )
        self.drive_tracks.append(tr)
        self.add_drive_block(tr, self.snap_time(100.0))
        return tr

    def add_drive_block(self, tr: DriveTrack, start: float | None = None, shape: str = "gaussian", kind: str = "pulse") -> DriveBlock:
        """Append a pulse to a drive track (after the last one if no start is given).

        :param tr: drive track.
        :type tr: DriveTrack
        :param start: start time in ns (None = after the last pulse)
        :type start: float | None
        :param shape: envelope shape (see :data:`~FIREQ_GUI.core.envelopes.SHAPES`)
        :type shape: str
        :param kind: ``pulse`` or ``vz``.
        :type kind: str
        :return: the new block.
        :rtype: DriveBlock
        """
        if start is None:
            start = self.track_end(tr) + 10 * self.tick if tr.blocks else 100.0
        # the carrier is per generator: reuse the one of the track
        carrier = tr.blocks[0].carrier if tr.blocks else 5000.0
        base = "vz" if kind == "vz" else ("x180" if shape != "rect" else "rect")
        b = DriveBlock(
            name=unique_name(base, self.item_names()),
            kind=kind,
            start=self.snap_time(start),
            duration=0.0 if kind == "vz" else self.snap_time(200.0),
            carrier=carrier,
            shape=shape,
        )
        tr.blocks.append(b)
        return b

    def add_readout_track(self, dac: str | None = None) -> ReadoutTrack:
        """Append a readout track with one tone, and an acquisition window for it.

        :param dac: DAC channel name (None = first free one)
        :type dac: str | None
        :return: the new track.
        :rtype: ReadoutTrack
        """
        idx = len(self.readout_tracks)
        tr = ReadoutTrack(dac=dac or self.free_dac(), color=PALETTE[(idx + 2) % len(PALETTE)])
        self.readout_tracks.append(tr)
        self.add_tone(tr)
        return tr

    def add_tone(self, tr: ReadoutTrack, start: float | None = None) -> Tone:
        """Append a tone (lane) to a readout track.

        :param tr: readout track.
        :type tr: ReadoutTrack
        :param start: start time in ns (None = after the drive pulses, or with the previous tone)
        :type start: float | None
        :return: the new tone.
        :rtype: Tone
        """
        # default: read out after the drive pulses
        if start is None:
            start = max(self.drive_end() + 20 * self.tick, 100.0)
        # a new lane copies the previous tone (same start and duration), 20 MHz higher
        prev = tr.tones[-1] if tr.tones else None
        freq = 7500.0 if prev is None else prev.frequency
        if prev is not None and isinstance(freq, (int, float)):
            freq = float(freq) + 20.0
        t = Tone(
            name=unique_name(f"ro{len(self.all_tones())}", self.item_names()),
            frequency=freq,
            start=self.snap_time(start if prev is None else (self.ev(prev.start) or start)),
            duration=self.snap_time(1000.0) if prev is None else prev.duration,
            generator=self.free_generator_for_tone(),
        )
        tr.tones.append(t)
        return t

    def copy_to_acquisition(self, tone: Tone, acq_track: AcquisitionTrack | None = None, tof: Param = 200.0) -> AcqWindow:
        """Create the acquisition window of a tone (delayed by the time of flight).

        :param tone: readout tone.
        :type tone: Tone
        :param acq_track: acquisition track (None = first one, created if needed)
        :type acq_track: AcquisitionTrack | None
        :param tof: time of flight in ns.
        :type tof: Param
        :return: the window (the existing one if the tone is already acquired)
        :rtype: AcqWindow
        """
        if acq_track is None:
            acq_track = self.acq_tracks[0] if self.acq_tracks else self.add_acquisition_track()
        # one window per tone: copying twice returns the existing window
        existing = self.windows_of(tone.id)
        if existing:
            return existing[0][1]
        w = AcqWindow(
            tone_id=tone.id,
            tof=self.snap_time(tof) if isinstance(tof, (int, float)) else tof,
            duration=tone.duration,
            acquisition=self.free_acquisition(),
        )
        acq_track.windows.append(w)
        return w

    def add_acquisition_track(self, adc: str | None = None) -> AcquisitionTrack:
        """Append an acquisition track.

        :param adc: ADC channel name (None = first free one)
        :type adc: str | None
        :return: the new track.
        :rtype: AcquisitionTrack
        """
        tr = AcquisitionTrack(adc=adc or self.free_adc())
        self.acq_tracks.append(tr)
        return tr

    def remove_item(self, item_id: str) -> None:
        """Remove a track or an item; windows linked to a removed tone go too.

        :param item_id: id of a track or of a track item.
        :type item_id: str
        """
        tr, it = self.find(item_id)
        if tr is None:
            return
        # removing a whole track
        if it is None:
            for lst in (self.drive_tracks, self.readout_tracks, self.acq_tracks):
                if tr in lst:
                    lst.remove(tr)
            if isinstance(tr, ReadoutTrack):
                for t in tr.tones:
                    self._drop_windows(t.id)
            return
        # removing one item of a track
        for name in ("blocks", "tones", "windows"):
            lst = getattr(tr, name, None)
            if lst is not None and it in lst:
                lst.remove(it)
        if isinstance(it, Tone):
            self._drop_windows(it.id)

    def _drop_windows(self, tone_id: str) -> None:
        """Remove the acquisition windows linked to a tone.

        :param tone_id: id of the removed tone.
        :type tone_id: str
        """
        for a in self.acq_tracks:
            a.windows = [w for w in a.windows if w.tone_id != tone_id]

    def track_end(self, tr: DriveTrack) -> float:
        """Return the end of the last pulse of a drive track (preview point).

        :param tr: drive track.
        :type tr: DriveTrack
        :return: the end time in ns (0 for an empty track)
        :rtype: float
        """
        end = 0.0
        for b in tr.blocks:
            # VZ gates have no duration
            s, d = self.ev(b.start), self.ev(b.duration) if b.kind == "pulse" else 0.0
            if s is not None and d is not None:
                end = max(end, s + d)
        return end

    def drive_end(self) -> float:
        """Return the end of the last drive pulse (preview point).

        :return: the end time in ns (0 without drive pulses)
        :rtype: float
        """
        return max([self.track_end(t) for t in self.drive_tracks] or [0.0])

    # ------------------------------------------------------------------ serialisation
    def to_dict(self) -> dict:
        """Serialise to a JSON-compatible dict (project file).

        :return: the project content.
        :rtype: dict
        """
        # the version number lets future versions convert old project files
        return {"fireq_gui_project": 2, **asdict(self)}

    def to_json(self) -> str:
        """Serialise to JSON text.

        :return: the project file content.
        :rtype: str
        """
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def from_dict(cls, d: dict) -> Experiment:
        """Build an experiment from a project dict.

        :param d: project content (see :meth:`to_dict`)
        :type d: dict
        :return: the experiment; a hardware description stored in the project is registered as a board.
        :rtype: Experiment
        """
        d = copy.deepcopy(d)
        d.pop("fireq_gui_project", None)
        exp = _build(cls, d)
        # register the project's hardware first, so that the board key resolves
        if exp.hardware:
            register_board(exp.board, exp.hardware)
        # nested dataclasses are rebuilt explicitly
        exp.preprocess = dict(d.get("preprocess", {}))
        exp.variables = [_build(Variable, v) for v in d.get("variables", [])]
        exp.drive_tracks = []
        for td in d.get("drive_tracks", []):
            tr = _build(DriveTrack, td)
            tr.blocks = [_build(DriveBlock, b) for b in td.get("blocks", [])]
            exp.drive_tracks.append(tr)
        exp.readout_tracks = []
        for td in d.get("readout_tracks", []):
            tr = _build(ReadoutTrack, td)
            tr.tones = [_build(Tone, t) for t in td.get("tones", [])]
            exp.readout_tracks.append(tr)
        exp.acq_tracks = []
        for td in d.get("acq_tracks", []):
            tr = _build(AcquisitionTrack, td)
            tr.windows = [_build(AcqWindow, w) for w in td.get("windows", [])]
            exp.acq_tracks.append(tr)
        return exp

    @classmethod
    def from_json(cls, text: str) -> Experiment:
        """Parse a project JSON file.

        :param text: project file content.
        :type text: str
        :return: the experiment.
        :rtype: Experiment
        """
        return cls.from_dict(json.loads(text))


def _same(a: Param, b: Param) -> bool:
    """Tell whether two start times are the same (equal values or equal numbers).

    :param a: first time.
    :type a: Param
    :param b: second time.
    :type b: Param
    :return: True if equal.
    :rtype: bool
    """
    if a == b:
        return True
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(float(a) - float(b)) < 1e-6
    return False


def unique_name(base: str, taken: set[str]) -> str:
    """Return a valid identifier based on ``base`` that is not in ``taken``.

    :param base: preferred name (invalid characters are replaced)
    :type base: str
    :param taken: names already in use.
    :type taken: set[str]
    :return: ``base`` or ``base_1``, ``base_2``...
    :rtype: str
    """
    # names end up in the YAML as identifiers: letters, digits and "_" only, not starting with a digit
    base = "".join(c if c.isalnum() or c == "_" else "_" for c in base) or "x"
    if base[0].isdigit():
        base = "_" + base
    if base not in taken:
        return base
    i = 1
    # "ro0" -> "ro0_1" rather than "ro01"
    if base[-1].isdigit():
        base += "_"
        i = 1
    sep = "" if base.endswith("_") else "_"
    while f"{base}{sep}{i}" in taken:
        i += 1
    return f"{base}{sep}{i}"


def _build(cls: type, d: dict) -> Any:
    """Instantiate dataclass ``cls`` from a dict, ignoring unknown and nested keys.

    :param d: field values.
    :type d: dict
    :return: the instance.
    :rtype: Any
    """
    nested = {"blocks", "tones", "windows", "variables", "drive_tracks", "readout_tracks", "acq_tracks", "preprocess"}
    # nested lists are rebuilt by the caller; unknown keys (older/newer projects) are ignored
    names = {f.name for f in fields(cls)} - nested
    return cls(**{k: v for k, v in d.items() if k in names})


def default_experiment(board: str = "ZCU216") -> Experiment:
    """Return a two-qubit template: two drive DACs, one multiplexed readout DAC and one ADC.

    :param board: board key.
    :type board: str
    :return: a ready-to-run Rabi-like template.
    :rtype: Experiment
    """
    # Rabi on qubit 0 (gain sweep), plain pulse on qubit 1, two multiplexed tones when the board allows it
    exp = Experiment(name="rabi", board=board, shots=1000)
    exp.preprocess = {"q0_freq": 5625.5, "q1_freq": 5810.0}
    exp.variables = [Variable(name="gain_sweep", mode="lin", start=0.05, stop=0.3, num=101)]
    d0 = exp.add_drive_track()
    d0.blocks[0].carrier = "%q0_freq"
    d0.blocks[0].gain = "#gain_sweep"
    n_drive = min(2, exp.board_profile.generators)
    if n_drive > 1:
        d1 = exp.add_drive_track()
        d1.blocks[0].carrier = "%q1_freq"
    ro = exp.add_readout_track()
    ro.tones[0].frequency = 7583.46
    if exp.board_profile.frequency_mux and exp.board_profile.generators > 1:
        t1 = exp.add_tone(ro)
        t1.frequency = 7612.0
    acq = exp.add_acquisition_track()
    for t in ro.tones:
        exp.copy_to_acquisition(t, acq)
    return exp
