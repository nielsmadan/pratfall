import json
from collections.abc import Iterable
from dataclasses import dataclass, replace
from typing import Protocol

from pratfall.limits import (
    EVENT_BYTES,
    NUMERIC_BYTES,
    RECORD_COUNT,
    RETAINED_STATE_BYTES,
    STDOUT_BYTES,
)
from pratfall.models import Activity, DecodedOutput, ResultError, Usage

CR = ord("\r")
PROTOCOL_SLOT = "protocol"


class ConsumerFailure(Exception):
    def __init__(self, error: ResultError, decoded: DecodedOutput | None = None) -> None:
        super().__init__(error.message)
        self.error = error
        self.decoded = decoded


class ByteConsumer(Protocol):
    def feed(self, data: bytes) -> Activity | None: ...

    def finish(self) -> DecodedOutput: ...


@dataclass(frozen=True)
class ConsumerLimits:
    event_bytes: int = EVENT_BYTES
    state_bytes: int = RETAINED_STATE_BYTES
    records: int = RECORD_COUNT
    numeric_bytes: int = NUMERIC_BYTES


DEFAULT_CONSUMER_LIMITS = ConsumerLimits()


class ConsumerFactory(Protocol):
    def __call__(self, limits: ConsumerLimits = ...) -> ByteConsumer: ...


class CapturingConsumer:
    def __init__(self, consumer: ByteConsumer, limit: int = STDOUT_BYTES) -> None:
        self.consumer = consumer
        self.stdout = bytearray()
        self.limit = limit

    def feed(self, data: bytes) -> Activity | None:
        remaining = self.limit - len(self.stdout)
        captured = data[:remaining]
        self.stdout.extend(captured)
        activity = self.consumer.feed(captured) if captured else None
        if len(data) > remaining:
            raise _limit_failure(f"Agent stdout exceeded {self.limit} bytes.")
        return activity

    def finish(self) -> DecodedOutput:
        return self.consumer.finish()


class StateBudget:
    def __init__(self, limits: ConsumerLimits) -> None:
        self._limits = limits
        self._bytes = 0
        self._records = 0

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

    def numeric_size(self, value: int | float | None) -> int:
        if value is None:
            return 0
        size = len(str(value).encode("ascii"))
        if size > self._limits.numeric_bytes:
            raise _limit_failure(
                f"Agent output numeric value exceeded {self._limits.numeric_bytes} bytes."
            )
        return size


class Retention:
    def __init__(self, budget: StateBudget) -> None:
        self._budget = budget
        self._sizes: dict[str, int] = {}
        self._records: set[str] = set()

    def payload(self, slot: str, size: int, *, record: bool = False) -> None:
        added = int(record and slot not in self._records)
        self._budget.replace_state(self._sizes.get(slot, 0), size, added)
        self._sizes[slot] = size
        if added:
            self._records.add(slot)

    def text(self, slot: str, value: str, *, extra: int = 0, record: bool = False) -> bytes:
        encoded = retained_utf8(value)
        self.payload(slot, len(encoded) + extra, record=record)
        return encoded

    def numbers(
        self,
        slot: str,
        values: Iterable[int | float | None],
        *,
        extra: int = 0,
        record: bool = False,
    ) -> None:
        sizes = sum(self._budget.numeric_size(value) for value in values)
        self.payload(slot, sizes + extra, record=record)

    def usage(
        self, slot: str, value: Usage | None, *, extra: int = 0, record: bool = False
    ) -> None:
        values = () if value is None else _usage_values(value)
        self.numbers(slot, values, extra=extra, record=record)

    def record(self, slot: str) -> None:
        self.payload(slot, self._sizes.get(slot, 0), record=True)

    def release(self, *slots: str) -> None:
        released = 0
        records = 0
        for slot in slots:
            released += self._sizes.pop(slot, 0)
            records += int(slot in self._records)
            self._records.discard(slot)
        self._budget.replace_state(released, 0, -records)

    def commit(self, *slots: str) -> None:
        for slot in slots:
            self._sizes.pop(slot, None)
            self._records.discard(slot)


class JsonlConsumer:
    def __init__(self, name: str, limits: ConsumerLimits = DEFAULT_CONSUMER_LIMITS) -> None:
        self.budget = StateBudget(limits)
        self.retain = Retention(self.budget)
        self.protocol_error: ResultError | None = None
        self._name = name
        self._limits = limits
        self._buffer = bytearray()
        self._start = 0
        self._scan = 0
        self._line_number = 0
        self._finished = False
        self._failed = False
        self._failure: ConsumerFailure | None = None

    def feed(self, data: bytes) -> Activity | None:
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

    def _feed(self, data: bytes) -> Activity | None:
        self._buffer.extend(data)
        activity: Activity | None = None
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

    def apply(self, event: dict[str, object]) -> Activity | None:
        raise NotImplementedError

    def result(self) -> DecodedOutput:
        raise NotImplementedError

    def _consume(self, record: bytes) -> Activity | None:
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
            event = self.parse_record(line)
        except UnicodeEncodeError as error:
            raise ConsumerFailure(
                ResultError("output_encoding", "Agent output contains invalid Unicode text.")
            ) from error
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

    def parse_record(self, line: str) -> object:
        return json.loads(line, parse_int=self._parse_int, parse_float=self._parse_float)

    @property
    def type_field(self) -> str:
        return "type"

    def malformed(self, message: str) -> None:
        if self.protocol_error is None:
            self.retain.text(PROTOCOL_SLOT, message)
            self.protocol_error = ResultError("protocol_error", message)

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


def decode_with(factory: ConsumerFactory, stdout: str) -> DecodedOutput:
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
        return replace(decoded, error=failure.error)


def retained_utf8(value: str) -> bytes:
    try:
        return value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise ConsumerFailure(
            ResultError("output_encoding", "Agent output contains invalid Unicode text.")
        ) from error


def _usage_values(usage: Usage) -> tuple[int | None, ...]:
    return (
        usage.input_tokens,
        usage.cached_input_tokens,
        usage.cache_write_input_tokens,
        usage.output_tokens,
        usage.reasoning_output_tokens,
    )


def _limit_failure(message: str) -> ConsumerFailure:
    return ConsumerFailure(ResultError("stdout_limit_exceeded", message))
