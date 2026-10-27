"""Tests of the client against the reply sequences of the server protocol (v0.1.1)."""

import socket

import numpy as np
import pytest
import yaml

from FIREQ_CLIENT import ConfigurationError, FireqClient, ProtocolError, RunRejected, ServerError
from FIREQ_CLIENT.network import Message, ReceiveWorker, SendWorker

OK = {"type": "status", "msg": "ok"}
SWEEP_HEADER = {
    "type": "status",
    "msg": "experiment_header",
    "shots": 2,
    "variable_order": ["a"],
    "variable_values": {"a": [1, 2], "unused": [5, 6, 7]},
}
FORMAT = [["imag", "<i2"], ["real", "<i2"]]
CONFIG = {"sys_config": {"$shots": 2}}


@pytest.fixture
def client(tmp_path):
    sockets = socket.socketpair()
    client = FireqClient("localhost", 0, output_dir=str(tmp_path))
    client.reader = ReceiveWorker(sockets[0])
    client.sender = SendWorker(sockets[1])  # never started: sent commands stay queued
    yield client
    for s in sockets:
        s.close()


def _feed(client, *headers):
    for header in headers:
        data = b""
        if header.get("type") == "dma_package":
            data = np.zeros(header["shots"], dtype=np.dtype([tuple(f) for f in FORMAT])).tobytes()
        client.reader._queue.put(Message(header=header, data=data))


def _dma(shots=2):
    return {"type": "dma_package", "source": "/acq0", "shots": shots, "format": FORMAT}


def _iteration(time_ns):
    return {"type": "status", "msg": "iteration_ended", "time": time_ns}


def _footer(aborted=False):
    return {"type": "status", "msg": "experiment_footer", "run_time": 900, "aborted": aborted}


def _runtimes(tmp_path):
    (run_dir,) = (tmp_path / "exp").iterdir()
    return yaml.safe_load((run_dir / "runtimes.yaml").read_text())


def test_sweep_run(client, tmp_path):
    _feed(client, OK, SWEEP_HEADER, _dma(), _iteration(100), _dma(), _iteration(200), _footer())
    client.run(CONFIG, name="exp")
    assert _runtimes(tmp_path) == {"runtimes": [100, 200], "run_time": 900, "aborted": False}
    (run_dir,) = (tmp_path / "exp").iterdir()
    assert sorted(p.name for p in (run_dir / "acq0").glob("*.pkl")) == ["data_0.pkl", "data_1.pkl"]


def test_single_run_has_no_variables(client, tmp_path):
    header = {"type": "status", "msg": "experiment_header", "shots": 2}
    _feed(client, OK, header, _dma(), _iteration(100), _footer())
    client.run(CONFIG, name="exp")
    assert _runtimes(tmp_path)["runtimes"] == [100]


def test_run_rejected_before_the_header(client, tmp_path):
    _feed(client, {"type": "error", "msg": "Invalid sweep definition: x"}, {"resp": "pong"})
    with pytest.raises(RunRejected, match="Run rejected: Invalid sweep definition: x. Send reset_all"):
        client.run(CONFIG, name="exp")
    # nothing else belongs to the run, the next reply is untouched
    assert client._wait_for_message().header == {"resp": "pong"}
    assert not (tmp_path / "exp").exists()


def test_error_during_the_run_ends_with_an_aborted_footer(client, tmp_path):
    error = {"type": "error", "msg": "error while running experiment: dma"}
    _feed(client, OK, SWEEP_HEADER, _dma(), _iteration(100), error, _footer(aborted=True))
    client.run(CONFIG, name="exp")
    assert _runtimes(tmp_path) == {"runtimes": [100], "run_time": 900, "aborted": True}


def test_warnings_are_skipped(client, tmp_path):
    warning = {"type": "warning", "msg": "careful"}
    _feed(client, warning, OK, warning, SWEEP_HEADER, warning, _iteration(1), _iteration(2), _footer())
    client.run(CONFIG, name="exp")
    assert _runtimes(tmp_path)["runtimes"] == [1, 2]


