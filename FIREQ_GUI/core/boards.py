"""Board profiles: RF converters, SMA connectors, FIREQ IP resources and time base.

Profiles are loaded from ``boards.json`` (next to this file). A user file can override
or extend them: ``~/.fireq_gui/boards.json`` or the path in ``$FIREQ_GUI_BOARDS``.

Converter channels are named as in the AMD RF Data Converter IP: ``<tile>_<block>``
(``228_0`` is block 0 of DAC tile 228).
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_BOARDS_FILE = Path(__file__).with_name("boards.json")


@dataclass
class Connector:
    """One converter channel and its SMA connector.

    :param kind: ``dac`` or ``adc``.
    :type kind: str
    :param tile: RF Data Converter tile number (e.g. 228)
    :type tile: int
    :param block: block (channel) inside the tile.
    :type block: int
    :param sma: label printed next to the SMA connector, if any.
    :type sma: str
    :param crossbar_bit: DAC only: crossbar output (bit of ``_dac_target``) that reaches this DAC; None if not
        routed.
    :type crossbar_bit: int | None
    :param acq_ips: ADC only: acquisition IPs fed by this ADC; None = unknown/any, empty = not routed.
    :type acq_ips: list[int] | None
    :param note: free text (e.g. 'verify')
    :type note: str
    """

    kind: str  # "dac" | "adc"
    tile: int
    block: int
    sma: str = ""
    crossbar_bit: int | None = None  # DAC only
    acq_ips: list[int] | None = None  # ADC only (None = unknown / any)
    note: str = ""

    @property
    def name(self) -> str:
        """Return the AMD channel name, e.g. ``228_0``.

        :return: the channel name ``<tile>_<block>``.
        :rtype: str
        """
        return f"{self.tile}_{self.block}"

    @property
    def label(self) -> str:
        """Return the display label, e.g. ``DAC 228_0``.

        :return: the channel kind and name, e.g. ``DAC 228_0``.
        :rtype: str
        """
        return f"{self.kind.upper()} {self.name}"

    @property
    def routed(self) -> bool:
        """Return True if the channel is reachable with the current bitstream.

        :return: True if the bitstream connects this channel to an IP.
        :rtype: bool
        """
        if self.kind == "dac":
            return self.crossbar_bit is not None
        return self.acq_ips is None or len(self.acq_ips) > 0

    @property
    def dac_mask(self) -> int:
        """Return the ``_dac_target`` mask that selects this DAC (0 if not routed).

        :return: the ``_dac_target`` value (``1 << crossbar_bit``), 0 if not routed.
        :rtype: int
        """
        return 0 if self.crossbar_bit is None else 1 << self.crossbar_bit


@dataclass
class Board:
    """Hardware profile of a supported board.

    :param key: key of the board in the board list.
    :type key: str
    :param title: name shown to the user.
    :type title: str
    :param generators: number of axisGeneratorIP instances.
    :type generators: int
    :param acquisitions: number of axisAcquisitionIP instances.
    :type acquisitions: int
    :param trigger_channels: number of channels of the trigger generator.
    :type trigger_channels: int
    :param frequency_mux: True if a frequency multiplexer sums several readout tones on one DAC.
    :type frequency_mux: bool
    :param dac_fs_mhz: DAC sampling rate in MHz.
    :type dac_fs_mhz: float
    :param adc_fs_mhz: ADC sampling rate in MHz.
    :type adc_fs_mhz: float
    :param trigger_clock_mhz: clock of the trigger generator in MHz (sets the timeline tick)
    :type trigger_clock_mhz: float
    :param raw_buffer_samples: size of the raw acquisition buffer in samples.
    :type raw_buffer_samples: int
    :param dacs: DAC channels.
    :type dacs: list[Connector]
    :param adcs: ADC channels.
    :type adcs: list[Connector]
    """

    key: str
    title: str
    generators: int
    acquisitions: int
    trigger_channels: int
    frequency_mux: bool
    dac_fs_mhz: float
    adc_fs_mhz: float
    trigger_clock_mhz: float
    raw_buffer_samples: int
    dacs: list[Connector] = field(default_factory=list)
    adcs: list[Connector] = field(default_factory=list)

    @property
    def tick_ns(self) -> float:
        """Return the time resolution of the trigger generator in ns.

        :return: one trigger generator clock period in ns (1000 / trigger_clock_mhz)
        :rtype: float
        """
        return 1000.0 / self.trigger_clock_mhz if self.trigger_clock_mhz > 0 else 1.0

    def dac(self, name: str) -> Connector | None:
        """Find a DAC by AMD name (``228_0``).

        :param name: AMD channel name, e.g. ``228_0``.
        :type name: str
        :return: the DAC connector, or None if the board has no such channel.
        :rtype: Connector | None
        """
        return next((c for c in self.dacs if c.name == name), None)

    def adc(self, name: str) -> Connector | None:
        """Find an ADC by AMD name (``224_0``).

        :param name: AMD channel name, e.g. ``224_0``.
        :type name: str
        :return: the ADC connector, or None if the board has no such channel.
        :rtype: Connector | None
        """
        return next((c for c in self.adcs if c.name == name), None)

    def dac_for_bit(self, bit: int) -> Connector | None:
        """Return the DAC driven by a crossbar output bit.

        :param bit: crossbar output bit (bit of ``_dac_target``)
        :type bit: int
        :return: the DAC reached by that bit, or None.
        :rtype: Connector | None
        """
        return next((c for c in self.dacs if c.crossbar_bit == bit), None)

    def adc_for_acq(self, acq: int) -> Connector | None:
        """Return the ADC that feeds an acquisition IP (first match).

        :param acq: acquisition IP index.
        :type acq: int
        :return: the ADC that feeds the acquisition IP; an ADC with unknown routing if none matches exactly; or
            None.
        :rtype: Connector | None
        """
        exact = next((c for c in self.adcs if c.acq_ips and acq in c.acq_ips), None)
        return exact or next((c for c in self.adcs if c.acq_ips is None), None)


def nyquist_zone(freq_mhz: float, fs_mhz: float) -> int:
    """Return the Nyquist zone (1-based) of a frequency for a given sampling rate.

    :param freq_mhz: signal frequency in MHz.
    :type freq_mhz: float
    :param fs_mhz: sampling rate in MHz.
    :type fs_mhz: float
    :return: the Nyquist zone, 1 for 0..fs/2, 2 for fs/2..fs, ...
    :rtype: int
    """
    if fs_mhz <= 0:
        return 1
    return int(math.floor(abs(freq_mhz) / (fs_mhz / 2.0))) + 1


def _connector(d: dict, kind: str) -> Connector:
    """Build a :class:`Connector` from its JSON description.

    :param d: connector entry (tile, block, sma, crossbar_bit, acq_ips, note)
    :type d: dict
    :param kind: ``dac`` or ``adc``.
    :type kind: str
    :return: the connector.
    :rtype: Connector
    """
    return Connector(
        kind=kind,
        tile=int(d["tile"]),
        block=int(d["block"]),
        sma=d.get("sma", ""),
        crossbar_bit=d.get("crossbar_bit"),
        # a missing "acq_ips" means "unknown" (None), an empty list means "not routed"
        acq_ips=d.get("acq_ips") if "acq_ips" in d else None,
        note=d.get("note", ""),
    )


def _board(key: str, d: dict) -> Board:
    """Build a :class:`Board` from a complete board description.

    :param key: key of the board in the board list.
    :type key: str
    :param d: board description with the fields of a boards.json entry.
    :type d: dict
    :return: the board profile.
    :rtype: Board
    """
    # every field has a default so that partial descriptions still load
    return Board(
        key=key,
        title=d.get("title", key),
        generators=int(d.get("generators", 2)),
        acquisitions=int(d.get("acquisitions", 2)),
        trigger_channels=int(d.get("trigger_channels", 15)),
        frequency_mux=bool(d.get("frequency_mux", False)),
        dac_fs_mhz=float(d.get("dac_fs_mhz", 0)),
        adc_fs_mhz=float(d.get("adc_fs_mhz", 0)),
        trigger_clock_mhz=float(d.get("trigger_clock_mhz", 583.68)),
        raw_buffer_samples=int(d.get("raw_buffer_samples", 16384)),
        dacs=[_connector(c, "dac") for c in d.get("dacs", [])],
        adcs=[_connector(c, "adc") for c in d.get("adcs", [])],
    )


def load_boards(extra_file: str | os.PathLike | None = None) -> dict[str, Board]:
    """Load the built-in board profiles, then apply user overrides if present.

    :param extra_file: optional file with additional/overriding boards; defaults to ``$FIREQ_GUI_BOARDS`` or
        ``~/.fireq_gui/boards.json``.
    :type extra_file: str | os.PathLike | None
    :return: the board profiles by key.
    :rtype: dict[str, Board]
    """
    # built-in profiles first, then the user file (same key = override)
    files = [DEFAULT_BOARDS_FILE]
    user = extra_file or os.environ.get("FIREQ_GUI_BOARDS") or Path.home() / ".fireq_gui" / "boards.json"
    if user and Path(user).is_file():
        files.append(Path(user))
    boards: dict[str, Board] = {}
    for f in files:
        with open(f, encoding="utf-8") as fh:
            data = json.load(fh)
        for key, d in data.get("boards", {}).items():
            # keep the raw dict too: it is the "base" of hardware descriptions
            RAW_BOARDS[key] = d
            boards[key] = _board(key, d)
    return boards


# --------------------------------------------------------------------------- hardware descriptions
# A hardware description has the same fields as an entry of boards.json. It can come from a
# file today and from FIREQ-Server after the connection in the future. Missing fields are taken
# from the board named in "base" (e.g. a file with only "base": "ZCU216", "generators": 4).

# fields understood in a hardware description (reference for files and server messages)
HARDWARE_KEYS = (
    "title", "generators", "acquisitions", "trigger_channels", "frequency_mux",
    "dac_fs_mhz", "adc_fs_mhz", "trigger_clock_mhz", "raw_buffer_samples", "dacs", "adcs",
)  # fmt: skip


def resolve_hardware(d: dict) -> dict:
    """Return a complete board dict: the fields of ``d`` over those of its ``base`` board.

    :param d: hardware description, possibly with a ``base`` board and only some fields.
    :type d: dict
    :return: the complete description (base fields overridden by those of ``d``)
    :rtype: dict
    :raises ValueError: if the base board is unknown or required fields are missing.
    """
    base = d.get("base")
    out: dict = {}
    # start from the base board, then apply the fields given explicitly
    if base:
        if base not in RAW_BOARDS:
            raise ValueError(f"unknown base board {base!r} (known: {', '.join(RAW_BOARDS)})")
        out.update(RAW_BOARDS[base])
    out.update({k: v for k, v in d.items() if k != "base"})
    # the IP counts and the channel lists are mandatory once the base is applied
    missing = [k for k in ("generators", "acquisitions") if k not in out]
    if missing:
        raise ValueError(f"hardware description without {', '.join(missing)}")
    if not out.get("dacs") or not out.get("adcs"):
        raise ValueError("hardware description without 'dacs'/'adcs' (give them or a 'base' board)")
    return out


def register_board(key: str, d: dict) -> Board:
    """Add (or replace) a board profile at runtime from a hardware description.

    :param key: key under which the board is registered.
    :type key: str
    :param d: hardware description (see :func:`resolve_hardware`)
    :type d: dict
    :return: the registered board profile.
    :rtype: Board
    """
    full = resolve_hardware(d)
    # registered boards appear in the board list of the Experiment tab
    RAW_BOARDS[key] = full
    BOARDS[key] = _board(key, full)
    return BOARDS[key]


def parse_hardware(data: dict, default_key: str = "custom") -> tuple[str, dict]:
    """Extract (key, description) from a parsed file or server message.

    Accepted shapes: a board dict, ``{"hardware": {...}}`` or ``{"boards": {key: {...}}}``.

    :param data: parsed JSON/YAML content or server message.
    :type data: dict
    :param default_key: key used when the description does not name one.
    :type default_key: str
    :return: the board key and its description.
    :rtype: tuple[str, dict]
    """
    # server messages wrap the description in a "hardware" field
    if "hardware" in data and isinstance(data["hardware"], dict):
        data = data["hardware"]
    # a boards.json-like file: take its first board
    if "boards" in data and isinstance(data["boards"], dict) and data["boards"]:
        key, d = next(iter(data["boards"].items()))
        return key, d
    # a single board description, optionally naming its key
    return str(data.get("key", default_key)), {k: v for k, v in data.items() if k != "key"}


def load_hardware_file(path: str | os.PathLike) -> tuple[str, dict]:
    """Read a hardware description (JSON or YAML) and return (key, description).

    :param path: path of a ``.json``, ``.yaml`` or ``.yml`` file.
    :type path: str | os.PathLike
    :return: the board key and its description (validated)
    :rtype: tuple[str, dict]
    """
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    # YAML for hand-written files, JSON otherwise
    if p.suffix.lower() in (".yaml", ".yml"):
        import yaml

        data = yaml.safe_load(text) or {}
    else:
        data = json.loads(text)
    key, d = parse_hardware(data, default_key=p.stem)
    resolve_hardware(d)  # validate now, with a clear message
    return key, d


# board registry: raw descriptions (bases for hardware files) and parsed profiles by key
RAW_BOARDS: dict[str, dict] = {}
BOARDS = load_boards()
# keys of the profiles shipped with the GUI (anything else was loaded at runtime)
BUILTIN_BOARDS = frozenset(BOARDS)
