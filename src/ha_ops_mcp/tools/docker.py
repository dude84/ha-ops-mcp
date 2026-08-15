"""Docker maintenance tools — reclaim space the HA host accumulates.

Repeated addon rebuilds (every ``dev-deploy`` / addon update) leave the HA
host holding artifacts that nothing references: dangling ``<none>`` images from
superseded builds and, on BuildKit hosts, unused build cache. Over a dev
stretch that can grow to many GB. ``haops_docker_prune`` reclaims exactly those
two buckets and nothing else.

Like the container tools this is inert without the Docker socket — see
``connections/docker.py`` for why that needs a manifest capability *and*
Protection mode off. A ``docker.prune_on_start`` config flag (on by default,
but only fires when the socket is present) runs the same prune automatically
at startup; a successful dev-deploy restarts the addon, so each rebuild
self-cleans the image it just orphaned.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ha_ops_mcp.connections.docker import DockerError, DockerUnavailableError
from ha_ops_mcp.server import registry
from ha_ops_mcp.tools.container import _unavailable

if TYPE_CHECKING:
    from ha_ops_mcp.server import HaOpsContext


@registry.tool(
    name="haops_docker_prune",
    description=(
        "Reclaim disk on the HA host by removing DANGLING Docker images "
        "(untagged '<none>' layers left behind by repeated addon rebuilds) and "
        "UNUSED build cache. "
        "Use this when the HA machine is low on disk after a run of "
        "dev-deploys / addon updates, or to check how much is reclaimable. "
        "SAFE BY DESIGN: it only ever touches images referenced by no tag and "
        "no container, and build cache referenced by no live image. It NEVER "
        "removes a tagged image (HA Core, other add-ons, the current addon), "
        "never touches volumes, and never runs 'docker system prune -a'. "
        "Two-phase: call without confirm to PREVIEW — returns a 'usage' block "
        "with reclaimable bytes and a token. Then call again with confirm=true "
        "and the token to prune; the response reports images_deleted, "
        "caches_deleted and space_reclaimed_bytes. "
        "Requires the Docker socket: 'docker_api: true' in the manifest AND "
        "Protection mode OFF on the add-on. If it is not enabled the response "
        "explains how to enable it instead of failing opaquely. "
        "Also runs automatically at addon start (docker_prune_on_start, on by "
        "default) when the socket is available. "
        "Byte counts are raw — divide by 1e9 for GB. "
        "Parameters: confirm (bool, default false), token (string, required if "
        "confirm=true)."
    ),
    params={
        "confirm": {
            "type": "boolean",
            "description": "Execute the prune (default false = preview only)",
            "default": False,
        },
        "token": {
            "type": "string",
            "description": "Confirmation token from the preview step",
        },
    },
)
async def haops_docker_prune(
    ctx: HaOpsContext,
    confirm: bool = False,
    token: str | None = None,
) -> dict[str, Any]:
    unavailable = _unavailable(ctx)
    if unavailable:
        return unavailable

    assert ctx.docker is not None

    if not confirm:
        try:
            usage = await ctx.docker.disk_usage()
        except DockerUnavailableError as e:
            return {"error": str(e), "docker_available": False}
        except DockerError as e:
            return {"error": str(e)}

        tk = ctx.safety.create_token(
            action="docker_prune",
            details={"reclaimable": usage.get("reclaimable", 0)},
        )
        return {
            "usage": usage,
            "scope": (
                "dangling images + unused build cache — tagged/in-use images "
                "and volumes are never touched"
            ),
            "token": tk.id,
            "message": (
                "Review the reclaimable bytes. Call again with confirm=true "
                "and this token to prune."
            ),
        }

    if token is None:
        return {"error": "confirm=true requires a token"}

    try:
        token_data = ctx.safety.validate_token(token)
    except Exception as e:  # noqa: BLE001 — surfaced to the caller as a message
        return {"error": str(e)}

    if getattr(token_data, "action", None) != "docker_prune":
        return {
            "error": "Token was not issued for docker_prune. Re-run the preview."
        }

    try:
        images = await ctx.docker.prune_images(dangling_only=True)
        cache = await ctx.docker.prune_build_cache()
    except DockerUnavailableError as e:
        return {"error": str(e), "docker_available": False}
    except DockerError as e:
        return {"error": str(e)}

    ctx.safety.consume_token(token)

    reclaimed = images["space_reclaimed"] + cache["space_reclaimed"]
    await ctx.audit.log(
        tool="docker_prune",
        details={
            "images_deleted": images["images_deleted"],
            "caches_deleted": cache["caches_deleted"],
            "space_reclaimed": reclaimed,
        },
        token_id=token,
    )

    return {
        "images_deleted": images["images_deleted"],
        "caches_deleted": cache["caches_deleted"],
        "space_reclaimed_bytes": reclaimed,
        "docker_available": True,
    }
