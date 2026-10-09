from dataclasses import dataclass


@dataclass
class ExternalCallCounter:
    """Counts real HTTP calls made while serving one request (cache hits excluded).

    Returned in the response `meta`, so the "one routing call" design is
    visible rather than just claimed.
    """

    routing: int = 0
    geocoding: int = 0

    @property
    def total(self) -> int:
        return self.routing + self.geocoding
