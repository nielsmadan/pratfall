class PratError(Exception):
    def __init__(self, message: str, *, code: str = "invalid_config", exit_code: int = 2) -> None:
        super().__init__(message)
        self.code = code
        self.exit_code = exit_code
