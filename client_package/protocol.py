from dataclasses import dataclass
from typing import Optional
import copy
import msgpack
import struct

# ─── Dataclass for a parsed message ───────────────────────────────

@dataclass
class Message:
    """Simple message type, will be sent over the socket as a json.

    :param type: Message type identifier ("experiment_header" or "sweep_header").
    :type type: str
    :param metadata: Full metadata dict to serialize as JSON.
    :type metadata: dict
    """

    header: dict
    payload: Optional[bytes] = None

    def to_buffers(self) -> tuple:
        nheader = copy.deepcopy(self.header)
        if self.payload:
            nheader["tdata"] = len(self.payload)
        header_bytes = msgpack.packb(nheader)
        header_size_bytes = struct.pack(">I", len(header_bytes))  # 4 bytes, network byte order
        if self.payload:
            return (header_size_bytes, header_bytes, self.payload)
        return (header_size_bytes, header_bytes)