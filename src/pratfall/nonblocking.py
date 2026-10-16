import errno
import os
import select


def write_nonblocking(descriptor: int, data: bytes) -> int:
    offset = 0
    while offset < len(data):
        try:
            if not _writable(descriptor):
                return offset
            written = os.write(descriptor, data[offset : offset + select.PIPE_BUF])
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


def _writable(descriptor: int) -> bool:
    try:
        return bool(select.select([], [descriptor], [], 0)[1])
    except ValueError as error:
        raise OSError(errno.EBADF, str(error)) from error
