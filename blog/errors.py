class BlogError(Exception):
    """A user-facing error: printed as `error: ...`, exit code 1."""

    def __init__(self, message, hint=None, code=1):
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.code = code
