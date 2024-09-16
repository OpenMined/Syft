from __future__ import annotations

import base64
import copy
import hashlib
import json
import os
import re
import sys
import types
import zlib
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from importlib.abc import Loader, MetaPathFinder
from importlib.util import spec_from_loader
from pathlib import Path
from threading import Lock
from urllib.parse import urlparse

import requests
from typing_extensions import Any, Self

USER_GROUP_GLOBAL = "GLOBAL"

ICON_FILE = "Icon"  # special
IGNORE_FILES = []


def perm_file_path(path: str) -> str:
    return f"{path}/_.syftperm"


def is_primitive_json_serializable(obj):
    if isinstance(obj, (list, dict, str, int, float, bool, type(None))):
        return True
    return False


def pack(obj) -> Any:
    if is_primitive_json_serializable(obj):
        return obj
    if hasattr(obj, "to_dict"):
        return obj.to_dict()
    raise Exception(f"Unable to pack type: {type(obj)} value: {obj}")


class Jsonable:
    def to_dict(self) -> dict:
        output = {}
        for k, v in self.__dict__.items():
            if k.startswith("_"):
                continue
            output[k] = pack(v)
        return output

    def __iter__(self):
        for key, val in self.to_dict().items():
            if key.startswith("_"):
                yield key, val

    def __getitem__(self, key):
        if key.startswith("_"):
            return None
        return self.to_dict()[key]

    @classmethod
    def load(cls, filepath: str) -> Self:
        try:
            with open(filepath) as f:
                data = f.read()
                d = json.loads(data)
                return cls(**d)
        except Exception as e:
            print(f"Unable to load file: {filepath}. {e}")
        return None

    def save(self, filepath: str) -> None:
        d = self.to_dict()
        with open(filepath, "w") as f:
            f.write(json.dumps(d))


@dataclass
class SyftPermission(Jsonable):
    admin: list[str]
    read: list[str]
    write: list[str]
    filepath: str | None = None

    @classmethod
    def datasite_default(cls, email: str) -> Self:
        return SyftPermission(
            admin=[email],
            read=[email],
            write=[email],
        )

    @classmethod
    def no_permission(self) -> Self:
        return SyftPermission(admin=[], read=[], write=[])

    def __repr__(self) -> str:
        string = "SyftPermission:\n"
        string += f"{self.filepath}\n"
        string += "ADMIN: ["
        for v in self.admin:
            string += v + ", "
        string += "]\n"

        string += "READ: ["
        for r in self.read:
            string += r + ", "
        string += "]\n"

        string += "WRITE: ["
        for w in self.write:
            string += w + ", "
        string += "]\n"
        return string


def bintostr(binary_data):
    return base64.b85encode(zlib.compress(binary_data)).decode("utf-8")


def strtobin(encoded_data):
    return zlib.decompress(base64.b85decode(encoded_data.encode("utf-8")))


class FileChangeKind(Enum):
    CREATE: str = "create"
    # READ: str "read"
    WRITE: str = "write"
    # append?
    DELETE: str = "delete"


@dataclass
class FileChange(Jsonable):
    kind: FileChangeKind
    parent_path: str
    sub_path: str
    file_hash: str
    sync_folder: str | None = None

    @property
    def kind_write(self) -> bool:
        return self.kind in [FileChangeKind.WRITE, FileChangeKind.CREATE]

    @property
    def kind_delete(self) -> bool:
        return self.kind == FileChangeKind.DELETE

    def to_dict(self) -> dict:
        output = {}
        for k, v in self.__dict__.items():
            if k.startswith("_"):
                continue
            if k == "kind":
                v = v.value
            output[k] = pack(v)
        return output

    @property
    def full_path(self) -> str:
        return self.sync_folder + "/" + self.parent_path + "/" + self.sub_path

    @property
    def internal_path(self) -> str:
        return self.parent_path + "/" + self.sub_path

    def read(self) -> bytes:
        with open(self.full_path, "rb") as f:
            return f.read()

    def write(self, data: bytes) -> bool:
        return self.write_to(data, self.full_path)

    def delete(self) -> bool:
        try:
            os.unlink(self.full_path)
            return True
        except Exception as e:
            if "No such file" in str(e):
                return True
            print(f"Failed to delete file at {self.full_path}. {e}")
        return False

    def write_to(self, data: bytes, path: str) -> bool:
        base_dir = os.path.dirname(path)
        os.makedirs(base_dir, exist_ok=True)
        try:
            with open(path, "wb") as f:
                f.write(data)
            return True
        except Exception as e:
            print("failed to write", path, e)
            return False


