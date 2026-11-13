"""Known, pure stage-action validation failures; never wrap execution errors."""


class ActionRejected(ValueError):
    def __init__(self, code, message, field_path='action'):
        super().__init__(message)
        self.code = code
        self.message = message
        self.field_path = field_path


def require_action(condition, code, message, field_path='action'):
    if not condition:
        raise ActionRejected(code, message, field_path)
