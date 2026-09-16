"""Threaded sender worker."""

import logging
import socket
from queue import Empty, Queue
from threading import Event, Thread, current_thread

from .protocol import Message


# ─── Threaded sender ──────────────────────────────────────────────
class SendWorker:
    """Sends framed messages via a socket using a background thread and a queue."""

    def __init__(self, sock: socket.socket, logger: logging.Logger = None) -> None:
        """Initialize the sender with the socket and a background thread.

        :param sock: the socket used to send messages.
        :type sock: socket.socket
        """
        self._sock = sock
        self._queue = Queue()
        self.log = logger or logging.getLogger(__name__)
        self._stop_event = Event()
        self._thread = Thread(target=self._sender_loop, daemon=True)

    def start(self) -> None:
        """Start the background sending thread."""
        self._thread.start()
        self.log.debug("Thread started")

    def stop(self, timeout: float | None = None) -> None:
        """Stop the sending thread and close the socket.

        :param timeout: how long to wait for the thread to stop.
        :type timeout: float | None
        """
        self._stop_event.set()
        # the worker calls this on its way out, and a thread cannot join itself
        if current_thread() is not self._thread:
            self._thread.join(timeout)

    def send(self, message: Message) -> None:
        """Enqueue a message to be sent by the background thread.

        :param message: the message to enqueue.
        :type message: Message
        """
        self._queue.put(message)

    def _sender_loop(self) -> None:
        """Loop sending queued messages until the worker is stopped."""
        while not self._stop_event.is_set():
            try:
                msg = self._queue.get(timeout=0.5)  # periodically check stop flag
            except Empty:
                continue

            # send the message on the socket
            try:
                self._sock.sendmsg(msg.to_buffers())
            except Exception as e:
                self.log.exception(f"Caught an exception while sending packet: {e}")
                break
            self._queue.task_done()
        # stop if the stop event is not set
        if not self._stop_event.is_set():
            self.stop()