@dataclass
class DirState(Jsonable):
    tree: dict[str, str]
    timestamp: float
    sync_folder: str
    sub_path: str


def get_file_hash(file_path: str) -> str:
    with open(file_path, "rb") as file:
        return hashlib.md5(file.read()).hexdigest()


def ignore_dirs(directory: str, root: str, ignore_folders=None) -> bool:
    if ignore_folders is not None:
        for ignore_folder in ignore_folders:
            if root.endswith(ignore_folder):
                return True
    return False


def hash_dir(
    sync_folder: str,
    sub_path: str,
    ignore_folders: list | None = None,
) -> DirState:
    state_dict = {}
    full_path = os.path.join(sync_folder, sub_path)
    for root, dirs, files in os.walk(full_path):
        if not ignore_dirs(full_path, root, ignore_folders):
            for file in files:
                if not ignore_file(full_path, root, file):
                    path = os.path.join(root, file)
                    rel_path = os.path.relpath(path, full_path)
                    state_dict[rel_path] = get_file_hash(path)

    utc_unix_timestamp = datetime.now().timestamp()
    dir_state = DirState(
        tree=state_dict,
        timestamp=utc_unix_timestamp,
        sync_folder=sync_folder,
        sub_path=sub_path,
    )
    return dir_state


def ignore_file(directory: str, root: str, filename: str) -> bool:
    if directory == root:
        if filename.startswith(ICON_FILE):
            return True
        if filename in IGNORE_FILES:
            return True
    if filename == ".DS_Store":
        return True
    return False


def get_datasites(sync_folder: str) -> list[str]:
    datasites = []
    folders = os.listdir(sync_folder)
    for folder in folders:
        if "@" in folder:
            datasites.append(folder)
    return datasites


def build_tree_string(paths_dict, prefix=""):
    lines = []
    items = list(paths_dict.items())

    for index, (key, value) in enumerate(items):
        # Determine if it's the last item in the current directory level
        connector = "└── " if index == len(items) - 1 else "├── "
        lines.append(f"{prefix}{connector}{repr(key)}")

        # Prepare the prefix for the next level
        if isinstance(value, dict):
            extension = "    " if index == len(items) - 1 else "│   "
            lines.append(build_tree_string(value, prefix + extension))

    return "\n".join(lines)


@dataclass
class PermissionTree(Jsonable):
    tree: dict[str, SyftPermission]
    parent_path: str
    root_perm: SyftPermission | None

    @classmethod
    def from_path(cls, parent_path) -> Self:
        perm_dict = {}
        for root, dirs, files in os.walk(parent_path):
            for file in files:
                if file.endswith(".syftperm"):
                    path = os.path.join(root, file)
                    perm_dict[path] = SyftPermission.load(path)

        root_perm = None
        root_perm_path = perm_file_path(parent_path)
        if root_perm_path in perm_dict:
            root_perm = perm_dict[root_perm_path]

        return PermissionTree(
            root_perm=root_perm, tree=perm_dict, parent_path=parent_path
        )

    @property
    def root_or_default(self) -> SyftPermission:
        if self.root_perm:
            return self.root_perm
        return SyftPermission.no_permission()

    def permission_for_path(self, path: str) -> SyftPermission:
        parent_path = os.path.normpath(self.parent_path)
        current_perm = self.root_or_default

        # default
        if parent_path not in path:
            return current_perm

        sub_path = path.replace(parent_path, "")
        current_perm_level = parent_path
        for part in sub_path.split("/"):
            if part == "":
                continue

            current_perm_level += "/" + part
            next_perm_file = perm_file_path(current_perm_level)
            if next_perm_file in self.tree:
                # we could do some overlay with defaults but
                # for now lets just use a fully defined overwriting perm file
                next_perm = self.tree[next_perm_file]
                current_perm = next_perm

        return current_perm

    def __repr__(self) -> str:
        return f"PermissionTree: {self.parent_path}\n" + build_tree_string(self.tree)


