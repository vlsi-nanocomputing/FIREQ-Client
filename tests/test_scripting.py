"""Tests of the scriptable client API against a scripted fake server over TCP."""

import socket
import struct
import threading

import msgpack
import numpy as np
import pytest

from FIREQ_CLIENT import (
    ConfigurationError,
    ConnectionFailed,
    ConnectionLost,
    Desynchronized,
    FireqClient,
    FireqTimeout,
    RunFailed,
    RunRejected,
    load_experiment,
)
from FIREQ_CLIENT.cli.shell import InteractiveShell, parse_overrides
from FIREQ_CLIENT.network import Message

FORMAT = [["imag", "<i2"], ["real", "<i2"]]
OK = {"type": "status", "msg": "ok"}
CONFIG = {"preprocess": {"shots": 2}, "sys_config": {"$shots": "%shots"}, "variables": {}}


def _header(values):
    return {
        "type": "status",
        "msg": "experiment_header",
        "variable_order": ["a"],
        "variable_values": {"a": values},
    }


def _point(real, time_ns=10):
    data = np.zeros(2, dtype=np.dtype([tuple(f) for f in FORMAT]))
    data["real"] = real
    return [
        ({"type": "dma_package", "source": "/acq0", "shots": 2, "format": FORMAT}, data.tobytes()),
        ({"type": "status", "msg": "iteration_ended", "time": time_ns}, b""),
    ]


def _footer(aborted=False):
    return ({"type": "status", "msg": "experiment_footer", "run_time": 99, "aborted": aborted}, b"")


def _sweep(values):
    replies = [(OK, b""), (_header(values), b"")]
    for index, _ in enumerate(values):
        replies += _point(index + 1)
    return [*replies, _footer()]


class FakeServer:
    """Accept connections, send the handshake and answer each command with ``handler(header)``."""

    def __init__(self, handler):
        self.handler = handler
        self.received = []
        self.connections = 0
        self.listener = socket.create_server(("127.0.0.1", 0))
        self.port = self.listener.getsockname()[1]
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        while True:
            try:
                conn, _ = self.listener.accept()
            except OSError:
                return
            self.connections += 1
            threading.Thread(target=self._session, args=(conn,), daemon=True).start()

    @staticmethod
    def _recv(conn, n):
        data = b""
        while len(data) < n:
            chunk = conn.recv(n - len(data))
            if not chunk:
                raise ConnectionError
            data += chunk
        return data

    def _session(self, conn):
        with conn:
            try:
                conn.sendall(b"".join(Message({"type": "handshake"}).to_buffers()))
                while True:
                    size = struct.unpack(">I", self._recv(conn, 4))[0]
                    header = msgpack.unpackb(self._recv(conn, size))
                    self.received.append(header)
                    replies = self.handler(header)
                    if replies is None:
                        return  # close the connection
                    for reply, data in replies:
                        conn.sendall(b"".join(Message(reply, data).to_buffers()))
            except (ConnectionError, OSError):
                return

    def close(self):
        self.listener.close()


def _default_handler(header):
    if header.get("type") == "handshake_ack":
        return []
    if header.get("cmd") == "config_and_run":
        return _sweep([0.1, 0.2, 0.3])
    return [({"resp": "pong"}, b"")]


@pytest.fixture
def server():
    srv = FakeServer(_default_handler)
    yield srv
    srv.close()


@pytest.fixture
def fq(server, tmp_path):
    client = FireqClient("127.0.0.1", server.port, output_dir=tmp_path, command_timeout=2)
    yield client
    client.close()


def test_run_returns_the_result_and_prints_nothing(fq, server, capsys):
    events = []
    with fq:
        result = fq.run(CONFIG, name="rabi", preprocess={"shots": 2}, progress=events.append)
    assert capsys.readouterr() == ("", "")
    result.raise_for_status()
    assert (result.points_expected, result.points_completed, result.aborted) == (3, 3, False)
    assert result.sources == ["acq0"]
    assert result.runtimes_ns == [10, 10, 10]
    assert [e.coordinates for e in events] == [{"a": 0.1}, {"a": 0.2}, {"a": 0.3}]
    df = result.dataframe()
    assert list(df.index.names) == ["a", "shot", "time"]
    assert df.groupby(level="a")["value"].mean().tolist() == [1, 2, 3]
    # the same data read back from disk
    assert load_experiment(result.directory).dataframe().equals(df)
    run_cmd = next(h for h in server.received if h.get("cmd") == "config_and_run")
    assert run_cmd["system"] == {"$shots": 2}


def test_keep_in_memory_without_saving(fq, tmp_path):
    with fq:
        result = fq.run(CONFIG, name="mem", save=False, keep_in_memory=True)
    assert result.directory is None
    assert not (tmp_path / "mem").exists()
    assert result.dataframes()["acq0"].groupby(level="a")["value"].mean().tolist() == [1, 2, 3]


def test_preprocess_and_variables_overrides(fq, server):
    with fq:
        fq.run(CONFIG, preprocess={"shots": 7}, variables={"g": {"mode": "list", "values": [1]}})
    run_cmd = next(h for h in server.received if h.get("cmd") == "config_and_run")
    assert run_cmd["system"] == {"$shots": 7}
    assert run_cmd["variables"] == {"g": {"mode": "list", "values": [1]}}


def test_invalid_config_raises_before_sending(fq, server):
    with fq:
        with pytest.raises(ConfigurationError, match="cannot be sent"):
            fq.run({"sys_config": {"$shots": object()}})
        with pytest.raises(ConfigurationError, match="keys must be strings"):
            fq.run({"sys_config": {1: 2}})
        with pytest.raises(ConfigurationError, match="Undefined variable"):
            fq.run({"sys_config": {"$shots": "%missing"}})
    assert not any(h.get("cmd") == "config_and_run" for h in server.received)


