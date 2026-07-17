import struct
import threading
import queue
from dataclasses import dataclass
from typing import Optional

import msgpack

# ─── Dataclass for a parsed message ───────────────────────────────
@dataclass
class Message:
    """
    Represents one complete message received from the socket.
    """
    header: dict            # Unpacked MessagePack dictionary
    payload: Optional[bytes] = None  # Raw bytes if 'tsize' was present; otherwise None


# ─── Threaded socket reader ───────────────────────────────────────
class ThreadedSocketReader:
    """
    Reads framed messages from a connected socket in a background thread
    and makes them available through a thread-safe queue.

    Protocol (same as before):
        [4 bytes: uint32 big-endian size of msgpack header]
        [msgpack bytes]
        [optional 'tsize' raw bytes]
    """

    def __init__(self, sock):
        """
        Args:
            sock: connected socket (must support recv).
        """
        self._sock = sock
        self._queue = queue.Queue()
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._reader_loop, daemon=True)

    def start(self):
        """Start the background reader thread."""
        self._thread.start()

    def stop(self, timeout: Optional[float] = None):
        """
        Signal the thread to stop and wait for it to finish.
        Closes the underlying socket to unblock a recv() call.
        """
        self._stop_event.set()
        try:
            self._sock.shutdown(0)   # socket.SHUT_RD on Python < 3.13
        except OSError:
            pass
        self._sock.close()
        self._thread.join(timeout)

    def get_message(self, block=True, timeout=None) -> Message:
        """
        Retrieve the next message from the queue.

        Returns:
            A Message instance.
        Raises:
            queue.Empty if timeout expires and no message is available.
        """
        return self._queue.get(block, timeout)

    @property
    def message_queue(self):
        """Access the underlying queue (e.g., for non-blocking checks)."""
        return self._queue

    # ─── Private helpers ───────────────────────────────────────
    def _recv_exactly(self, n: int) -> bytes:
        """Read exactly n bytes from the socket."""
        data = b''
        while len(data) < n:
            # Check stop flag on each iteration to avoid hanging forever
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
        """Main loop running in the background thread."""
        try:
            while not self._stop_event.is_set():
                # 1. Read 4‑byte size (big‑endian)
                size_bytes = self._recv_exactly(4)
                msgpack_size = struct.unpack('!I', size_bytes)[0]

                # 2. Read the msgpack header
                msgpack_bytes = self._recv_exactly(msgpack_size)
                header = msgpack.unpackb(msgpack_bytes, raw=False)

                # 3. Optional trailing payload
                tsize = header.get('tsize')
                payload = self._recv_exactly(tsize) if tsize is not None else None

                # Put the complete message onto the queue
                self._queue.put(Message(header=header, payload=payload))

        except (ConnectionError, OSError, struct.error, msgpack.exceptions.ExtraData,
                msgpack.exceptions.UnpackException) as e:
            # If we're supposed to stop, just exit quietly; otherwise forward the error.
            if not self._stop_event.is_set():
                raise
        finally:
            # Ensure a clean shutdown: close socket and unblock queue consumers
            try:
                self._sock.close()
            except OSError:
                pass