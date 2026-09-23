"""Shared Emulator YAML load error across the local parsers."""

class StaticEmulatorConfigLoadError(ValueError):
    """Report fail-closed Emulator configuration loading failure.

    Layer: Emulator configuration-read error. The Emulator YAML loader creates
    it and its file-input callers catch it. It is not a Simulator configuration
    error, entity validation error or runtime state and is public from
    ``skyseed.configuration.emulator``.
    """
