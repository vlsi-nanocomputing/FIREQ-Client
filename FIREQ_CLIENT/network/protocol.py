"""Network protocol specification, with dataclasses that automatically handle network serialization."""

import copy
import struct
from dataclasses import dataclass

import msgpack


@dataclass
class Message:
    """Simple message type, will be sent over the socket as a json.

    :param type: Message type identifier ("experiment_header" or "sweep_header").
    :type type: str
    :param metadata: Full metadata dict to serialize as JSON.
    :type metadata: dict
    """

    header: dict
    data: bytes = b""

    def to_buffers(self) -> tuple:
        """
        Turn the message into a tuple of bytes ready to be sent over the network.

        The tuple always starts with a 4 byte length item that defines the length of the header, followed by
        the serialized header (using messagepack) and optionally the data bytes.

        :return: Series of byte objects
        :rtype: tuple
        """
        # copy header and insert data length
        nheader = copy.deepcopy(self.header)
        if self.data:
            nheader["tdata"] = len(self.data)
        # pack the header and size
        header_bytes = msgpack.packb(nheader)
        header_size_bytes = struct.pack(">I", len(header_bytes))  # 4 bytes, network byte order
        # return the tuple of bytes
        if self.data:
            return (header_size_bytes, header_bytes, self.data)
        return (header_size_bytes, header_bytes)
