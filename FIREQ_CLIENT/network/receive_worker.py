"""Receive worker thread class."""

import logging
import socket
import struct
from queue import Queue
from threading import Event, Thread, current_thread

import msgpack

from .protocol import Message


class ReceiveWorker:
    """Reads framed messages from a socket in a background thread."""

    def __init__(self, sock: socket.socket, logger: logging.Logger = None) -> None:
        """Initialize the receiver with the socket and a background thread.

        :param sock: the socket used to receive messages.
        :type sock: socket.socket
        """
        self._sock = sock
        self._queue = Queue()
        self.log = logger or logging.getLogger(__name__)
        self._stop_event = Event()

    def start(self) -> None:
        """Start the receive worker thread."""
        self._thread = Thread(target=self._run, daemon=True)
        self._thread.start()
        self.log.debug("Thread started")

    def stop(self, timeout: float | None = None) -> None:
        """Stop the reading thread and close the socket.

        :param timeout: how long to wait for the thread to stop.
        :type timeout: float | None
        """
        self._stop_event.set()
        # the worker calls this on its way out, and a thread cannot join itself
        if current_thread() is not self._thread:
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
    def message_queue(self) -> Queue:
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
            except TimeoutError:
                continue
            except OSError as e:
                raise ConnectionError("Socket error") from e
            if not chunk:
                raise ConnectionError("Connection closed")
            data += chunk
        return data

    def _run(self) -> None:
        """Loop reading framed messages until the worker is stopped."""
        while not self._stop_event.is_set():
            try:
                # read the first 4 bytes which define the length of the header
                size_bytes = self._recv_exactly(4)
                header_size = struct.unpack("!I", size_bytes)[0]
                # receive the header
                header_bytes = self._recv_exactly(header_size)
                header = msgpack.unpackb(header_bytes, raw=False)
                # get the size of the traling data and receive it if needed
                tsize = header.get("tsize")
                data = self._recv_exactly(tsize) if tsize is not None else b""
                self._queue.put(Message(header=header, data=data))
            except TimeoutError:
                continue
            except ConnectionError:
                break
            except Exception as e:
                self.log.exception(f"Caught exception {e} in receive worker, shutting down")
                break
        if not self._stop_event.is_set():
            self.stop()
