"""Receive worker thread class."""

import queue
import socket
import struct
import threading

import msgpack

from .protocol import Message


class ReceiveWorker:
    """Reads framed messages from a socket in a background thread."""

    def __init__(self, sock: socket.socket) -> None:
        """Initialize the receiver with the socket and a background thread.

        :param sock: the socket used to receive messages.
        :type sock: socket.socket
        """
        self._sock = sock
        self._queue = queue.Queue()
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._reader_loop, daemon=True)

    def start(self) -> None:
        """Start the background reading thread."""
        self._thread.start()

    def stop(self, timeout: float | None = None) -> None:
        """Stop the reading thread and close the socket.

        :param timeout: how long to wait for the thread to stop.
        :type timeout: float | None
        """
        self._stop_event.set()
        try:
            self._sock.shutdown(0)
        except OSError:
            pass
        self._sock.close()
        self._thread.join(timeout)

    def get_message(self, block: bool = True, timeout: float | None = None) -> Message:
        """Retrieve the next message from the queue.

        :param block: block until a message is available.
        :type block: bool
        :param timeout: how long to wait before raising queue.Empty.
        :type timeout: float | None
        :return: the next message.
        :rtype: Message
        """
        return self._queue.get(block, timeout)

    @property
    def message_queue(self) -> queue.Queue:
        """Return the underlying queue."""
        return self._queue

    def _recv_exactly(self, n: int) -> bytes:
        """Read exactly n bytes from the socket.

        :param n: number of bytes to read.
        :type n: int
        :return: the read bytes.
        :rtype: bytes
        """
        data = b""
        while len(data) < n:
            if self._stop_event.is_set():
                raise ConnectionError("Stopped by user")
            try:
                chunk = self._sock.recv(n - len(data))
            except OSError:
                raise ConnectionError("Socket error") from None
            if not chunk:
                raise ConnectionError("Connection closed")
            data += chunk
        return data

    def _reader_loop(self) -> None:
        """Loop reading framed messages until the worker is stopped."""
        try:
            while not self._stop_event.is_set():
                size_bytes = self._recv_exactly(4)
                msgpack_size = struct.unpack("!I", size_bytes)[0]
                msgpack_bytes = self._recv_exactly(msgpack_size)
                header = msgpack.unpackb(msgpack_bytes, raw=False)
                tsize = header.get("tsize")
                payload = self._recv_exactly(tsize) if tsize is not None else None
                self._queue.put(Message(header=header, payload=payload))
        except (
            ConnectionError,
            OSError,
            struct.error,
            msgpack.exceptions.ExtraData,
            msgpack.exceptions.UnpackException,
        ):
            if not self._stop_event.is_set():
                raise