def filter_read_state(user_email: str, dir_state: DirState, perm_tree: PermissionTree):
    filtered_tree = {}
    root_dir = dir_state.sync_folder + "/" + dir_state.sub_path
    for file_path, file_hash in dir_state.tree.items():
        full_path = root_dir + "/" + file_path
        perm_file_at_path = perm_tree.permission_for_path(full_path)
        if user_email in perm_file_at_path.read or "GLOBAL" in perm_file_at_path.read:
            filtered_tree[file_path] = file_hash
    return filtered_tree


def validate_email(email: str) -> bool:
    # Define a regex pattern for a valid email
    email_regex = r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$"

    # Use the match method to check if the email fits the pattern
    if re.match(email_regex, email):
        return True
    return False


@dataclass
class ClientConfig(Jsonable):
    config_path: Path
    sync_folder: Path | None = None
    port: int | None = None
    email: str | None = None
    token: int | None = None
    server_url: str = "http://localhost:5001"

    def save(self, path: str | None = None) -> None:
        if path is None:
            path = self.config_path
        super().save(path)

    @property
    def datasite_path(self) -> Path:
        return os.path.join(self.sync_folder, self.email)


class SharedState:
    def __init__(self, client_config: ClientConfig):
        self.data = {}
        self.lock = Lock()
        self.client_config = client_config

    @property
    def sync_folder(self) -> str:
        return self.client_config.sync_folder

    def get(self, key, default=None):
        with self.lock:
            if key == "my_datasites":
                return self._get_datasites()
            return self.data.get(key, default)

    def set(self, key, value):
        with self.lock:
            self.data[key] = value

    def _get_datasites(self):
        syft_folder = self.data.get(self.client_config.sync_folder)
        if not syft_folder or not os.path.exists(syft_folder):
            return []

        return [
            folder
            for folder in os.listdir(syft_folder)
            if os.path.isdir(os.path.join(syft_folder, folder))
        ]


def get_root_data_path() -> Path:
    # get the PySyft / data directory to share datasets between notebooks
    # on Linux and MacOS the directory is: ~/.syft/data"
    # on Windows the directory is: C:/Users/$USER/.syft/data

    data_dir = Path.home() / ".syft" / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    return data_dir


def autocache(
    url: str, extension: str | None = None, cache: bool = True
) -> Path | None:
    try:
        data_path = get_root_data_path()
        file_hash = hashlib.sha256(url.encode("utf8")).hexdigest()
        filename = file_hash
        if extension:
            filename += f".{extension}"
        file_path = data_path / filename
        if os.path.exists(file_path) and cache:
            return file_path
        return download_file(url, file_path)
    except Exception as e:
        print(f"Failed to autocache: {url}. {e}")
        return None


def download_file(url: str, full_path: str | Path) -> Path | None:
    full_path = Path(full_path)
    if not full_path.exists():
        r = requests.get(url, allow_redirects=True, verify=verify_tls())  # nosec
        if not r.ok:
            print(f"Got {r.status_code} trying to download {url}")
            return None
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_bytes(r.content)
    return full_path


def verify_tls() -> bool:
    return not str_to_bool(str(os.environ.get("IGNORE_TLS_ERRORS", "0")))


def str_to_bool(bool_str: str | None) -> bool:
    result = False
    bool_str = str(bool_str).lower()
    if bool_str == "true" or bool_str == "1":
        result = True
    return result


