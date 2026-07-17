import socket
import struct
import threading
import queue
from dataclasses import dataclass
from typing import Optional
import socket

import msgpack

from .protocol import Message


# ─── Threaded sender ──────────────────────────────────────────────
class ThreadedSocketSender:
    """Sends framed messages via a socket using a background thread and a queue."""

    def __init__(self, sock: socket.socket):
        self._sock = sock
        self._queue = queue.Queue()
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._sender_loop, daemon=True)

    def start(self):
        self._thread.start()

    def stop(self, timeout: Optional[float] = None):
        self._stop_event.set()
        # Unblock the queue by pushing a sentinel (optional) or close socket
        try:
            self._sock.shutdown(1)  # SHUT_WR
        except OSError:
            pass
        self._sock.close()
        self._thread.join(timeout)

    def send(self, message: Message):
        """Enqueue a message to be sent. The header's 'tsize' key should match the payload length."""
        self._queue.put(message)

    def _sender_loop(self):
        try:
            while not self._stop_event.is_set():
                try:
                    msg = self._queue.get(timeout=0.5)  # periodically check stop flag
                except queue.Empty:
                    continue
                
                # send the message on the socket
                self._sock.sendmsg(msg.to_buffers())

                self._queue.task_done()
        except (OSError, BrokenPipeError):
            if not self._stop_event.is_set():
                raise