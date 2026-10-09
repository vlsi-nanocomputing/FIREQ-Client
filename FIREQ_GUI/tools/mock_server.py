"""Minimal FIREQ server simulator for testing the GUI without hardware.

It speaks the FIREQ wire protocol (4-byte length + msgpack header + optional payload),
accepts ``config_and_run`` and streams synthetic data:

* ``accumulated``: one IQ point per shot, two Gaussian blobs (|0>, |1>) whose population
  follows a Rabi-like oscillation on any swept gain, or a Lorentzian on a swept frequency;
* ``raw`` / ``decimated``: a ring-up trace per shot.

Run:  python -m FIREQ_GUI.tools.mock_server --port 5000
"""

from __future__ import annotations

import argparse
import itertools
import logging
import socket
import struct
import threading
import time

import msgpack
import numpy as np

log = logging.getLogger("mock_server")
DTYPE = [["real", "<f4"], ["imag", "<f4"]]


def _send(sock: socket.socket, header: dict, data: bytes = b"") -> None:
    """Send one framed message (4-byte length, msgpack header, payload).

    :param sock: connected socket.
    :type sock: socket.socket
    :param header: message header.
    :type header: dict
    :param data: binary payload.
    :type data: bytes
    """
    # the payload size travels in the header so the reader knows how much follows
    if data:
        header = {**header, "tsize": len(data)}
    hb = msgpack.packb(header)
    # big-endian 4-byte header length, then header, then payload
    sock.sendall(struct.pack(">I", len(hb)) + hb + data)


def _recv_exactly(sock: socket.socket, n: int) -> bytes:
    """Read exactly ``n`` bytes.

    :param sock: connected socket.
    :type sock: socket.socket
    :param n: number of bytes.
    :type n: int
    :return: the bytes.
    :rtype: bytes
    :raises ConnectionError: if the client closes the connection.
    """
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("closed")
        buf += chunk
    return buf


def _recv(sock: socket.socket) -> dict:
    """Read one framed message (the payload is discarded).

    :param sock: connected socket.
    :type sock: socket.socket
    :return: the header.
    :rtype: dict
    """
    size = struct.unpack("!I", _recv_exactly(sock, 4))[0]
    header = msgpack.unpackb(_recv_exactly(sock, size), raw=False)
    # consume the payload to keep the stream aligned
    if header.get("tsize"):
        _recv_exactly(sock, header["tsize"])
    return header


def _values(spec: dict) -> np.ndarray:
    """Return the values of a sweep variable description.

    :param spec: variable description.
    :type spec: dict
    :return: the values.
    :rtype: np.ndarray
    """
    mode = spec.get("mode", "lin")
    if mode == "list":
        return np.asarray(spec["values"], dtype=float)
    if mode == "const":
        return np.asarray([spec["value"]], dtype=float)
    return np.linspace(spec["start"], spec["stop"], int(spec["num"]))


