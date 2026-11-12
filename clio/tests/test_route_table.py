from __future__ import annotations

from clio.ui.route_table import POST_HANDLER_PARAMS, ROUTES, build_router


def test_route_table_has_unique_method_and_path_pairs():
    keys = [(route.method, route.path) for route in ROUTES]

    assert len(keys) == len(set(keys))


def test_all_route_handlers_use_lazy_names():
    assert all(isinstance(route.handler, str) for route in ROUTES)


def test_build_router_resolves_parameterized_handlers():
    calls: list[str] = []

    def resolver(name: str):
        calls.append(name)
        return f"resolved:{name}"

    router = build_router(resolver)
    handler, kwargs, route = router.dispatch("GET", "/api/vmeta/001_test")

    assert handler == "resolved:handle_get_vmeta"
    assert kwargs == {"stem": "001_test"}
    assert route is not None
    assert calls == ["handle_get_vmeta"]


def test_route_authentication_exception_is_preserved():
    router = build_router(lambda _name: lambda *_args, **_kwargs: None)

    policy = router.get_policy("POST", "/api/desktop/focus")

    assert policy.auth_required is False


def test_post_parameter_overrides_reference_known_routes():
    route_handlers = {route.handler for route in ROUTES}

    assert set(POST_HANDLER_PARAMS) <= route_handlers
    assert all(params and params <= {"qs", "obj"} for params in POST_HANDLER_PARAMS.values())
