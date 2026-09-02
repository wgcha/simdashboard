from fastapi import APIRouter

from ..adapters.http.routers.project_assignees import router as project_assignees_router
from ..adapters.http.routers.project_memberships import router as project_memberships_router
from ..adapters.http.routers.project_invitations import router as project_invitations_router
from ..adapters.http.routers.user_administration import router as user_administration_router
from ..adapters.http.routers.menu_policy import router as menu_policy_router


router = APIRouter(tags=["access-control"])


router.include_router(user_administration_router)

router.include_router(project_memberships_router)
router.include_router(project_invitations_router)
router.include_router(project_assignees_router)
router.include_router(menu_policy_router)
