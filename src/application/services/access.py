from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from infrastructure.database import AccessGrant, AccessRequest, session_scope
from infrastructure.repositories import AccessRepository

INVITATION_LIFETIME = timedelta(hours=24)


@dataclass(frozen=True, slots=True)
class InvitationLink:
    token: str
    target_type: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class RedemptionResult:
    status: str
    grant: AccessGrant | None = None


class AccessService:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    async def is_allowed(self, telegram_chat_id: int) -> bool:
        async with self.session_factory() as session:
            return await AccessRepository(session).is_allowed(telegram_chat_id)

    async def create_invitation(
        self, *, created_by_user_id: int, target_type: str
    ) -> InvitationLink:
        if target_type not in {"private", "group"}:
            raise ValueError("unsupported invitation target")
        token = f"invite_{secrets.token_urlsafe(18)}"
        expires_at = datetime.now(UTC) + INVITATION_LIFETIME
        async with session_scope(self.session_factory) as session:
            await AccessRepository(session).create_invitation(
                token_digest=self._digest(token),
                target_type=target_type,
                created_by_user_id=created_by_user_id,
                expires_at=expires_at,
            )
        return InvitationLink(token=token, target_type=target_type, expires_at=expires_at)

    async def redeem_invitation(
        self,
        *,
        token: str,
        telegram_chat_id: int,
        chat_type: str,
        title: str,
        username: str | None,
    ) -> RedemptionResult:
        actual_target = "private" if chat_type == "private" else "group"
        async with session_scope(self.session_factory) as session:
            repository = AccessRepository(session)
            invitation = await repository.get_invitation_for_update(self._digest(token))
            if invitation is None:
                return RedemptionResult("invalid")
            if invitation.used_at is not None:
                return RedemptionResult("used")
            if invitation.expires_at <= datetime.now(UTC):
                return RedemptionResult("expired")
            if invitation.target_type != actual_target:
                return RedemptionResult("wrong_target")
            grant = await repository.grant(
                telegram_chat_id=telegram_chat_id,
                chat_type=chat_type,
                title=title,
                username=username,
                granted_by_user_id=invitation.created_by_user_id,
            )
            invitation.used_at = datetime.now(UTC)
            invitation.used_chat_id = telegram_chat_id
            await session.flush()
            return RedemptionResult("granted", grant)

    async def request_access(
        self,
        *,
        telegram_chat_id: int,
        requester_user_id: int,
        chat_type: str,
        title: str,
        username: str | None,
    ) -> AccessRequest:
        async with session_scope(self.session_factory) as session:
            return await AccessRepository(session).request_access(
                telegram_chat_id=telegram_chat_id,
                requester_user_id=requester_user_id,
                chat_type=chat_type,
                title=title,
                username=username,
            )

    async def approve_request(
        self, *, request_id: int, reviewed_by_user_id: int
    ) -> tuple[AccessRequest, AccessGrant] | None:
        async with session_scope(self.session_factory) as session:
            repository = AccessRepository(session)
            request = await repository.get_request_for_update(request_id)
            if request is None or request.status != "pending":
                return None
            grant = await repository.grant(
                telegram_chat_id=request.telegram_chat_id,
                chat_type=request.chat_type,
                title=request.title,
                username=request.username,
                granted_by_user_id=reviewed_by_user_id,
            )
            request.status = "approved"
            request.reviewed_by_user_id = reviewed_by_user_id
            await session.flush()
            return request, grant

    async def reject_request(
        self, *, request_id: int, reviewed_by_user_id: int
    ) -> AccessRequest | None:
        async with session_scope(self.session_factory) as session:
            request = await AccessRepository(session).get_request_for_update(request_id)
            if request is None or request.status != "pending":
                return None
            request.status = "rejected"
            request.reviewed_by_user_id = reviewed_by_user_id
            await session.flush()
            return request

    async def list_pending_requests(self) -> tuple[AccessRequest, ...]:
        async with self.session_factory() as session:
            return tuple(await AccessRepository(session).list_pending_requests())

    async def list_active_grants(self) -> tuple[AccessGrant, ...]:
        async with self.session_factory() as session:
            return tuple(await AccessRepository(session).list_active_grants())

    async def revoke(self, telegram_chat_id: int) -> AccessGrant | None:
        async with session_scope(self.session_factory) as session:
            return await AccessRepository(session).revoke(telegram_chat_id)

    @staticmethod
    def _digest(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()


__all__ = ["AccessService", "InvitationLink", "RedemptionResult"]
