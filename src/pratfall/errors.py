from pratfall.codes import Code, exit_code_for


class PratError(Exception):
    def __init__(self, message: str, *, code: Code = "invalid_config") -> None:
        super().__init__(message)
        self.code = code
        self.exit_code = exit_code_for(code)
