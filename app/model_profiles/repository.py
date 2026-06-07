from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.schemas.model_profiles import (
    CreateModelProfileRequest,
    ModelProfileSummary,
    ModelUsage,
)
from app.storage.models import UserModelProfile


class ModelProfileError(RuntimeError):
    pass


class ModelProfileNotFoundError(ModelProfileError):
    pass


class ModelProfileAccessError(ModelProfileError):
    pass


class ModelProfileRepository:
    def __init__(self, session: Session):
        self.session = session

    def create_profile(
        self,
        *,
        tenant_id: str | None,
        guild_id: str,
        user_id: str,
        request: CreateModelProfileRequest,
        encrypted_api_key: str,
    ) -> ModelProfileSummary:
        display_name = request.display_name or (
            f"{request.usage}:{request.provider}:{request.model_name}"
        )
        make_default = request.make_default or not self._has_active_profile(
            guild_id=guild_id,
            user_id=user_id,
            usage=request.usage,
        )
        if make_default:
            self._clear_default(guild_id=guild_id, user_id=user_id, usage=request.usage)
        profile = UserModelProfile(
            tenant_id=tenant_id,
            guild_id=guild_id,
            user_id=user_id,
            provider=request.provider,
            model_name=request.model_name,
            display_name=display_name,
            encrypted_api_key=encrypted_api_key,
            base_url=request.base_url,
            usage=request.usage,
            status="active",
            is_default=make_default,
            last_test_status="untested",
        )
        self.session.add(profile)
        self.session.flush()
        return model_profile_summary(profile)

    def list_profiles(
        self,
        *,
        guild_id: str,
        user_id: str,
        usage: ModelUsage | None = None,
        include_revoked: bool = False,
    ) -> list[ModelProfileSummary]:
        statement = select(UserModelProfile).where(
            UserModelProfile.guild_id == guild_id,
            UserModelProfile.user_id == user_id,
        )
        if usage is not None:
            statement = statement.where(UserModelProfile.usage == usage)
        if not include_revoked:
            statement = statement.where(UserModelProfile.status == "active")
        statement = statement.order_by(UserModelProfile.created_at.desc())
        profiles = self.session.execute(statement).scalars().all()
        return [model_profile_summary(profile) for profile in profiles]

    def get_active_profile(
        self,
        *,
        profile_id: UUID,
        guild_id: str | None,
        user_id: str | None,
        tenant_id: str | None = None,
        usage: ModelUsage | None = "chat",
    ) -> UserModelProfile:
        profile = self.session.get(UserModelProfile, profile_id)
        if profile is None or profile.status != "active":
            raise ModelProfileNotFoundError(f"model profile not found: {profile_id}")
        if usage is not None and profile.usage != usage:
            raise ModelProfileNotFoundError(f"model profile not found: {profile_id}")
        if guild_id is not None and profile.guild_id != guild_id:
            raise ModelProfileAccessError("model profile guild_id does not match caller")
        if user_id is not None and profile.user_id != user_id:
            raise ModelProfileAccessError("model profile user_id does not match caller")
        if tenant_id is not None and profile.tenant_id not in {None, tenant_id}:
            raise ModelProfileAccessError("model profile tenant_id does not match caller")
        if not profile.encrypted_api_key:
            raise ModelProfileNotFoundError(f"model profile secret was revoked: {profile_id}")
        return profile

    def get_active_profile_by_display_name(
        self,
        *,
        guild_id: str,
        user_id: str,
        display_name: str,
        usage: ModelUsage | None = None,
    ) -> UserModelProfile:
        statement = select(UserModelProfile).where(
            UserModelProfile.guild_id == guild_id,
            UserModelProfile.user_id == user_id,
            UserModelProfile.display_name == display_name,
            UserModelProfile.status == "active",
        )
        if usage is not None:
            statement = statement.where(UserModelProfile.usage == usage)
        profile = self.session.execute(statement).scalar_one_or_none()
        if profile is None or not profile.encrypted_api_key:
            raise ModelProfileNotFoundError(f"model profile not found: {display_name}")
        return profile

    def get_default_profile(
        self,
        *,
        guild_id: str,
        user_id: str,
        usage: ModelUsage = "chat",
    ) -> UserModelProfile:
        profile = self.session.execute(
            select(UserModelProfile).where(
                UserModelProfile.guild_id == guild_id,
                UserModelProfile.user_id == user_id,
                UserModelProfile.usage == usage,
                UserModelProfile.status == "active",
                UserModelProfile.is_default.is_(True),
            )
        ).scalar_one_or_none()
        if profile is None:
            raise ModelProfileNotFoundError("default model profile not found")
        return profile

    def set_default(
        self,
        *,
        profile_id: UUID,
        guild_id: str,
        user_id: str,
        usage: ModelUsage = "chat",
    ) -> ModelProfileSummary:
        profile = self.get_active_profile(
            profile_id=profile_id,
            guild_id=guild_id,
            user_id=user_id,
            usage=usage,
        )
        self._clear_default(guild_id=guild_id, user_id=user_id, usage=usage)
        profile.is_default = True
        profile.updated_at = datetime.now(UTC)
        self.session.flush()
        return model_profile_summary(profile)

    def revoke(
        self,
        *,
        profile_id: UUID,
        guild_id: str,
        user_id: str,
    ) -> ModelProfileSummary:
        profile = self.get_active_profile(
            profile_id=profile_id,
            guild_id=guild_id,
            user_id=user_id,
            usage=None,
        )
        profile.status = "revoked"
        profile.is_default = False
        profile.encrypted_api_key = None
        profile.updated_at = datetime.now(UTC)
        self.session.flush()
        return model_profile_summary(profile)

    def mark_test_result(self, *, profile_id: UUID, status: str) -> None:
        self.session.execute(
            update(UserModelProfile)
            .where(UserModelProfile.id == profile_id)
            .values(last_test_status=status, updated_at=datetime.now(UTC))
        )
        self.session.flush()

    def mark_used(self, *, profile_id: UUID) -> None:
        self.session.execute(
            update(UserModelProfile)
            .where(UserModelProfile.id == profile_id)
            .values(last_used_at=datetime.now(UTC), updated_at=datetime.now(UTC))
        )
        self.session.flush()

    def _has_active_profile(self, *, guild_id: str, user_id: str, usage: ModelUsage) -> bool:
        return (
            self.session.execute(
                select(UserModelProfile.id).where(
                    UserModelProfile.guild_id == guild_id,
                    UserModelProfile.user_id == user_id,
                    UserModelProfile.usage == usage,
                    UserModelProfile.status == "active",
                )
            ).first()
            is not None
        )

    def _clear_default(self, *, guild_id: str, user_id: str, usage: ModelUsage) -> None:
        self.session.execute(
            update(UserModelProfile)
            .where(
                UserModelProfile.guild_id == guild_id,
                UserModelProfile.user_id == user_id,
                UserModelProfile.usage == usage,
            )
            .values(is_default=False, updated_at=datetime.now(UTC))
        )


def model_profile_summary(profile: UserModelProfile) -> ModelProfileSummary:
    return ModelProfileSummary(
        id=profile.id,
        tenant_id=profile.tenant_id,
        guild_id=profile.guild_id,
        user_id=profile.user_id,
        provider=profile.provider,
        model_name=profile.model_name,
        display_name=profile.display_name,
        base_url=profile.base_url,
        usage=profile.usage,
        status=profile.status,
        is_default=profile.is_default,
        last_test_status=profile.last_test_status,
        last_used_at=profile.last_used_at,
        created_at=profile.created_at,
        updated_at=profile.updated_at,
    )
