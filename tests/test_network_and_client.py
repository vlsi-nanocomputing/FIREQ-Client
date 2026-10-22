"""Tests for the network workers and the connection handling of the client."""

import socket
import time

import pytest

from FIREQ_CLIENT.client import Client
from FIREQ_CLIENT.network import Message, ReceiveWorker, SendWorker
from FIREQ_CLIENT.yaml_preprocessor import load_and_resolve


def _wait(condition, timeout=3.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture
def pair():
    a, b = socket.socketpair()
    a.settimeout(1.0)
    b.settimeout(1.0)
    yield a, b
    a.close()
    b.close()


def test_message_roundtrip(pair):
    a, b = pair
    sender, reader = SendWorker(a), ReceiveWorker(b)
    sender.start()
    reader.start()
    sender.send(Message(header={"cmd": "x"}, data=b"payload"))
    msg = reader.get_message(timeout=3)
    assert msg.header["cmd"] == "x"
    assert msg.data == b"payload"
    sender.stop()
    reader.stop()


def test_send_without_sendmsg(pair):
    """The Windows branch (no ``sendmsg``) must send the message."""
    a, b = pair
    sender, reader = SendWorker(a), ReceiveWorker(b)
    sender._has_sendmsg = False
    sender.start()
    reader.start()
    sender.send(Message(header={"cmd": "y"}, data=b"abc"))
    assert reader.get_message(timeout=3).data == b"abc"
    assert not sender.failed.is_set()
    sender.stop()
    reader.stop()


def test_stop_before_start_does_not_raise(pair):
    a, _ = pair
    ReceiveWorker(a).stop()
    SendWorker(a).stop()


def test_reader_flags_closed_connection(pair):
    a, b = pair
    reader = ReceiveWorker(b)
    reader.start()
    a.close()
    assert _wait(reader.failed.is_set)


def test_sender_flags_send_error(pair):
    a, b = pair
    sender = SendWorker(a)
    sender.start()
    b.close()
    a.close()
    sender.send(Message(header={"cmd": "x"}))
    assert _wait(sender.failed.is_set)


def test_wait_for_message_raises_on_lost_connection(pair):
    a, b = pair
    client = Client("localhost", 0)
    client.reader = ReceiveWorker(b)
    client.sender = SendWorker(b)
    client.reader.start()
    a.close()
    with pytest.raises(ConnectionError):
        client._wait_for_message()


def test_wait_for_message_serves_queued_messages_before_failing(pair):
    a, b = pair
    client = Client("localhost", 0)
    client.reader = ReceiveWorker(b)
    client.reader.start()
    a.sendall(b"".join(Message(header={"msg": "last"}).to_buffers()))
    a.close()
    assert _wait(client.reader.failed.is_set)
    assert client._wait_for_message().header["msg"] == "last"
    with pytest.raises(ConnectionError):
        client._wait_for_message()


def test_disconnect_without_connection():
    Client("localhost", 0)._disconnect()


def test_drain_queue(pair):
    _, b = pair
    client = Client("localhost", 0)
    client.reader = ReceiveWorker(b)
    client.reader._queue.put(Message(header={"stale": True}))
    client._drain_queue()
    assert client.reader._queue.empty()


def test_config_requires_sys_config(tmp_path):
    f = tmp_path / "no_sys.yaml"
    f.write_text("variables: {}\n")
    with pytest.raises(ValueError, match="sys_config"):
        Client._load_config(str(f))
    empty = tmp_path / "empty.yaml"
    empty.write_text("")
    with pytest.raises(ValueError):
        Client._load_config(str(empty))


def test_config_resolves_placeholders(tmp_path):
    f = tmp_path / "ok.yaml"
    f.write_text("preprocess: {a: 3}\nsys_config: {x: '%a'}\nvariables: {}\n")
    assert load_and_resolve(str(f))["sys_config"] == {"x": 3}
    assert Client._load_config(str(f))["sys_config"] == {"x": 3}


def test_disconnect_is_fast_and_not_reported_as_lost(pair):
    from FIREQ_CLIENT.client import SOCKET_TIMEOUT

    a, b = pair
    b.settimeout(SOCKET_TIMEOUT)
    client = Client("localhost", 0)
    client.sock = b
    client.reader, client.sender = ReceiveWorker(b), SendWorker(b)
    client.reader.start()
    client.sender.start()
    time.sleep(0.1)
    start = time.time()
    client._disconnect()
    assert time.time() - start < 1.0
    assert not client.reader.failed.is_set()
