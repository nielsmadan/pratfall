import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from pratfall.limits import EVENT_BYTES, NUMERIC_BYTES, RECORD_COUNT, RETAINED_STATE_BYTES
from pratfall.models import DecodedOutput, ResultError

CR = ord("\r")


class ConsumerFailure(Exception):
    def __init__(self, error: ResultError, decoded: DecodedOutput | None = None) -> None:
        super().__init__(error.message)
        self.error = error
        self.decoded = decoded


class ByteConsumer(Protocol):
    def feed(self, data: bytes) -> str | None: ...

    def finish(self) -> DecodedOutput: ...


@dataclass(frozen=True)
class ConsumerLimits:
    event_bytes: int = EVENT_BYTES
    state_bytes: int = RETAINED_STATE_BYTES
    records: int = RECORD_COUNT
    numeric_bytes: int = NUMERIC_BYTES


DEFAULT_CONSUMER_LIMITS = ConsumerLimits()


class StateBudget:
    def __init__(self, limits: ConsumerLimits) -> None:
        self._limits = limits
        self._bytes = 0
        self._records = 0

    def replace_bytes(self, old: int, new: int) -> None:
        updated = self._bytes - old + new
        if updated > self._limits.state_bytes:
            raise _limit_failure(
                f"Agent retained output state exceeded {self._limits.state_bytes} bytes."
            )
        self._bytes = updated

    def replace_state(self, old_bytes: int, new_bytes: int, added_records: int) -> None:
        updated_bytes = self._bytes - old_bytes + new_bytes
        updated_records = self._records + added_records
        if updated_records > self._limits.records:
            raise _limit_failure(f"Agent retained output records exceeded {self._limits.records}.")
        if updated_bytes > self._limits.state_bytes:
            raise _limit_failure(
                f"Agent retained output state exceeded {self._limits.state_bytes} bytes."
            )
        self._bytes = updated_bytes
        self._records = updated_records

    def add_string(self, value: str) -> int:
        try:
            size = len(value.encode("utf-8"))
        except UnicodeEncodeError as error:
            raise ConsumerFailure(
                ResultError("output_encoding", "Agent output contains invalid Unicode text.")
            ) from error
        self.replace_bytes(0, size)
        return size

    def add_record(self) -> None:
        if self._records >= self._limits.records:
            raise _limit_failure(f"Agent retained output records exceeded {self._limits.records}.")
        self._records += 1

    def remove_record(self) -> None:
        self._records -= 1

    def numeric_size(self, value: int | float | None) -> int:
        if value is None:
            return 0
        size = len(str(value).encode("ascii"))
        if size > self._limits.numeric_bytes:
            raise _limit_failure(
                f"Agent output numeric value exceeded {self._limits.numeric_bytes} bytes."
            )
        return size


