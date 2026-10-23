"""Threaded sender worker."""

import logging
import socket
from queue import Empty, Queue
from threading import Event, Thread, current_thread

from .protocol import Message


# ─── Threaded sender ──────────────────────────────────────────────
class SendWorker:
    """Sends framed messages via a socket using a background thread and a queue."""

    def __init__(self, sock: socket.socket, logger: logging.Logger | None = None) -> None:
        """Initialize the sender with the socket and a background thread.

        :param sock: the socket used to send messages.
        :type sock: socket.socket
        :param logger: logger used by the worker, defaults to the module logger.
        :type logger: logging.Logger | None
        """
        self._sock = sock
        self._queue = Queue()
        self.log = logger or logging.getLogger(__name__)
        self._stop_event = Event()
        #: set when the worker ended because of a send error rather than a stop request
        self.failed = Event()
        self._thread = Thread(target=self._sender_loop, daemon=True)
        # flag to know if the socket object has the sendmsg method
        self._has_sendmsg = hasattr(self._sock, "sendmsg")

    def start(self) -> None:
        """Start the background sending thread."""
        self._thread.start()
        self.log.debug("Thread started")

    def stop(self, timeout: float | None = None) -> None:
        """Stop the sending thread (the socket is closed by the owner of the socket).

        :param timeout: how long to wait for the thread to stop.
        :type timeout: float | None
        """
        self._stop_event.set()
        # the worker calls this on its way out, and a thread cannot join itself
        if self._thread.is_alive() and current_thread() is not self._thread:
            self._thread.join(timeout)

    def send(self, message: Message) -> None:
        """Enqueue a message to be sent by the background thread.

        :param message: the message to enqueue.
        :type message: Message
        """
        self._queue.put(message)

    def _send_message(self, msg: Message) -> None:
        """Write one message on the socket.

        :param msg: the message to send.
        :type msg: Message
        """
        if self._has_sendmsg:
            # sendmsg may do a partial send, so loop until everything is out
            buffers = [memoryview(b) for b in msg.to_buffers()]
            while buffers:
                sent = self._sock.sendmsg(buffers)
                while sent and buffers:
                    if sent >= len(buffers[0]):
                        sent -= len(buffers[0])
                        buffers.pop(0)
                    else:
                        buffers[0] = buffers[0][sent:]
                        sent = 0
        else:
            # Windows and other platforms without sendmsg
            self._sock.sendall(b"".join(msg.to_buffers()))

    def _sender_loop(self) -> None:
        """Loop sending queued messages until the worker is stopped."""
        while not self._stop_event.is_set():
            try:
                msg = self._queue.get(timeout=0.5)  # periodically check stop flag
            except Empty:
                continue
            try:
                self._send_message(msg)
            except Exception:
                # a lost connection must be visible to the waiting client
                self.log.exception("Send failed, shutting down the send worker")
                self.failed.set()
                break
            # only a message actually sent counts as done
            self._queue.task_done()
        # stop if the stop event is not set
        if not self._stop_event.is_set():
            self.stop()
