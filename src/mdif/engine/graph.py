"""Dependency graph: topological order and lineage edges."""

from __future__ import annotations

from collections import deque
from typing import Any

from mdif.errors import ConfigError


def build_adjacency(sources: list[dict[str, Any]]) -> dict[str, list[str]]:
    names = {s["name"] for s in sources}
    adj: dict[str, list[str]] = {n: [] for n in names}
    for s in sources:
        for dep in s.get("depends_on") or []:
            if dep not in names:
                raise ConfigError(f"Unknown dependency '{dep}' for source '{s['name']}'")
            adj[dep].append(s["name"])
    return adj


def topological_layers(sources: list[dict[str, Any]]) -> list[list[str]]:
    """Return execution layers; nodes in the same layer are independent."""
    indeg: dict[str, int] = {s["name"]: 0 for s in sources}
    children = build_adjacency(sources)
    deps_map = {s["name"]: list(s.get("depends_on") or []) for s in sources}
    for name, deps in deps_map.items():
        indeg[name] = len(deps)

    layers: list[list[str]] = []
    ready = deque(sorted(n for n, d in indeg.items() if d == 0))
    seen = 0
    while ready:
        layer = list(ready)
        ready.clear()
        layers.append(layer)
        for n in layer:
            seen += 1
            for child in sorted(children[n]):
                indeg[child] -= 1
                if indeg[child] == 0:
                    ready.append(child)
    if seen != len(indeg):
        raise ConfigError("Cycle detected in source depends_on graph")
    return layers


def lineage_edges(sources: list[dict[str, Any]]) -> list[dict[str, str]]:
    edges: list[dict[str, str]] = []
    for s in sources:
        src_name = s["name"]
        target = s["target"]
        edges.append({"from": src_name, "to": target, "kind": "load"})
        for dep in s.get("depends_on") or []:
            edges.append({"from": dep, "to": src_name, "kind": "depends_on"})
    return edges


def sources_by_name(sources: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {s["name"]: s for s in sources}
