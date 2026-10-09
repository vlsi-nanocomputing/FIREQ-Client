"""FIREQ YAML -> timeline model (best effort).

Reconstructs drive, readout and acquisition tracks from generator, acquisition and
trigger nodes so that existing experiment files (``yaml_experiment_configurations_examples``)
can be opened and edited graphically. Anything that cannot be mapped is reported.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import yaml

from .envelopes import envelope, samples_from_yaml
from .expressions import Param, add
from .model import PALETTE, AcqWindow, AcquisitionTrack, DriveBlock, DriveTrack, Experiment, ReadoutTrack, Tone, Variable

GEN_PREFIX = "/axisGeneratorIP_"
ACQ_PREFIX = "/axisAcquisitionIP_"
TRIG_PREFIX = "/axisTriggerGenerator_"


def _index(key: str, prefix: str) -> int:
    """Return the instance number of a node path (``/axisGeneratorIP_1`` -> 1).

    :param key: node path.
    :type key: str
    :param prefix: node path without the index.
    :type prefix: str
    :return: the index (0 if it is not a number)
    :rtype: int
    """
    try:
        return int(key[len(prefix) :])
    except ValueError:
        return 0


def _lowest_bit(mask: int) -> int:
    """Return the position of the lowest set bit of a mask.

    :param mask: bit mask.
    :type mask: int
    :return: the bit position (-1 for 0)
    :rtype: int
    """
    return (mask & -mask).bit_length() - 1


def _guess_shape(samples: np.ndarray) -> tuple[str, dict]:
    """Find the built-in shape that best matches imported samples.

    :param samples: imported envelope samples.
    :type samples: np.ndarray
    :return: the shape name and its parameters (``custom`` if nothing matches within 2%)
    :rtype: tuple[str, dict]
    """
    n = len(samples)
    if n < 2:
        return "custom", {}
    # compare with the built-in shapes (Gaussian over a range of widths); accept a match within 2% of full scale
    best, best_err, best_kw = "custom", 0.02, {}
    candidates: list[tuple[str, dict]] = [("cosine", {}), ("sine", {}), ("triangle", {})]
    candidates += [("gaussian", {"sigma": float(s)}) for s in np.linspace(0.05, 0.5, 46)]
    for shape, kw in candidates:
        ref = envelope(shape, n, **kw)
        err = float(np.max(np.abs(ref - samples / max(np.max(np.abs(samples)), 1e-12))))
        if err < best_err:
            best, best_err, best_kw = shape, err, kw
    return best, best_kw


def import_yaml_text(text: str, board: str = "ZCU216", name: str = "imported") -> tuple[Experiment, list[str]]:
    """Parse YAML text into an experiment; return (experiment, warnings).

    :param text: YAML (or JSON) text.
    :type text: str
    :param board: board used to map ``_dac_target`` and acquisitions to channels.
    :type board: str
    :param name: experiment name.
    :type name: str
    :return: the experiment and a list of warnings.
    :rtype: tuple[Experiment, list[str]]
    """
    data = yaml.safe_load(text) or {}
    warnings: list[str] = []
    # imported times are kept exactly as written (no tick snapping)
    exp = Experiment(name=name, board=board, snap=False)
    exp.preprocess = dict(data.get("preprocess") or {})
    # ---- sweep variables
    for vname, vd in (data.get("variables") or {}).items():
        mode = vd.get("mode", "lin")
        v = Variable(name=vname, mode=mode)
        if mode == "list":
            v.values = [float(x) for x in vd.get("values", [])]
        elif mode == "const":
            v.value = float(vd.get("value", 0))
        else:
            v.start, v.stop, v.num = float(vd.get("start", 0)), float(vd.get("stop", 1)), int(vd.get("num", 2))
        exp.variables.append(v)
    pre, var_names = exp.preprocess, exp.var_names
    sysc = data.get("sys_config") or {}
    exp.shots = sysc.get("$shots", 1000)
    bp = exp.board_profile

    # ---- hardware nodes by index
    gens = {_index(k, GEN_PREFIX): v or {} for k, v in sysc.items() if k.startswith(GEN_PREFIX)}
    acqs = {_index(k, ACQ_PREFIX): v or {} for k, v in sysc.items() if k.startswith(ACQ_PREFIX)}
    trig_key = next((k for k in sysc if k.startswith(TRIG_PREFIX)), "/axisTriggerGenerator_0")
    exp.trigger_node = trig_key
    trig = sysc.get(trig_key) or {}
    # report everything the timeline model cannot represent
    for k in sysc:
        if not (k == "$shots" or k.startswith((GEN_PREFIX, ACQ_PREFIX, TRIG_PREFIX))):
            warnings.append(f"unsupported node/parameter ignored: {k}")

    # ---- absolute times of the trigger FIFO entries
    t: Param = 0
    events = []
    # the FIFO delays are relative: accumulate them to get the absolute time of each trigger
    for d in sorted(trig.get("delay") or [], key=lambda d: d.get("_index", 0)):
        t = add(t, d.get("$delay", 0), pre, var_names)
        events.append((t, d.get("_ttype", "drive"), int(d.get("_channel_mask", d.get("_channel", 1)))))
    exp.experiment_duration = trig.get("$experiment_duration")

    def bit_of(ch: int) -> int:
        """Return the trigger mask bit of a trigger channel.

        :param ch: trigger channel (1-based; 0 = disabled)
        :type ch: int
        :return: the mask bit (0 if disabled)
        :rtype: int
        """
        return 1 << (ch - 1) if ch and ch > 0 else 0

    def dac_name(mask: int, fallback_index: int) -> str:
        """Return the DAC channel selected by a ``_dac_target`` mask.

        :param mask: ``_dac_target`` value.
        :type mask: int
        :param fallback_index: generator index (unused, kept for messages)
        :type fallback_index: int
        :return: the channel name (a free DAC if no channel matches)
        :rtype: str
        """
        # the crossbar bit of the mask identifies the DAC on the selected board
        c = bp.dac_for_bit(_lowest_bit(mask))
        if c is None:
            warnings.append(f"no DAC of {bp.title} is routed to crossbar bit {_lowest_bit(mask)}")
            return exp.free_dac()
        return c.name

    # ---- drive tracks
    for g, node in sorted(gens.items()):
        envs = {e["_name"]: e for e in node.get("envelope") or []}
        pulses = {p["_name"]: p for p in node.get("pulse") or [] if not p.get("_readout", False)}
        vz = {z["_name"]: z for z in node.get("vzgate") or [] if not z.get("_readout", False)}
        if not pulses and not vz:
            continue
        dch = int(node.get("$dchannel", 0) or 0)
        # a drive track has one DAC: take it from the first drive pulse
        mask = next((int(p.get("_dac_target", 1)) for p in pulses.values()), 1)
        if mask & (mask - 1):
            warnings.append(f"{GEN_PREFIX}{g}: _dac_target {mask} drives several DACs, using the first one")
        tr = DriveTrack(dac=dac_name(mask, g), generator=g, trigger_channel=dch or 1, color=PALETTE[len(exp.drive_tracks) % len(PALETTE)])
        # the k-th drive trigger on this generator's channel plays the k-th gate of $drive_order
        times = [ev[0] for ev in events if ev[1] == "drive" and ev[2] & bit_of(dch)]
        order = list(node.get("$drive_order") or []) or list(pulses)
        k = 0
        last_end: Param = 0
        carrier = node.get("$dfrequency", 0)
        for pname in order:
            # VZ gates take no trigger: place them right after the previous pulse
            if pname in vz:
                tr.blocks.append(DriveBlock(name=pname, kind="vz", start=last_end, duration=0, carrier=carrier, vz_rotation=vz[pname].get("$vz_rotation", 0)))
                continue
            p = pulses.get(pname)
            if p is None:
                warnings.append(f"$drive_order: pulse '{pname}' is not defined")
                continue
            start = times[k] if k < len(times) else last_end
            if k >= len(times):
                warnings.append(f"{pname}: no matching drive trigger, placed after the previous pulse")
            k += 1
            b = DriveBlock(
                name=pname,
                start=start,
                duration=p.get("$duration", 100),
                carrier=carrier,
                gain=p.get("$gain", 0.1),
                shape="rect",
                switch_iq=bool(p.get("_switch_iq", False)),
                keep_last=bool(p.get("_keep_last", False)),
            )
            # custom envelopes: recover the shape from the samples so that it stays editable
            env_name = p.get("_envelope", "_RECTANGULAR")
            if env_name != "_RECTANGULAR":
                e = envs.get(env_name)
                if e is None:
                    warnings.append(f"{pname}: envelope '{env_name}' not defined in the file (server state?)")
                else:
                    smp = samples_from_yaml(e.get("$samples"))
                    b.for_interpolation = bool(e.get("_for_interpolation", False))
                    b.symmetric = bool(e.get("_is_symmetric", False))
                    # symmetric envelopes store only the first half: mirror it back
                    if b.symmetric:
                        smp = np.concatenate([smp, smp[::-1]])
                    b.n_samples = max(len(smp), 2)
                    b.shape, kw = _guess_shape(smp)
                    for key, val in kw.items():
                        setattr(b, key, val)
                    if b.shape == "custom":
                        warnings.append(f"{pname}: unknown envelope shape, replaced by a custom shape to review")
            tr.blocks.append(b)
            last_end = add(start, b.duration, pre, var_names)
        if k < len(times):
            warnings.append(f"{GEN_PREFIX}{g}: {len(times) - k} drive triggers without a pulse")
        exp.drive_tracks.append(tr)

    # ---- readout tracks (one per readout DAC) and acquisition windows
    by_dac: dict[str, ReadoutTrack] = {}
    used_acq: set[int] = set()
    for g, node in sorted(gens.items()):
        for p in node.get("pulse") or []:
            if not p.get("_readout", False):
                continue
            # a readout pulse is a tone; tones on the same DAC share a readout track
            rch = int(node.get("$rchannel", 1) or 1)
            mask = int(p.get("_dac_target", 1))
            dac = dac_name(mask, g)
            if dac not in by_dac:
                by_dac[dac] = ReadoutTrack(dac=dac, color=PALETTE[(len(by_dac) + 2) % len(PALETTE)])
            # the tone starts at the first readout trigger on its channel
            times = [ev[0] for ev in events if ev[1] == "readout" and ev[2] & bit_of(rch)]
            if not times:
                warnings.append(f"{p['_name']}: no readout trigger on channel {rch}")
            tone = Tone(
                name=p["_name"],
                frequency=node.get("$rfrequency", 0),
                start=times[0] if times else 0,
                duration=p.get("$duration", 1000),
                gain=p.get("$gain", 0.1),
                phase=node.get("$rphase", 0.0),
                generator=g,
            )
            by_dac[dac].tones.append(tone)
            # matching acquisition: same frequency and trigger channel, else the first unused one
            cands = [a for a in sorted(acqs) if a not in used_acq and acqs[a].get("$rchannel", 1)]
            acq = next((a for a in cands if acqs[a].get("$rfrequency") == node.get("$rfrequency")), cands[0] if cands else None)
            if acq is None:
                continue
            used_acq.add(acq)
            an = acqs[acq]
            # the acquisition window goes on the track of the ADC that feeds this acquisition IP
            adc = bp.adc_for_acq(acq)
            adc_name = adc.name if adc else exp.free_adc()
            atr = next((a for a in exp.acq_tracks if a.adc == adc_name), None)
            if atr is None:
                atr = AcquisitionTrack(adc=adc_name)
                exp.acq_tracks.append(atr)
            atr.windows.append(
                AcqWindow(
                    tone_id=tone.id,
                    tof=an.get("$tof", 0),
                    duration=an.get("$duration", tone.duration),
                    output_type=an.get("$output_type", "accumulated"),
                    demod_phase=an.get("$rphase", 0.0),
                    acquisition=acq,
                )
            )
    exp.readout_tracks = list(by_dac.values())
    for a in acqs:
        if a not in used_acq and acqs[a].get("$rchannel", 0):
            warnings.append(f"{ACQ_PREFIX}{a}: acquisition without a readout tone (ignored)")
    return exp, warnings


def import_yaml_file(path: str | Path, board: str = "ZCU216") -> tuple[Experiment, list[str]]:
    """Import a YAML experiment file.

    :param path: YAML file.
    :type path: str | Path
    :param board: board used to map the hardware nodes to channels.
    :type board: str
    :return: the experiment (named after the file) and a list of warnings.
    :rtype: tuple[Experiment, list[str]]
    """
    p = Path(path)
    return import_yaml_text(p.read_text(encoding="utf-8"), board=board, name=p.stem)
