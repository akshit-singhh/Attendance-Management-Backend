#app/core/rbac.py

from fastapi import Depends, HTTPException, status
from typing import Callable
from app.models.user import User, RoleEnum

def AllowRoles(get_user_dependency: Callable, *allowed_roles):
    """
    Checks if the user has AT LEAST ONE of the allowed roles
    in their RoleAssignment list.
    """
    # Normalize inputs to string values for easy comparison
    normalized_allowed = {
        role.value if isinstance(role, RoleEnum) else str(role).upper()
        for role in allowed_roles
    }

    async def role_checker(current_user: User = Depends(get_user_dependency)):
        if not current_user:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Unauthenticated")

        # Extract all assigned roles for this user
        user_roles = {assignment.role.value for assignment in current_user.role_assignments}

        # Global Admin override
        if RoleEnum.ADMIN.value in user_roles:
            return current_user

        # Check for intersection between user's roles and allowed roles
        if not user_roles.intersection(normalized_allowed):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access denied. Requires one of: {', '.join(normalized_allowed)}"
            )

        return current_user

    return role_checker