class MockServer:
    """Single-client simulated server."""

    def __init__(
        self, host: str = "127.0.0.1", port: int = 5000, delay: float = 0.002, package_shots: int = 500, hardware: dict | None = None
    ) -> None:
        """Bind the listening socket; ``hardware`` is sent with the handshake and on get_hardware.

        :param host: address to bind.
        :type host: str
        :param port: port to bind (0 = any free port)
        :type port: int
        :param delay: seconds between DMA packages.
        :type delay: float
        :param package_shots: shots per DMA package.
        :type package_shots: int
        :param hardware: hardware description announced to clients (None = none)
        :type hardware: dict | None
        """
        self.hardware = hardware
        self.srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind((host, port))
        self.srv.listen(1)
        # the actual port, in case port 0 asked for any free one
        self.port = self.srv.getsockname()[1]
        self.delay = delay
        self.package_shots = package_shots
        self.abort = threading.Event()
        # fixed seed: synthetic data is reproducible between runs
        self.rng = np.random.default_rng(1)
        # the runner thread and the command loop write to the same socket
        self._send_lock = threading.Lock()

    def serve_forever(self, once: bool = False) -> None:
        """Accept clients one at a time.

        :param once: return after the first client disconnects.
        :type once: bool
        """
        while True:
            conn, addr = self.srv.accept()
            log.info("client %s", addr)
            try:
                self._handle(conn)
            except (ConnectionError, OSError) as e:
                log.info("client gone: %s", e)
            finally:
                conn.close()
            if once:
                return

    def send(self, conn: socket.socket, header: dict, data: bytes = b"") -> None:
        """Thread-safe send.

        :param conn: client socket.
        :type conn: socket.socket
        :param header: message header.
        :type header: dict
        :param data: binary payload.
        :type data: bytes
        """
        with self._send_lock:
            _send(conn, header, data)

    def _handle(self, conn: socket.socket) -> None:
        """Serve one client: handshake, then commands until it disconnects.

        :param conn: client socket.
        :type conn: socket.socket
        """
        # the server speaks first; hardware is announced only if configured
        self.send(conn, {"type": "handshake", **({"hardware": self.hardware} if self.hardware else {})})
        ack = _recv(conn)
        if ack.get("type") != "handshake_ack":
            raise ConnectionError("bad handshake")
        runner: threading.Thread | None = None
        # command loop: keeps reading while an experiment streams from another thread
        while True:
            msg = _recv(conn)
            cmd = msg.get("cmd")
            if cmd == "abort":
                # handled here so abort is seen while _run is streaming
                self.abort.set()
            elif cmd == "ping":
                self.send(conn, {"resp": "pong"})
            # without hardware fall through to the 'unrecognized command' error, like older servers
            elif cmd == "get_hardware" and self.hardware:
                self.send(conn, {"type": "hardware", "hardware": self.hardware})
            elif cmd == "reset_all":
                self.send(conn, {"type": "status", "msg": "successfully reset"})
            elif cmd in ("mts_sync", "set_nyquist"):
                log.info("%s %s", cmd, msg)
            elif cmd == "config_and_run":
                # only one experiment at a time
                if runner is not None and runner.is_alive():
                    self.send(conn, {"type": "warning", "msg": "experiment already running"})
                    continue
                self.abort.clear()
                # stream in a thread so abort and ping are still served
                runner = threading.Thread(target=self._run, args=(conn, msg), daemon=True)
                runner.start()
            # a bare generator header is a manual trigger request
            elif "generator" in msg:
                log.info("manual trigger %s", msg)
            else:
                self.send(conn, {"type": "error", "msg": f"unrecognized command '{cmd}'"})

    # ------------------------------------------------------------------ simulation
    def _run(self, conn: socket.socket, msg: dict) -> None:
        """Simulate a ``config_and_run`` command (single or swept).

        :param conn: client socket.
        :type conn: socket.socket
        :param msg: command message.
        :type msg: dict
        """
        system = msg.get("system", {})
        variables = msg.get("variables") or {}
        shots = int(system.get("$shots", 100))
        # only enabled acquisitions (non-zero $rchannel) produce data
        acqs = {k: v for k, v in system.items() if k.startswith("/axisAcquisitionIP") and v.get("$rchannel", 0)}
        t0 = time.time()
        try:
            if variables:
                order = list(variables)
                # announce the variable nesting order before the first point
                self.send(conn, {"type": "sweep_experiment_header", "variables_order": order})
                axes = [_values(variables[v]) for v in order]
                # product() varies the last variable fastest, matching the client index advance
                for point in itertools.product(*axes):
                    # stop between points on abort; the final status is still sent
                    if self.abort.is_set():
                        break
                    self._one(conn, acqs, shots, dict(zip(order, point)), system)
                self.send(conn, {"type": "status", "msg": "sweep ended", "time": f"{int((time.time() - t0) * 1e9)} ns"})
            else:
                self._one(conn, acqs, shots, {}, system)
        # the client disconnected mid-run
        except OSError:
            pass

    def _one(self, conn: socket.socket, acqs: dict, shots: int, point: dict, system: dict) -> None:
        """Simulate one experiment (one sweep point).

        :param conn: client socket.
        :type conn: socket.socket
        :param acqs: enabled acquisition nodes.
        :type acqs: dict
        :param shots: shots to send.
        :type shots: int
        :param point: sweep point.
        :type point: dict
        :param system: system configuration.
        :type system: dict
        """
        self.send(conn, {"type": "status", "msg": "experiment started"})
        t0 = time.time()
        for k, (src, node) in enumerate(acqs.items()):
            otype = node.get("$output_type", "accumulated")
            sent = 0
            while sent < shots:
                n = min(self.package_shots, shots - sent)
                arr = self._data(otype, n, point, node, k)
                # pack as a structured float32 (real, imag) array matching DTYPE
                rec = np.empty(arr.size, dtype=np.dtype([tuple(x) for x in DTYPE]))
                rec["real"], rec["imag"] = arr.real, arr.imag
                self.send(conn, {"type": "dma_package", "source": src, "shots": n, "format": DTYPE}, rec.tobytes())
                sent += n
                time.sleep(self.delay)
        self.send(conn, {"type": "status", "msg": "experiment ended", "time": f"{int((time.time() - t0) * 1e9)} ns"})

    def _excited_prob(self, point: dict) -> float:
        """Return a plausible excited-state population for a sweep point.

        :param point: sweep point (variable name -> value)
        :type point: dict
        :return: the population, 0..1.
        :rtype: float
        """
        p = 0.0
        for name, v in point.items():
            n = name.lower()
            # rabi: population oscillates with the drive gain
            if "gain" in n:
                p = np.sin(np.pi * v / 0.2) ** 2 * 0.95
            # ramsey-like decaying fringes on delay sweeps
            elif "delay" in n or "tau" in n:
                p = 0.5 + 0.45 * np.exp(-v / 20000) * np.cos(2 * np.pi * v / 8000)
            # qubit spectroscopy: Lorentzian around a fixed qubit frequency
            elif "freq" in n:
                p = 0.9 / (1 + ((v - 5625.3) / 0.8) ** 2) + p
        return float(min(max(p, 0.0), 1.0))

    def _data(self, otype: str, n: int, point: dict, node: dict, k: int) -> np.ndarray:
        """Generate the samples of one DMA package.

        :param otype: output type.
        :type otype: str
        :param n: number of shots.
        :type n: int
        :param point: sweep point.
        :type point: dict
        :param node: acquisition node.
        :type node: dict
        :param k: index of the acquisition (rotates the IQ blobs)
        :type k: int
        :return: complex64 samples.
        :rtype: np.ndarray
        """
        p = self._excited_prob(point)
        # |0> and |1> blob centres, rotated per acquisition to tell sources apart
        c0 = (1.0 + 0.3j) * np.exp(1j * 0.7 * k)
        c1 = (-0.4 + 0.9j) * np.exp(1j * 0.7 * k)
        freq_vals = [v for name, v in point.items() if "freq" in name.lower()]
        # no qubit response on a frequency sweep: resonator dip moves the |0> blob
        if freq_vals and not p:
            lor = 1 / (1 + ((freq_vals[0] - 7583.5) / 1.5) ** 2)
            c0 = (1 - 0.8 * lor) * np.exp(1j * (np.arctan((freq_vals[0] - 7583.5) / 1.5)))
        # draw each shot's state from the population
        excited = self.rng.random(n) < p
        centers = np.where(excited, c1, c0)
        noise = 0.15 * (self.rng.standard_normal(n) + 1j * self.rng.standard_normal(n))
        if otype == "accumulated":
            return (centers + noise).astype(np.complex64)
        length = 256 if otype == "decimated" else 1024
        t = np.arange(length) / length
        ring = 1 - np.exp(-t / 0.08)
        # no signal before the time of flight (sweepable as 'tof')
        tof = float(point.get("tof", node.get("$tof", 0)) or 0)
        ring = np.where(t * 2000 > tof, ring, 0)
        traces = centers[:, None] * ring[None, :] + 0.2 * (
            self.rng.standard_normal((n, length)) + 1j * self.rng.standard_normal((n, length))
        )
        return traces.ravel().astype(np.complex64)


def main() -> None:
    """Command line entry point."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=5000)
    ap.add_argument("--delay", type=float, default=0.002, help="seconds between DMA packages")
    ap.add_argument("--hardware", help="hardware description (JSON/YAML) to announce to the client")
    args = ap.parse_args()
    hardware = None
    # optional hardware description, tagged with its boards.json key
    if args.hardware:
        from FIREQ_GUI.core.boards import load_hardware_file

        key, hardware = load_hardware_file(args.hardware)
        hardware = {"key": key, **hardware}
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    srv = MockServer(args.host, args.port, args.delay, hardware=hardware)
    log.info("mock FIREQ server on %s:%s", args.host, srv.port)
    srv.serve_forever()


if __name__ == "__main__":
    main()
