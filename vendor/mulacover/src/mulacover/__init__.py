"""MuLaCover: controllable cover-song generation from audio or MIDI."""

from importlib import import_module

# Audio transcription must work without loading codec/generation dependencies.
def __getattr__(name):
    modules = {'MuLaCoverConfig': '.configuration', 'MuLaCover': '.modeling',
               'MuLaCoverGenConfig': '.pipeline', 'MuLaCoverGenPipeline': '.pipeline'}
    if name not in modules:
        raise AttributeError(name)
    value = getattr(import_module(modules[name], __name__), name)
    globals()[name] = value
    return value

__all__ = [
    "MuLaCover",
    "MuLaCoverConfig",
    "MuLaCoverGenConfig",
    "MuLaCoverGenPipeline",
]

__version__ = "0.1.0"
