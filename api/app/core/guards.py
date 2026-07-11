# api/app/core/guards.py
#
# FastAPI dependency factory for role-based access control.
#
# Pattern from the project guide:
#   Depends(require_role("feat:datasource:create"))
#
# The factory returns a dependency that:
#   1. Resolves the current user (from cookie or Bearer header via get_current_user)
#   2. Checks whether the user holds the required role
#   3. Raises 403 if not — the route handler is never reached
#   4. Returns the current_user dict so the route can read claims (tenant_id, id, etc.)
#
# Admins (insightx-admin realm role) bypass all role checks.

from typing import Callable

from fastapi import Depends, HTTPException, status

from app.core.security import get_current_user


def require_role(role: str) -> Callable:
    """
    Return a FastAPI dependency that enforces a single role.

    Usage:
        @router.post("/")
        async def create(..., current_user: dict = Depends(require_role("feat:datasource:create"))):
            ...

    Adding a new guarded operation = one Depends() call. Nothing else changes.
    """
    def _checker(current_user: dict = Depends(get_current_user)) -> dict:
        realm_roles:  list[str] = current_user.get("roles", [])
        client_roles: list[str] = current_user.get("client_roles", [])

        # insightx-admin (realm role) bypasses all feature-level checks
        if "insightx-admin" in realm_roles:
            return current_user

        if role not in client_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to perform this action. "
                       "Please contact your administrator.",
            )
        return current_user

    # Give the inner function a unique name so FastAPI generates distinct dependency keys
    _checker.__name__ = f"require_role_{role.replace(':', '_')}"
    return _checker
