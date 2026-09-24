from dataclasses import dataclass
from typing import Protocol


class DiscoveryError(Exception):
    """Expected failure; messages must never contain API credentials."""


class BudgetExceeded(DiscoveryError):
    pass


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str = ""


@dataclass(frozen=True)
class SearchPage:
    results: list[SearchResult]
    next_start: int | None


@dataclass(frozen=True)
class Resolution:
    status: str
    name: str = ""
    url: str = ""
    reason: str = ""
    evidence: str = ""


class SearchProvider(Protocol):
    def search(self, query: str, start: int) -> dict: ...
    def parse(self, payload: dict, start: int) -> SearchPage: ...


class EntrypointResolver(Protocol):
    def resolve(self, result: SearchResult) -> Resolution: ...
