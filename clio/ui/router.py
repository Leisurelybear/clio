"""URL routing for the UI server.

Replaces hand-written if-chains in server.py with a declarative Router.
Supports static routes, {param} path parameters, and prefix matching.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class RoutePolicy:
    method: str
    path: str
    auth_required: bool = True
    prefix: bool = False


@dataclass
class Route:
    method: str
    path: str
    handler: Callable | str
    auth_required: bool = True
    prefix: bool = False


class Router:
    def __init__(self, resolver: Callable[[str], Callable | None] | None = None) -> None:
        self._routes: dict[str, list[tuple[re.Pattern, list[str], Route]]] = {}
        self._resolver = resolver or (lambda k: None)

    def add(self, route: Route) -> None:
        pattern, param_names = self._compile(route.path)
        self._routes.setdefault(route.method, []).append((pattern, param_names, route))

    def add_list(self, routes: list[Route]) -> None:
        for r in routes:
            self.add(r)

    def _resolve_handler(self, route: Route) -> Callable | None:
        if isinstance(route.handler, str):
            return self._resolver(route.handler)
        return route.handler

    def dispatch(self, method: str, path: str) -> tuple[Callable | None, dict[str, str], Route | None]:
        for pattern, param_names, route in self._routes.get(method, []):
            if route.prefix:
                if path.startswith(route.path):
                    return self._resolve_handler(route), {}, route
            else:
                m = pattern.match(path)
                if m:
                    kwargs = dict(zip(param_names, m.groups(), strict=True))
                    return self._resolve_handler(route), kwargs, route
        return None, {}, None

    def get_policy(self, method: str, path: str) -> RoutePolicy:
        _, _, route = self.dispatch(method, path)
        if route is not None:
            return RoutePolicy(
                method=route.method,
                path=route.path,
                auth_required=route.auth_required,
                prefix=route.prefix,
            )
        if path.startswith("/api/"):
            return RoutePolicy(method, path, auth_required=True)
        return RoutePolicy(method, path, auth_required=method in {"PUT", "POST"})

    @staticmethod
    def _compile(path: str) -> tuple[re.Pattern, list[str]]:
        param_names: list[str] = []

        def _replace_param(m: re.Match) -> str:
            name = m.group(1)
            param_names.append(name)
            return "<<<PARAM>>>"

        placeholder = re.sub(r"\{(\w+)\}", _replace_param, path)
        escaped = re.escape(placeholder)
        regex_str = escaped.replace("<<<PARAM>>>", r"([^/]+)")
        return re.compile(f"^{regex_str}$"), param_names
