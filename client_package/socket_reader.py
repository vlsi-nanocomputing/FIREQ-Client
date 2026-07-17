import socket
import struct
import threading
import queue
from dataclasses import dataclass
from typing import Optional

import msgpack

from .protocol import Message

# ─── Threaded receiver (same as before) ──────────────────────────
class ThreadedSocketReader:
    """Reads framed messages from a socket in a background thread."""

    def __init__(self, sock):
        self._sock = sock
        self._queue = queue.Queue()
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._reader_loop, daemon=True)

    def start(self):
        self._thread.start()

    def stop(self, timeout: Optional[float] = None):
        self._stop_event.set()
        try:
            self._sock.shutdown(0)
        except OSError:
            pass
        self._sock.close()
        self._thread.join(timeout)

    def get_message(self, block=True, timeout=None) -> Message:
        return self._queue.get(block, timeout)

    @property
    def message_queue(self):
        return self._queue

    def _recv_exactly(self, n: int) -> bytes:
        data = b''
        while len(data) < n:
            if self._stop_event.is_set():
                raise ConnectionError("Stopped by user")
            try:
                chunk = self._sock.recv(n - len(data))
            except OSError:
                raise ConnectionError("Socket error")
            if not chunk:
                raise ConnectionError("Connection closed")
            data += chunk
        return data

    def _reader_loop(self):
        try:
            while not self._stop_event.is_set():
                size_bytes = self._recv_exactly(4)
                msgpack_size = struct.unpack('!I', size_bytes)[0]
                msgpack_bytes = self._recv_exactly(msgpack_size)
                header = msgpack.unpackb(msgpack_bytes, raw=False)
                tsize = header.get('tsize')
                payload = self._recv_exactly(tsize) if tsize is not None else None
                self._queue.put(Message(header=header, payload=payload))
        except (ConnectionError, OSError, struct.error, msgpack.exceptions.ExtraData,
                msgpack.exceptions.UnpackException):
            if not self._stop_event.is_set():
                raise