class JsonlConsumer:
    def __init__(self, name: str, limits: ConsumerLimits = DEFAULT_CONSUMER_LIMITS) -> None:
        self.budget = StateBudget(limits)
        self._name = name
        self._limits = limits
        self._buffer = bytearray()
        self._start = 0
        self._scan = 0
        self._line_number = 0
        self._finished = False
        self._failed = False
        self._failure: ConsumerFailure | None = None

    def feed(self, data: bytes) -> str | None:
        if self._finished:
            raise RuntimeError("consumer already finished")
        if self._failed:
            return None
        try:
            return self._feed(data)
        except ConsumerFailure as failure:
            self._failed = True
            self._failure = failure
            raise

    def _feed(self, data: bytes) -> str | None:
        self._buffer.extend(data)
        activity: str | None = None
        while True:
            newline = self._buffer.find(b"\n", self._scan)
            if newline < 0:
                self._scan = len(self._buffer)
                self._check_unfinished_limit()
                self._compact()
                return activity
            record_end = (
                newline - 1
                if newline > self._start and self._buffer[newline - 1] == CR
                else newline
            )
            record = bytes(self._buffer[self._start : record_end])
            self._start = newline + 1
            self._scan = self._start
            current = self._consume(record)
            if current is not None:
                activity = current

    def finish(self) -> DecodedOutput:
        if self._finished:
            raise RuntimeError("consumer already finished")
        self._finished = True
        if self._failure is not None:
            self._buffer.clear()
            raise ConsumerFailure(self._failure.error, self.result()) from self._failure
        if not self._failed and self._start < len(self._buffer):
            record = bytes(self._buffer[self._start :])
            self._start = len(self._buffer)
            try:
                self._consume(record)
            except ConsumerFailure as failure:
                self._failed = True
                self._failure = failure
                self._buffer.clear()
                raise ConsumerFailure(failure.error, self.result()) from failure
        self._buffer.clear()
        return self.result()

    def apply(self, event: dict[str, object]) -> str | None:
        raise NotImplementedError

    def result(self) -> DecodedOutput:
        raise NotImplementedError

    def _consume(self, record: bytes) -> str | None:
        self._line_number += 1
        if len(record) > self._limits.event_bytes:
            raise _limit_failure(f"Agent output event exceeded {self._limits.event_bytes} bytes.")
        try:
            line = record.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ConsumerFailure(
                ResultError("output_encoding", "Agent stdout is not valid UTF-8.")
            ) from error
        if not line.strip():
            return None
        try:
            event = json.loads(
                line,
                parse_int=self._parse_int,
                parse_float=self._parse_float,
            )
        except json.JSONDecodeError as error:
            self.malformed(f"Invalid {self._name} JSONL on line {self._line_number}: {error.msg}.")
            return None
        except (ValueError, RecursionError):
            self.malformed(
                f"Invalid {self._name} JSONL on line {self._line_number}: "
                "numeric value is too large or nesting is excessive."
            )
            return None
        if not isinstance(event, dict) or not isinstance(event.get(self.type_field), str):
            self.malformed(f"Malformed {self._name} event on line {self._line_number}.")
            return None
        return self.apply(event)

    @property
    def type_field(self) -> str:
        return "type"

    def malformed(self, message: str) -> None:
        raise NotImplementedError

    @property
    def failed(self) -> bool:
        return self._failed

    def _parse_int(self, value: str) -> int:
        self._check_numeric(value)
        return int(value)

    def _parse_float(self, value: str) -> float:
        self._check_numeric(value)
        return float(value)

    def _check_numeric(self, value: str) -> None:
        if len(value.encode("ascii")) > self._limits.numeric_bytes:
            raise ValueError("numeric value is too large")

    def _check_unfinished_limit(self) -> None:
        size = len(self._buffer) - self._start
        if size > self._limits.event_bytes + 1:
            raise _limit_failure(f"Agent output event exceeded {self._limits.event_bytes} bytes.")
        if size == self._limits.event_bytes + 1 and self._buffer[-1] != CR:
            raise _limit_failure(f"Agent output event exceeded {self._limits.event_bytes} bytes.")

    def _compact(self) -> None:
        if self._start and (self._start >= 64 * 1024 or self._start == len(self._buffer)):
            removed = self._start
            del self._buffer[: self._start]
            self._start = 0
            self._scan -= removed


def decode_with(factory: Callable[[], ByteConsumer], stdout: str) -> DecodedOutput:
    consumer = factory()
    try:
        encoded = stdout.encode("utf-8")
        for start in range(0, len(encoded), 64 * 1024):
            consumer.feed(encoded[start : start + 64 * 1024])
        return consumer.finish()
    except UnicodeEncodeError:
        return DecodedOutput(
            error=ResultError("output_encoding", "Agent output contains invalid Unicode text.")
        )
    except ConsumerFailure as failure:
        decoded = failure.decoded
        if decoded is None:
            try:
                decoded = consumer.finish()
            except ConsumerFailure as finish_failure:
                decoded = finish_failure.decoded or DecodedOutput()
        return DecodedOutput(
            output=decoded.output,
            usage=decoded.usage,
            error=failure.error,
            timed_out=decoded.timed_out,
            reported_models=decoded.reported_models,
            cost_usd=decoded.cost_usd,
        )


def retained_utf8(value: str) -> bytes:
    try:
        return value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise ConsumerFailure(
            ResultError("output_encoding", "Agent output contains invalid Unicode text.")
        ) from error


def _limit_failure(message: str) -> ConsumerFailure:
    return ConsumerFailure(ResultError("stdout_limit_exceeded", message))