def test_client_failure_mid_run_skips_to_the_footer(client, tmp_path):
    _feed(client, OK, SWEEP_HEADER, {"type": "bogus"}, _iteration(1), _footer(), {"resp": "pong"})
    with pytest.raises(ProtocolError, match="bogus"):
        client.run(CONFIG, name="exp")
    assert client._wait_for_message().header == {"resp": "pong"}
    (run_dir,) = (tmp_path / "exp").iterdir()
    assert not (run_dir / "runtimes.yaml").exists()


@pytest.mark.parametrize("command", ["mts_sync", "ping", "reset_all"])
def test_commands_read_their_reply(client, command):
    _feed(client, {"type": "error", "msg": "unrecognized command 'mts_sync'"}, {"resp": "pong"})
    with pytest.raises(ServerError, match="unrecognized command"):
        getattr(client, command)()
    assert client._wait_for_message().header == {"resp": "pong"}


def test_set_nyquist_reads_its_reply(client):
    _feed(client, {"type": "error", "msg": "unrecognized command 'set_nyquist'"}, {"resp": "pong"})
    with pytest.raises(ServerError):
        client.set_nyquist(228, 0, 2)
    assert client._wait_for_message().header == {"resp": "pong"}


def test_apply_configuration_reads_the_sweep_warning(client, tmp_path):
    f = tmp_path / "c.yaml"
    f.write_text("sys_config: {$shots: '#n'}\nvariables: {n: {mode: list, values: [1]}}\n")
    _feed(client, OK, {"type": "warning", "msg": "sweep expressions not applied"}, {"resp": "pong"})
    client.apply_configuration(str(f))
    assert client._wait_for_message().header == {"resp": "pong"}


def test_apply_configuration_without_sweep(client, tmp_path):
    f = tmp_path / "c.yaml"
    f.write_text("sys_config: {$shots: 10}\n")
    _feed(client, OK, {"resp": "pong"})
    client.apply_configuration(str(f))
    assert client._wait_for_message().header == {"resp": "pong"}


def test_apply_configuration_error(client, tmp_path):
    f = tmp_path / "c.yaml"
    f.write_text("sys_config: {$shots: 10}\n")
    _feed(client, {"type": "error", "msg": "bad"})
    with pytest.raises(ConfigurationError, match="bad. Send reset_all"):
        client.apply_configuration(str(f))


@pytest.mark.parametrize(
    ("system", "expected"),
    [
        ({"$shots": "#n"}, True),
        ({"/gen": {"pulse": [{"_name": "p", "$gain": "#g"}]}}, True),
        ({"/gen": {"$rfrequency": "#7583.46 + x"}}, True),
        ({"/gen": {"pulse": [{"_name": "#p", "_envelope": "#e"}]}}, False),
        ({"/gen": {"$drive_order": ["#a"]}}, False),
        ({"$shots": 10, "/gen": {"$rfrequency": 5.0}}, False),
    ],
)
def test_sweep_expression_rule_matches_the_server(system, expected):
    assert FireqClient._has_sweep_expressions(system) is expected


def test_run_rejected_after_status_ok(client, tmp_path):
    error = {
        "type": "error",
        "msg": "Message from client tryed to execute a sweep experiment without specifing variables",
    }
    _feed(client, OK, error, {"resp": "pong"})
    with pytest.raises(RunRejected, match="Run rejected: Message from client"):
        client.run(CONFIG, name="exp")
    assert client._wait_for_message().header == {"resp": "pong"}
    assert not (tmp_path / "exp").exists()


@pytest.mark.parametrize("command", ["mts_sync", "set_nyquist"])
def test_rf_commands_read_status_ok(client, command):
    _feed(client, OK, {"resp": "pong"})
    if command == "mts_sync":
        client.mts_sync()
    else:
        client.set_nyquist(228, 0, 2)
    assert client._wait_for_message().header == {"resp": "pong"}
