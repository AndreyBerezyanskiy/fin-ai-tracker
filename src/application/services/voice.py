from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from infrastructure.database import session_scope
from infrastructure.repositories import FeatureFlagRepository

VOICE_FEATURE = "voice_input"


@dataclass(frozen=True, slots=True)
class VoiceFeatureStatus:
    configured: bool
    admin_enabled: bool

    @property
    def enabled(self) -> bool:
        return self.configured and self.admin_enabled


class VoiceFeatureService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        configured: bool,
    ) -> None:
        self.session_factory = session_factory
        self.configured = configured

    async def status(self) -> VoiceFeatureStatus:
        async with self.session_factory() as session:
            admin_enabled = await FeatureFlagRepository(session).is_enabled(
                VOICE_FEATURE, default=True
            )
        return VoiceFeatureStatus(
            configured=self.configured,
            admin_enabled=admin_enabled,
        )

    async def set_admin_enabled(self, *, enabled: bool) -> VoiceFeatureStatus:
        async with session_scope(self.session_factory) as session:
            await FeatureFlagRepository(session).set_enabled(
                VOICE_FEATURE, enabled=enabled
            )
        return VoiceFeatureStatus(configured=self.configured, admin_enabled=enabled)

    async def toggle(self) -> VoiceFeatureStatus:
        current = await self.status()
        return await self.set_admin_enabled(enabled=not current.admin_enabled)


__all__ = ["VOICE_FEATURE", "VoiceFeatureService", "VoiceFeatureStatus"]
