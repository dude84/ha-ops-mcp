"""Tests for Docker disk reclamation — the DockerClient prune/df methods, the
haops_docker_prune two-phase tool, the prune-on-start lifespan hook, and config
wiring.

The safety property under test throughout: pruning only ever removes dangling
images and unused build cache. A tagged image must never be reachable, and the
tool must not act without a matching confirmation token.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from ha_ops_mcp.config import DockerConfig, load_config
from ha_ops_mcp.connections.docker import DockerClient
from ha_ops_mcp.tools.docker import haops_docker_prune

# ----------------------------------------------------------- DockerClient ----


def _client_with_request(responses: dict[str, Any]) -> DockerClient:
    """A DockerClient whose _request is replaced by a scripted lookup keyed on
    the request path (method-agnostic; each path used here is unambiguous)."""
    client = DockerClient(socket_path="/run/docker.sock")

    async def fake_request(method, path, *, json_body=None, params=None, timeout=30.0):
        client.calls.append({"method": method, "path": path, "params": params})
        return responses.get(path)

    client.calls = []  # type: ignore[attr-defined]
    client._request = fake_request  # type: ignore[assignment]
    return client


@pytest.mark.asyncio
async def test_disk_usage_summarises_dangling_and_cache():
    client = _client_with_request(
        {
            "/system/df": {
                "LayersSize": 8_280_000_000,
                "Images": [
                    {"RepoTags": ["ha-core:2026.8"], "Size": 2_000_000_000},
                    {"RepoTags": ["<none>:<none>"], "Size": 900_000_000},
                    {"RepoTags": None, "Size": 100_000_000},
                ],
                "BuildCache": [
                    {"Size": 500_000_000, "InUse": False},
                    {"Size": 300_000_000, "InUse": True},
                ],
            }
        }
    )
    usage = await client.disk_usage()

    assert usage["images_count"] == 3
    assert usage["images_size"] == 3_000_000_000
    assert usage["dangling_count"] == 2  # <none>:<none> and null tags
    assert usage["dangling_size"] == 1_000_000_000
    assert usage["build_cache_reclaimable"] == 500_000_000  # InUse excluded
    assert usage["reclaimable"] == 1_500_000_000  # dangling + unused cache
    assert usage["layers_size"] == 8_280_000_000


@pytest.mark.asyncio
async def test_prune_images_defaults_to_dangling_only():
    client = _client_with_request(
        {"/images/prune": {"ImagesDeleted": [{"Deleted": "sha1"}], "SpaceReclaimed": 42}}
    )
    result = await client.prune_images()

    assert result == {"images_deleted": 1, "space_reclaimed": 42}
    # The filter must scope to dangling — never a blanket unused-image sweep.
    assert client.calls[0]["params"] == {"filters": '{"dangling":["true"]}'}


@pytest.mark.asyncio
async def test_prune_images_dangerous_mode_is_explicit():
    client = _client_with_request({"/images/prune": {"SpaceReclaimed": 0}})
    await client.prune_images(dangling_only=False)
    assert client.calls[0]["params"] == {"filters": '{"dangling":["false"]}'}


@pytest.mark.asyncio
async def test_prune_build_cache():
    client = _client_with_request(
        {"/build/prune": {"CachesDeleted": ["a", "b"], "SpaceReclaimed": 7}}
    )
    result = await client.prune_build_cache()
    assert result == {"caches_deleted": 2, "space_reclaimed": 7}
    assert client.calls[0]["path"] == "/build/prune"


# ------------------------------------------------------------- fake ctx ------


class _FakeDocker:
    def __init__(self, *, available: bool = True) -> None:
        self._available = available
        self.unavailable_reason = "docker_api + Protection mode OFF required"
        self.pruned_images: list[bool] = []
        self.pruned_cache = 0
        self.usage = {"reclaimable": 1_500_000_000, "dangling_count": 2}

    def available(self) -> bool:
        return self._available

    async def disk_usage(self) -> dict[str, Any]:
        return self.usage

    async def prune_images(self, *, dangling_only: bool = True) -> dict[str, Any]:
        self.pruned_images.append(dangling_only)
        return {"images_deleted": 3, "space_reclaimed": 1_000_000_000}

    async def prune_build_cache(self) -> dict[str, Any]:
        self.pruned_cache += 1
        return {"caches_deleted": 1, "space_reclaimed": 500_000_000}


class _FakeSafety:
    def __init__(self) -> None:
        self.tokens: dict[str, Any] = {}
        self.consumed: list[str] = []
        self._n = 0

    def create_token(self, action: str, details: dict[str, Any]):
        self._n += 1
        tid = f"tok{self._n}"
        tk = SimpleNamespace(id=tid, action=action, details=details)
        self.tokens[tid] = tk
        return tk

    def validate_token(self, token_id: str):
        if token_id not in self.tokens:
            raise ValueError("Invalid or already-used token")
        return self.tokens[token_id]

    def consume_token(self, token_id: str) -> None:
        self.consumed.append(token_id)
        self.tokens.pop(token_id, None)

    def claim_token(self, token_id: str):
        if token_id not in self.tokens:
            raise ValueError("Invalid or already-used token")
        tk = self.tokens.pop(token_id)
        self.consumed.append(token_id)
        return tk


class _FakeAudit:
    def __init__(self) -> None:
        self.entries: list[dict[str, Any]] = []

    async def log(self, **kwargs: Any) -> None:
        self.entries.append(kwargs)


def _ctx(*, available: bool = True) -> Any:
    return SimpleNamespace(
        docker=_FakeDocker(available=available),
        safety=_FakeSafety(),
        audit=_FakeAudit(),
    )


# --------------------------------------------------------------- the tool ----


@pytest.mark.asyncio
async def test_prune_preview_returns_usage_and_token():
    ctx = _ctx()
    preview = await haops_docker_prune(ctx)

    assert preview["token"] == "tok1"
    assert preview["usage"]["reclaimable"] == 1_500_000_000
    assert "token" in preview and "message" in preview
    # Preview must not have pruned anything.
    assert ctx.docker.pruned_images == []
    assert ctx.docker.pruned_cache == 0


@pytest.mark.asyncio
async def test_prune_confirm_reclaims_and_audits():
    ctx = _ctx()
    preview = await haops_docker_prune(ctx)
    result = await haops_docker_prune(ctx, confirm=True, token=preview["token"])

    assert result["images_deleted"] == 3
    assert result["caches_deleted"] == 1
    assert result["space_reclaimed_bytes"] == 1_500_000_000
    # Dangling-only, exactly once each.
    assert ctx.docker.pruned_images == [True]
    assert ctx.docker.pruned_cache == 1
    assert ctx.safety.consumed == [preview["token"]]
    assert ctx.audit.entries[0]["tool"] == "docker_prune"


@pytest.mark.asyncio
async def test_prune_confirm_requires_token():
    ctx = _ctx()
    result = await haops_docker_prune(ctx, confirm=True)
    assert "error" in result
    assert ctx.docker.pruned_images == []


@pytest.mark.asyncio
async def test_prune_rejects_foreign_token():
    """A token minted for another action must not drive a prune."""
    ctx = _ctx()
    tk = ctx.safety.create_token(action="container_exec", details={})
    result = await haops_docker_prune(ctx, confirm=True, token=tk.id)
    assert "error" in result
    assert ctx.docker.pruned_images == []


@pytest.mark.asyncio
async def test_prune_token_is_single_use():
    ctx = _ctx()
    preview = await haops_docker_prune(ctx)
    await haops_docker_prune(ctx, confirm=True, token=preview["token"])
    replay = await haops_docker_prune(ctx, confirm=True, token=preview["token"])
    assert "error" in replay
    assert ctx.docker.pruned_images == [True]  # only the first ran


@pytest.mark.asyncio
async def test_prune_unavailable_without_socket():
    ctx = _ctx(available=False)
    result = await haops_docker_prune(ctx)
    assert result["docker_available"] is False
    assert "how_to_enable" in result


# --------------------------------------------------------------- config ------


def test_docker_config_defaults_on():
    assert DockerConfig().prune_on_start is True


def test_docker_prune_on_start_env_can_disable(monkeypatch, tmp_path):
    monkeypatch.setenv("HA_OPS_DOCKER_PRUNE_ON_START", "false")
    cfg = load_config(tmp_path / "nonexistent.yaml")
    assert cfg.docker.prune_on_start is False


def test_docker_prune_on_start_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("HA_OPS_DOCKER_PRUNE_ON_START", "true")
    cfg = load_config(tmp_path / "nonexistent.yaml")
    assert cfg.docker.prune_on_start is True


def test_docker_prune_on_start_yaml(tmp_path):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("docker:\n  prune_on_start: true\n")
    cfg = load_config(cfg_file)
    assert cfg.docker.prune_on_start is True


# ----------------------------------------------------- startup lifespan hook -


def _startup_ctx(*, enabled: bool, available: bool = True) -> Any:
    return SimpleNamespace(
        config=SimpleNamespace(docker=DockerConfig(prune_on_start=enabled)),
        docker=_FakeDocker(available=available),
    )


@pytest.mark.asyncio
async def test_startup_prune_noop_when_flag_off():
    from ha_ops_mcp.server import _prune_docker_on_start

    ctx = _startup_ctx(enabled=False)
    await _prune_docker_on_start(ctx)
    assert ctx.docker.pruned_images == []
    assert ctx.docker.pruned_cache == 0


@pytest.mark.asyncio
async def test_startup_prune_runs_when_enabled_and_available():
    from ha_ops_mcp.server import _prune_docker_on_start

    ctx = _startup_ctx(enabled=True)
    await _prune_docker_on_start(ctx)
    assert ctx.docker.pruned_images == [True]
    assert ctx.docker.pruned_cache == 1


@pytest.mark.asyncio
async def test_startup_prune_skips_without_socket():
    from ha_ops_mcp.server import _prune_docker_on_start

    ctx = _startup_ctx(enabled=True, available=False)
    await _prune_docker_on_start(ctx)  # must not raise
    assert ctx.docker.pruned_images == []


@pytest.mark.asyncio
async def test_startup_prune_survives_engine_error():
    from ha_ops_mcp.server import _prune_docker_on_start

    ctx = _startup_ctx(enabled=True)

    async def boom(*, dangling_only: bool = True):
        raise RuntimeError("engine down")

    ctx.docker.prune_images = boom
    await _prune_docker_on_start(ctx)  # non-fatal — startup must continue
    assert ctx.docker.pruned_cache == 0
