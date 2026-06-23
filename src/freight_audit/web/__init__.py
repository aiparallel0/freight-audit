"""Browser-facing routes: marketing landing, signup, self-serve demo, review console.

Imported by api.py after the app + auth dependencies are defined, so the routes and
the static mount attach to the same FastAPI app.
"""
from . import pages  # noqa: F401  (registers routes on import)
