import inspect
from functools import wraps
from typing import Any, Callable, TypeVar

F = TypeVar("F", bound=Callable[..., Any])

SIGN_IN_REQUIRED_MESSAGE = "Sign in required."
ADMIN_REQUIRED_MESSAGE = "Admin access required."


class NotAuthenticated(PermissionError):
    """Raised by require_auth when the ctx is None. Wave 4A/5A catch it and
    open the sign-in modal instead of erroring the pipeline."""

    def __init__(self, message: str = SIGN_IN_REQUIRED_MESSAGE) -> None:
        super().__init__(message)


class NotAdmin(PermissionError):
    """Raised by require_admin when ctx.is_admin is False."""

    def __init__(self, message: str = ADMIN_REQUIRED_MESSAGE) -> None:
        super().__init__(message)


def is_generator_function(fn: Callable[..., Any]) -> bool:
    return inspect.isgeneratorfunction(fn)


def _extract_ctx(args: tuple) -> Any:
    if not args:
        return None
    return args[-1]


def require_auth(fn: F) -> F:
    # Gradio streaming handlers are generator functions; the check must happen
    # before the first yield, so we branch on isgeneratorfunction here.
    if is_generator_function(fn):
        @wraps(fn)
        def gen_wrapper(*args: Any, **kwargs: Any) -> Any:
            ctx = _extract_ctx(args)
            if ctx is None:
                raise NotAuthenticated()
            yield from fn(*args, **kwargs)

        return gen_wrapper  # type: ignore[return-value]

    @wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        ctx = _extract_ctx(args)
        if ctx is None:
            raise NotAuthenticated()
        return fn(*args, **kwargs)

    return wrapper  # type: ignore[return-value]


def require_admin(fn: F) -> F:
    if is_generator_function(fn):
        @wraps(fn)
        def gen_wrapper(*args: Any, **kwargs: Any) -> Any:
            ctx = _extract_ctx(args)
            if ctx is None:
                raise NotAuthenticated()
            if not getattr(ctx, "is_admin", False):
                raise NotAdmin()
            yield from fn(*args, **kwargs)

        return gen_wrapper  # type: ignore[return-value]

    @wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        ctx = _extract_ctx(args)
        if ctx is None:
            raise NotAuthenticated()
        if not getattr(ctx, "is_admin", False):
            raise NotAdmin()
        return fn(*args, **kwargs)

    return wrapper  # type: ignore[return-value]