class SyftLink:
    @classmethod
    def from_file(cls, path: str) -> SyftLink:
        if not os.path.exists(path):
            raise Exception(f"{path} does not exist")
        with open(path, "r") as f:
            return cls.from_url(f.read())

    def from_path(path: str) -> SyftLink | None:
        parts = []
        collect = False
        for part in path.split("/"):
            # quick hack find the first email and thats the datasite
            if collect:
                parts.append(part)
            elif validate_email(part):
                collect = True
                parts.append(part)

        if len(parts):
            sync_path = "/".join(parts)
            return SyftLink.from_url(f"syft://{sync_path}")
        return None

    @classmethod
    def from_url(cls, url: str | SyftLink) -> SyftLink:
        if isinstance(url, SyftLink):
            return url
        try:
            # urlparse doesnt handle no protocol properly
            if "://" not in url:
                url = "http://" + url
            parts = urlparse(url)
            host_or_ip_parts = parts.netloc.split(":")
            # netloc is host:port
            port = 80
            if len(host_or_ip_parts) > 1:
                port = int(host_or_ip_parts[1])
            host_or_ip = host_or_ip_parts[0]
            if parts.scheme == "https":
                port = 443

            return SyftLink(
                host_or_ip=host_or_ip,
                path=parts.path,
                port=port,
                protocol=parts.scheme,
                query=getattr(parts, "query", ""),
            )
        except Exception as e:
            raise e

    def to_file(self, path: str) -> bool:
        with open(path, "w") as f:
            f.write(str(self))

    def __init__(
        self,
        protocol: str = "http",
        host_or_ip: str = "localhost",
        port: int | None = 5001,
        path: str = "",
        query: str = "",
    ) -> None:
        # in case a preferred port is listed but its not clear if an alternative
        # port was included in the supplied host_or_ip:port combo passed in earlier
        match_port = re.search(":[0-9]{1,5}", host_or_ip)
        if match_port:
            sub_server_url: SyftLink = SyftLink.from_url(host_or_ip)
            host_or_ip = str(sub_server_url.host_or_ip)  # type: ignore
            port = int(sub_server_url.port)  # type: ignore
            protocol = str(sub_server_url.protocol)  # type: ignore
            path = str(sub_server_url.path)  # type: ignore

        prtcl_pattrn = "://"
        if prtcl_pattrn in host_or_ip:
            protocol = host_or_ip[: host_or_ip.find(prtcl_pattrn)]
            start_index = host_or_ip.find(prtcl_pattrn) + len(prtcl_pattrn)
            host_or_ip = host_or_ip[start_index:]

        self.host_or_ip = host_or_ip
        self.path: str = path
        self.port = port
        self.protocol = protocol
        self.query = query

    def with_path(self, path: str) -> Self:
        dupe = copy.copy(self)
        dupe.path = path
        return dupe

    @property
    def query_string(self) -> str:
        query_string = ""
        if len(self.query) > 0:
            query_string = f"?{self.query}"
        return query_string

    @property
    def url(self) -> str:
        return f"{self.base_url}{self.path}{self.query_string}"

    @property
    def url_no_port(self) -> str:
        return f"{self.base_url_no_port}{self.path}{self.query_string}"

    @property
    def base_url(self) -> str:
        return f"{self.protocol}://{self.host_or_ip}:{self.port}"

    @property
    def base_url_no_port(self) -> str:
        return f"{self.protocol}://{self.host_or_ip}"

    @property
    def url_no_protocol(self) -> str:
        return f"{self.host_or_ip}:{self.port}{self.path}"

    @property
    def url_path(self) -> str:
        return f"{self.path}{self.query_string}"

    def to_tls(self) -> Self:
        if self.protocol == "https":
            return self

        # TODO: only ignore ssl in dev mode
        r = requests.get(  # nosec
            self.base_url, verify=verify_tls()
        )  # ignore ssl cert if its fake
        new_base_url = r.url
        if new_base_url.endswith("/"):
            new_base_url = new_base_url[0:-1]
        return self.__class__.from_url(
            url=f"{new_base_url}{self.path}{self.query_string}"
        )

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {self.url}>"

    def __str__(self) -> str:
        return self.url

    def __hash__(self) -> int:
        return hash(self.__str__())

    def __copy__(self) -> Self:
        return self.__class__.from_url(self.url)

    def set_port(self, port: int) -> Self:
        self.port = port
        return self

    @property
    def sync_path(self) -> str:
        return self.host_or_ip + self.path


@dataclass
class SyftVault(Jsonable):
    mapping: dict

    @classmethod
    def reset(cls) -> None:
        print("> Resetting Vault")
        vault = cls.load_vault()
        vault.mapping = {}
        vault.save_vault()

    @classmethod
    def load_vault(cls, override_path: str | None = None) -> SyftVault:
        vault_file_path = "~/.syft/vault.json"
        if override_path:
            vault_file_path = override_path
        vault_file_path = os.path.abspath(os.path.expanduser(vault_file_path))
        vault = cls.load(vault_file_path)
        if vault is None:
            vault = SyftVault(mapping={})
            vault.save(vault_file_path)
        return vault

    def save_vault(self, override_path: str | None = None) -> bool:
        try:
            vault_file_path = "~/.syft/vault.json"
            if override_path:
                vault_file_path = override_path
            vault_file_path = os.path.abspath(os.path.expanduser(vault_file_path))
            self.save(vault_file_path)
            return True
        except Exception as e:
            print("Failed to write vault", e)
        return False

    def set_private(self, public: SyftLink, private_path: str) -> bool:
        self.mapping[public.sync_path] = private_path
        return True

    def get_private(self, public: SyftLink) -> str:
        if public.sync_path in self.mapping:
            return self.mapping[public.sync_path]
        return None

    @classmethod
    def make_link(cls, public_path: str, private_path: str) -> bool:
        syft_link = SyftLink.from_path(public_path)
        link_file_path = syftlink_path(public_path)
        syft_link.to_file(link_file_path)
        vault = cls.load_vault()
        vault.set_private(syft_link, private_path)
        return True


