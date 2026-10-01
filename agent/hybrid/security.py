"""Loopback-only local settings, token provisioning and exact request guards."""
import ipaddress
import os
import secrets
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    runtime_root: Path
    port: int = 8100
    token: str | None = None
    max_upload_bytes: int = 256 * 1024 * 1024
    operator_id: str = "local-operator"

    @classmethod
    def from_environment(cls):
        base = Path(os.environ.get("LOCALAPPDATA", ""))
        if not base.is_absolute():
            raise RuntimeError("LOCALAPPDATA must be an absolute local directory")
        root = base / "AFF" / "flowkit"
        return cls(root, port=int(os.environ.get("FLOWKIT_PORT", "8100")), operator_id=os.environ.get("FLOWKIT_OPERATOR_ID", "local-operator"))

    def provision(self) -> str:
        root = self.runtime_root.resolve()
        approved_root = (Path(os.environ.get("LOCALAPPDATA", "")) / "AFF" / "flowkit").resolve()
        if not root.is_relative_to(approved_root):
            raise RuntimeError("Runtime must be inside LOCALAPPDATA/AFF/flowkit")
        repo = Path(__file__).resolve().parents[2]
        if root.is_relative_to(repo):
            raise RuntimeError("Runtime cannot be inside the Git checkout")
        if not 1 <= self.port <= 65535:
            raise RuntimeError("Invalid loopback port")
        root.mkdir(parents=True, exist_ok=True)
        for name in ("inputs", "output"):
            (root / name).mkdir(exist_ok=True)
        if self.token is not None:
            if len(self.token) < 32:
                raise RuntimeError("Local token must have at least 32 characters")
            return self.token
        token_path = root / "local-token.txt"
        try:
            with token_path.open("x", encoding="utf-8") as stream:
                token = secrets.token_urlsafe(48)
                stream.write(token)
            if os.name != "nt":
                token_path.chmod(0o600)
        except FileExistsError:
            token = token_path.read_text(encoding="utf-8").strip()
        if len(token) < 32:
            raise RuntimeError("Invalid stored local token")
        return token

    @property
    def hosts(self):
        return {f"127.0.0.1:{self.port}", f"localhost:{self.port}", f"[::1]:{self.port}"}

    @property
    def origins(self):
        return {f"http://127.0.0.1:{self.port}", f"http://localhost:{self.port}", f"http://[::1]:{self.port}"}

    def transport_allowed(self, host: str, origin: str | None, peer: str | None) -> bool:
        try:
            local = bool(peer) and ipaddress.ip_address(peer).is_loopback
        except ValueError:
            local = False
        return local and host in self.hosts and (origin is None or origin in self.origins)
