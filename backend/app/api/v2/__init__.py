"""Install the unified API outside the existing authentication boundary."""
from .registry import ApiRegistry
from .middleware import ApiContractMiddleware


def install(app, *, service="workbench", legacy_prefix="/api/v1"):
    registry = ApiRegistry(app, service=service, legacy_prefix=legacy_prefix)
    app.state.api_registry = registry
    app.openapi = registry.openapi
    # Must be installed last so authentication/CSRF errors are wrapped too.
    app.add_middleware(ApiContractMiddleware, registry=registry)
    return registry
