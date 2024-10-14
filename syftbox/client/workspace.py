from pathlib import Path

from syftbox.client.const import DEFAULT_WORKSPACE_DIR


class SyftWorkspace:
    """
    A Syft workspace is a directory structure for everything stored by the client.
    Each workspace is expected to be unique for a client.

    syft_root_dir/
    ├── config/                      <-- syft client configuration
    │   └── config.json
    │   └── logs/                    <-- syft client logs
    ├── plugin/                      <-- workspace for plugins to store data
    │   └── sync/
    │       └── changelog.txt
    └── sync/                        <-- everything under this gets sync'd
        ├── apps/
        │   └── fedflix
        └── datasites/
            ├── alice@acme.org
            └── bob@acme.org
    """

    def __init__(self, root_dir: Path | str = DEFAULT_WORKSPACE_DIR):
        self.root_dir = Path(root_dir).expanduser()

        # config dir
        self.config_dir = self.root_dir / "config"
        self.logs_dir = self.config_dir / "logs"

        # plugins dir
        self.plugins_dir = self.root_dir / "plugins"

        # sync dirs
        self.sync_dir = self.root_dir / "sync"
        self.datasites_dir = self.sync_dir / "datasites"
        self.apps_dir = self.sync_dir / "apps"

    def mkdirs(self):
        dirs_to_create = [
            self.config_dir,
            self.logs_dir,
            self.sync_dir,
            self.datasites_dir,
            self.plugins_dir,
            self.apps_dir,
        ]
        for dir_path in dirs_to_create:
            dir_path.mkdir(parents=True, exist_ok=True)
