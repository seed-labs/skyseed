"""Narrow optional Model-resource cleanup shared by the existing owners."""


def _shutdown_owned(components, *, wait=True):
    if not isinstance(wait, bool):
        raise TypeError("wait must be a bool")
    failure = None
    for component in components:
        try:
            close = getattr(component, "shutdown", None)
            if close is not None:
                close(wait=wait)
        except BaseException as error:
            if failure is None:
                failure = error
            elif error is not failure:
                failure.add_note(f"additional shutdown failure ({type(component).__name__}): {type(error).__name__}: {error}")
                for note in getattr(error, "__notes__", ()):
                    failure.add_note(note)
    if failure is not None:
        raise failure