def syftlink_path(path):
    return f"{path}.syftlink"


def sy_path(path, resolve_private: bool | None = None):
    if resolve_private is None:
        resolve_private = str_to_bool(os.environ.get("RESOLVE_PRIVATE", "False"))

    if not os.path.exists(path):
        raise Exception(f"No file at: {path}")
    if resolve_private:
        link_path = syftlink_path(path)
        if not os.path.exists(link_path):
            raise Exception(f"No private link at: {link_path}")
        syft_link = SyftLink.from_file(link_path)
        vault = SyftVault.load_vault()
        private_path = vault.get_private(syft_link)
        print("> Resolved private link", private_path)
        return private_path
    return path


def datasite(sync_path: str | None, datasite: str):
    return os.path.join(sync_path, datasite)


def extract_datasite(sync_import_path: str) -> str:
    datasite_parts = []
    for part in sync_import_path.split("."):
        if part == "datasets":
            break
        datasite_parts.append(part)
    email_string = ".".join(datasite_parts)
    email_string = email_string.replace(".at.", "@")
    return email_string


def attrs_for_datasite_import(sync_import_path: str) -> dict[str, Any]:
    import os

    sync_dir = os.environ.get("SYFTBOX_SYNC_DIR", None)
    if sync_dir is None:
        raise Exception("set SYFTBOX_SYNC_DIR")

    datasite = extract_datasite(sync_import_path)
    datasite_path = os.path.join(sync_dir, datasite)
    datasets_path = datasite_path + "/" + "datasets"
    attrs = {}

    if os.path.exists(datasets_path):
        for file in os.listdir(datasets_path):
            if file.endswith(".csv"):
                full_path = os.path.abspath(os.path.join(datasets_path, file))
                attrs[file.replace(".csv", "")] = sy_path(full_path)
            print(file)
    return attrs


# Custom loader that dynamically creates the modules under syftbox.lib
class DynamicLibSubmodulesLoader(Loader):
    def __init__(self, fullname, sync_path):
        self.fullname = fullname
        self.sync_path = sync_path

    def create_module(self, spec):
        # Create a new module object
        module = types.ModuleType(spec.name)
        return module

    def exec_module(self, module):
        # Register the module in sys.modules
        sys.modules[self.fullname] = module

        # Determine if the module is a package (i.e., it has submodules)
        if not self.fullname.endswith(".datasets"):
            # This module is a package; set the __path__ attribute
            module.__path__ = []  # Empty list signifies a namespace package

        # Attach the module to the parent module
        parent_name = self.fullname.rpartition(".")[0]
        parent_module = sys.modules.get(parent_name)
        if parent_module:
            setattr(parent_module, self.fullname.rpartition(".")[2], module)

        # If this is the datasets module, populate it dynamically
        if self.fullname.endswith(".datasets"):
            self.populate_datasets_module(module)

    def populate_datasets_module(self, module):
        attrs = attrs_for_datasite_import(self.sync_path)
        # Add dynamic content to the datasets module
        for key, value in attrs.items():
            setattr(module, key, value)


# Custom finder to locate and use the DynamicLibSubmodulesLoader
class DynamicLibSubmodulesFinder(MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        # Check if the module starts with 'syftbox.lib.' and has additional submodules
        if fullname.startswith("syftbox.lib."):
            # Split the fullname to extract the email path after 'syftbox.lib.'
            sync_path = fullname[len("syftbox.lib.") :]
            # Return a spec with our custom loader
            return spec_from_loader(
                fullname, DynamicLibSubmodulesLoader(fullname, sync_path)
            )
        return None


# Register the custom finder in sys.meta_path
sys.meta_path.insert(0, DynamicLibSubmodulesFinder())
