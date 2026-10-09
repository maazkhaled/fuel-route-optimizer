"""WSGI config. Also warms the in-memory station and place indexes at start-up
so the first request doesn't pay ~0.2 s to load them."""

import logging
import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

application = get_wsgi_application()


def _warm_up() -> None:
    from django.db.utils import DatabaseError

    from planner.services.places import PlaceIndex
    from planner.services.stations import StationIndex

    try:
        PlaceIndex.get()
        StationIndex.get()
    except (DatabaseError, RuntimeError) as exc:  # e.g. `migrate` not run yet
        logging.getLogger("planner").warning("Skipping warm-up: %s", exc)


_warm_up()
