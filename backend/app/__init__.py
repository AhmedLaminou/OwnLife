"""OwnLife — a local-first record of one life: its past, its time, its plans."""

import sys

__version__ = "0.2.5"

try:  # the OpenAI client needs it; Smart App Control may refuse its compiled module
    import jiter  # noqa: F401
except ImportError:
    from app import _jiter_shim

    sys.modules["jiter"] = _jiter_shim
