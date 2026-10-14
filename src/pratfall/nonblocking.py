import errno
import os


def write_nonblocking(descriptor: int, data: bytes) -> int:
    offset = 0
    while offset < len(data):
        try:
            written = os.write(descriptor, data[offset : offset + 4096])
        except BlockingIOError:
            return offset
        except OSError as error:
            if error.errno in {errno.EAGAIN, errno.EWOULDBLOCK}:
                return offset
            raise
        if written == 0:
            raise OSError(errno.EIO, "stderr write returned zero bytes")
        offset += written
    return offset
