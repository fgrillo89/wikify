"""``wikify corpus serve`` — long-lived HTTP server for the corpus CLI.

See ``tasks/serve_design.md`` for the architecture.

Phase 1 surface:

- :func:`run_server` — start a foreground HTTP server bound to one corpus.
- :func:`maybe_route_to_server` — entry-point hook that checks
  ``WIKIFY_CORPUS_SERVER`` and forwards ``sys.argv`` to a running
  daemon when set, returning the daemon's response. CLI subcommands
  ``serve`` and ``repl`` are always executed locally.
"""

from .client import maybe_route_to_server
from .server import run_server

__all__ = ["maybe_route_to_server", "run_server"]
