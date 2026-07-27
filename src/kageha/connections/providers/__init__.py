"""Built-in connection providers."""

from __future__ import annotations

from kageha.connections.providers.gcal import GoogleCalendarProvider
from kageha.connections.providers.gdrive import GoogleDriveProvider
from kageha.connections.providers.github import GitHubProvider
from kageha.connections.providers.gmail import GmailProvider

BUILTIN_PROVIDERS = (
    GmailProvider,
    GoogleCalendarProvider,
    GoogleDriveProvider,
    GitHubProvider,
)

__all__ = [
    "BUILTIN_PROVIDERS",
    "GmailProvider",
    "GoogleCalendarProvider",
    "GoogleDriveProvider",
    "GitHubProvider",
]