def test_rejected_run_raises_and_keeps_the_session(server, fq):
    server.handler = lambda h: (
        [({"type": "error", "msg": "bad sweep"}, b"")] if h.get("cmd") == "config_and_run" else _default_handler(h)
    )
    with fq:
        with pytest.raises(RunRejected, match="bad sweep"):
            fq.run(CONFIG)
        assert fq.ping() >= 0


def test_aborted_run_returns_a_partial_result(server, fq):
    def handler(h):
        if h.get("cmd") == "config_and_run":
            error = ({"type": "error", "msg": "dma"}, b"")
            return [(OK, b""), (_header([1, 2, 3]), b""), *_point(1), error, _footer(True)]
        return _default_handler(h)

    server.handler = handler
    with fq:
        result = fq.run(CONFIG)
    assert (result.aborted, result.points_completed, result.server_errors) == (True, 1, ["dma"])
    assert len(result.dataframe()) == 2
    with pytest.raises(RunFailed, match="dma"):
        result.raise_for_status()


def test_timeout_desynchronizes_until_reconnect(server, fq):
    server.handler = lambda h: [] if h.get("cmd") == "ping" else _default_handler(h)
    fq.command_timeout = 0.2
    fq.connect(verify=False)
    with pytest.raises(FireqTimeout):
        fq.ping()
    with pytest.raises(Desynchronized):
        fq.reset_all()
    server.handler = _default_handler
    fq.reconnect()
    assert server.connections == 2
    fq.reset_all()


def test_interrupt_closes_the_connection_with_the_partial_result(fq, server):
    def interrupt(event):
        if event.point_index == 1:
            raise KeyboardInterrupt

    fq.connect()
    with pytest.raises(KeyboardInterrupt) as info:
        fq.run(CONFIG, progress=interrupt)
    assert info.value.result.points_completed == 2
    assert not fq.connected
    with pytest.raises(ConnectionLost):
        fq.ping()
    fq.reconnect()
    fq.ping()


def test_run_iter_yields_points_and_stops_early(fq):
    fq.connect()
    with fq.run_iter(CONFIG) as points:
        first = next(points)
    assert first.coordinates == {"a": 0.1}
    assert first.data["acq0"]["value"].tolist() == [1, 1]
    assert points.result.points_completed == 1
    assert not fq.connected

    fq.reconnect()
    points = fq.run_iter(CONFIG)
    assert [p.point_index for p in points] == [0, 1, 2]
    assert points.result.points_completed == 3


def test_wrong_token_is_reported_by_connect(server, tmp_path):
    server.handler = lambda h: None if h.get("cmd") == "ping" else []
    client = FireqClient("127.0.0.1", server.port, output_dir=tmp_path)
    with pytest.raises(ConnectionFailed, match="check the token"):
        client.connect()
    assert not client.connected


def test_no_handshake_is_reported_by_connect(tmp_path):
    listener = socket.create_server(("127.0.0.1", 0))
    try:
        client = FireqClient("127.0.0.1", listener.getsockname()[1], connect_timeout=0.3)
        with pytest.raises(ConnectionFailed, match="another client"):
            client.connect()
    finally:
        listener.close()


def test_shell_runs_on_a_connected_client(fq, capsys):
    fq.connect()
    shell = InteractiveShell(fq)
    assert shell.execute("ping")
    assert shell.execute("last")
    assert "No run yet" in capsys.readouterr().out
    shell.last = fq.run(CONFIG, name="exp")
    assert shell.execute("last")
    assert "exp: completed, 3/3 points" in capsys.readouterr().out
    assert not shell.execute("set_nyquist a b c")
    assert "set_nyquist failed" in capsys.readouterr().out


def test_parse_overrides():
    assert parse_overrides(["gain=0.25", "name=x", "n=3"]) == {"gain": 0.25, "name": "x", "n": 3}
    with pytest.raises(ValueError):
        parse_overrides(["gain"])


def test_cli_script(server, tmp_path, capsys):
    from FIREQ_CLIENT.cli import main

    script = tmp_path / "cmds.txt"
    script.write_text("# comment\nping\nreset_all\n")
    argv = ["--host", "127.0.0.1", "--port", str(server.port), "--output-dir", str(tmp_path), "--log-level", "info"]
    assert main([*argv, "--script", str(script)], ask=lambda _: "") == 0
    out = capsys.readouterr().out
    assert "Ping:" in out
    assert "reset_all: ok" in out
    script.write_text("bogus_command_that_fails 1\n")
    assert main([*argv, "--script", str(script)], ask=lambda _: "") == 0
    script.write_text("set_nyquist a b c\nping\n")
    assert main([*argv, "--script", str(script)], ask=lambda _: "") == 1


def test_start_interactive_keeps_the_scripts_connection(fq, monkeypatch, capsys):
    from prompt_toolkit import PromptSession
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    from FIREQ_CLIENT.cli import shell

    with create_pipe_input() as pipe:
        monkeypatch.setattr(shell, "make_prompt_session", lambda: PromptSession(input=pipe, output=DummyOutput()))
        pipe.send_text("ping\nrun_yaml missing.yaml\nquit\n")
        fq.connect()
        fq.start_interactive()
    out = capsys.readouterr().out
    assert "Ping:" in out
    assert "run_yaml failed: ConfigurationError" in out
    # the script opened the connection, the prompt leaves it open
    assert fq.connected
