from __future__ import annotations
from syftbox import __version__

from packaging import version
from pathlib import Path
from typing import TYPE_CHECKING, Optional

import httpx
from typing_extensions import Protocol

from syftbox.client.exceptions import SyftAuthenticationError, SyftPermissionError, SyftServerError, SyftServerTooOld
from syftbox.lib.client_config import SyftClientConfig
from syftbox.lib.http import HEADER_SYFTBOX_USER, HEADER_SYFTBOX_VERSION, SYFTBOX_HEADERS
from syftbox.lib.version_utils import get_range_for_version
from syftbox.lib.workspace import SyftWorkspace


class PluginManagerInterface(Protocol):
    """All initialized plugins."""

    if TYPE_CHECKING:
        from syftbox.client.plugins.apps import AppRunner
        from syftbox.client.plugins.sync.manager import SyncManager

    @property
    def sync_manager(self) -> SyncManager:
        """SyncManager instance for managing synchronization tasks."""
        ...

    @property
    def app_runner(self) -> AppRunner:
        """AppRunner instance for managing application execution."""
        ...


class SyftBoxContextInterface(Protocol):
    """
    Protocol defining the essential attributes required by SyftClient plugins/components.

    This interface serves two main purposes:
    1. Prevents circular dependencies by providing a minimal interface that
       plugins/components can import and type hint against, instead of importing
       the full SyftClient class.
    2. Enables dependency injection by defining a contract that any context
       or mock implementation can fulfill for testing or modular configuration.

    Attributes:
        config: Configuration settings for the Syft client
        workspace: Workspace instance managing data and computation
        server_client: HTTP client for server communication
    """

    if TYPE_CHECKING:
        from syftbox.client.server_client import SyftBoxClient

    config: SyftClientConfig
    """Configuration settings for the Syft client."""

    workspace: SyftWorkspace
    """Paths to different dirs in Syft"""

    plugins: Optional[PluginManagerInterface]
    """All initialized plugins."""

    client: "SyftBoxClient"
    """Client for communicating with the SyftBox server."""

    @property
    def email(self) -> str:
        """Email address of the current user."""
        ...

    @property
    def my_datasite(self) -> Path:
        """Path to the datasite directory for the current user."""
        ...  # pragma: no cover

    @property
    def all_datasites(self) -> list[str]:
        """Path to the datasite directory for the current user."""
        ...  # pragma: no cover


class ClientBase:
    def __init__(self, conn: httpx.Client):
        self.conn = conn

    def raise_for_status(self, response: httpx.Response) -> None:
        endpoint = response.request.url.path
        if response.status_code == 401:
            raise SyftAuthenticationError()
        elif response.status_code == 403:
            raise SyftPermissionError(f"No permission to access this resource: {response.text}")
        elif response.status_code != 200:
            raise SyftServerError(f"[{endpoint}] Server returned {response.status_code}: {response.text}")
        print(response.headers)
        print(response.status_code)
        server_version = response.headers.get(HEADER_SYFTBOX_VERSION)

        version_range = get_range_for_version(server_version)
        lower_bound_version = version_range[0]
        upper_bound_version = version_range[1]

        if len(upper_bound_version) > 0 and version.parse(upper_bound_version) < version.parse(__version__):
            raise SyftServerTooOld(f"Server version is {server_version} and can only work with clients between \
                                    {lower_bound_version} and {upper_bound_version}. Your client has version {__version__}.")
            
    @staticmethod
    def _make_headers(config: SyftClientConfig) -> dict:
        headers = {
            **SYFTBOX_HEADERS,
            HEADER_SYFTBOX_USER: config.email,
            "email": config.email,  # legacy
        }
        if config.access_token is not None:
            headers["Authorization"] = f"Bearer {config.access_token}"

        return headers

    @classmethod
    def from_config(cls, config: SyftClientConfig) -> "ClientBase":
        conn = httpx.Client(
            base_url=str(config.server_url),
            follow_redirects=True,
            headers=cls._make_headers(config),
        )
        return cls(conn)
