from dataclasses import dataclass

import pytest

from UI.gate import (
    ADMIN_REQUIRED_MESSAGE,
    SIGN_IN_REQUIRED_MESSAGE,
    NotAdmin,
    NotAuthenticated,
    is_generator_function,
    require_admin,
    require_auth,
)


@dataclass
class MockCtx:
    is_admin: bool = False


def test_require_auth_passes_with_ctx():
    @require_auth
    def handler(ctx):
        return 42

    assert handler(MockCtx()) == 42


def test_require_auth_raises_without_ctx():
    @require_auth
    def handler(ctx):
        return 42

    with pytest.raises(NotAuthenticated):
        handler(None)


def test_require_auth_message():
    @require_auth
    def handler(ctx):
        return 42

    with pytest.raises(NotAuthenticated) as exc_info:
        handler(None)
    assert str(exc_info.value) == SIGN_IN_REQUIRED_MESSAGE


def test_require_auth_passes_through_args():
    @require_auth
    def handler(a, b, ctx):
        return (a, b)

    assert handler(1, 2, MockCtx()) == (1, 2)


def test_require_auth_wraps_generator_function():
    @require_auth
    def handler(ctx):
        yield 1
        yield 2

    assert list(handler(MockCtx())) == [1, 2]


def test_require_auth_generator_raises_before_yield():
    @require_auth
    def handler(ctx):
        yield 1
        yield 2

    gen = handler(None)
    with pytest.raises(NotAuthenticated):
        next(gen)


def test_require_admin_raises_when_not_authenticated():
    @require_admin
    def handler(ctx):
        return "ok"

    with pytest.raises(NotAuthenticated):
        handler(None)


def test_require_admin_raises_when_not_admin():
    @require_admin
    def handler(ctx):
        return "ok"

    with pytest.raises(NotAdmin):
        handler(MockCtx(is_admin=False))


def test_require_admin_passes_when_admin():
    @require_admin
    def handler(ctx):
        return "ok"

    assert handler(MockCtx(is_admin=True)) == "ok"


def test_exceptions_are_permission_error():
    assert isinstance(NotAuthenticated(), PermissionError)
    assert isinstance(NotAdmin(), PermissionError)


def test_preserves_function_name():
    def f(ctx):
        return 1

    assert require_auth(f).__name__ == "f"


def test_is_generator_function():
    assert is_generator_function(lambda: None) is False

    def g():
        yield 1

    assert is_generator_function(g) is True


def test_admin_message():
    @require_admin
    def handler(ctx):
        return "ok"

    with pytest.raises(NotAdmin) as exc_info:
        handler(MockCtx(is_admin=False))
    assert str(exc_info.value) == ADMIN_REQUIRED_MESSAGE
