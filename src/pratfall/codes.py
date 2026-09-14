from collections.abc import Mapping
from types import MappingProxyType
from typing import Literal

Code = Literal[
    "executable_not_executable",
    "executable_not_found",
    "interrupted",
    "invalid_arguments",
    "invalid_config",
    "native_exit",
    "native_signal",
    "output_encoding",
    "output_io_error",
    "process_io_error",
    "protocol_error",
    "provider_error",
    "stderr_limit_exceeded",
    "stdout_limit_exceeded",
    "timeout",
    "unsupported_agent",
    "unsupported_platform",
]

EXECUTABLE_NOT_EXECUTABLE: Code = "executable_not_executable"
EXECUTABLE_NOT_FOUND: Code = "executable_not_found"
INTERRUPTED: Code = "interrupted"
INVALID_ARGUMENTS: Code = "invalid_arguments"
INVALID_CONFIG: Code = "invalid_config"
NATIVE_EXIT: Code = "native_exit"
NATIVE_SIGNAL: Code = "native_signal"
OUTPUT_ENCODING: Code = "output_encoding"
OUTPUT_IO_ERROR: Code = "output_io_error"
PROCESS_IO_ERROR: Code = "process_io_error"
PROTOCOL_ERROR: Code = "protocol_error"
PROVIDER_ERROR: Code = "provider_error"
STDERR_LIMIT_EXCEEDED: Code = "stderr_limit_exceeded"
STDOUT_LIMIT_EXCEEDED: Code = "stdout_limit_exceeded"
TIMEOUT: Code = "timeout"
UNSUPPORTED_AGENT: Code = "unsupported_agent"
UNSUPPORTED_PLATFORM: Code = "unsupported_platform"

FAILURE_EXIT = 1
USAGE_EXIT = 2
TIMEOUT_EXIT = 124
NOT_EXECUTABLE_EXIT = 126
NOT_FOUND_EXIT = 127
SIGNAL_EXIT_BASE = 128

EXIT_CODES: Mapping[Code, int] = MappingProxyType(
    {
        EXECUTABLE_NOT_EXECUTABLE: NOT_EXECUTABLE_EXIT,
        EXECUTABLE_NOT_FOUND: NOT_FOUND_EXIT,
        INVALID_ARGUMENTS: USAGE_EXIT,
        INVALID_CONFIG: USAGE_EXIT,
        OUTPUT_ENCODING: FAILURE_EXIT,
        OUTPUT_IO_ERROR: FAILURE_EXIT,
        PROCESS_IO_ERROR: FAILURE_EXIT,
        PROTOCOL_ERROR: FAILURE_EXIT,
        PROVIDER_ERROR: FAILURE_EXIT,
        STDERR_LIMIT_EXCEEDED: FAILURE_EXIT,
        STDOUT_LIMIT_EXCEEDED: FAILURE_EXIT,
        TIMEOUT: TIMEOUT_EXIT,
        UNSUPPORTED_AGENT: USAGE_EXIT,
        UNSUPPORTED_PLATFORM: FAILURE_EXIT,
    }
)


def exit_code_for(code: Code) -> int:
    return EXIT_CODES.get(code, FAILURE_EXIT)
