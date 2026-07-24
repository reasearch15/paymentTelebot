from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.auth import require_admin
from app.db.session import get_db
from app.models.payment_account import PaymentAccount
from app.models.payment_email import PaymentEmail, ProcessingStatus
from app.models.provider import Provider
from app.schemas.payment_email import (
    ParserActionResponse,
    ParserInspectionResponse,
    PaymentEmailDetail,
    PaymentEmailSummary,
)
from app.services.payment_email_parser import get_email_for_parsing, inspect_payment_email, parse_payment_email_safely

router = APIRouter(prefix="/payment-emails", tags=["payment emails"], dependencies=[Depends(require_admin)])


def serialize_summary(email: PaymentEmail) -> PaymentEmailSummary:
    account = email.payment_account
    provider = account.provider
    parsed_receiver_tag = (email.parsed_payload_json or {}).get("receiver_tag")
    return PaymentEmailSummary(
        id=email.id,
        payment_account_id=email.payment_account_id,
        provider_id=account.provider_id,
        provider_name=provider.name,
        friendly_name=account.friendly_name,
        receiver_tag=parsed_receiver_tag or account.receiver_tag,
        gmail_uid=email.gmail_uid,
        gmail_message_id=email.gmail_message_id,
        sender_address=email.sender_address,
        subject=email.subject,
        received_at=email.received_at,
        processing_status=email.processing_status,
        parser_key=email.parser_key,
        parser_version=email.parser_version,
        created_at=email.created_at,
    )


def apply_search(query: Select[tuple[PaymentEmail]], search: str | None) -> Select[tuple[PaymentEmail]]:
    if not search:
        return query
    term = f"%{search.strip()}%"
    return query.where(
        or_(
            PaymentEmail.subject.ilike(term),
            PaymentEmail.sender_address.ilike(term),
            PaymentEmail.raw_text.ilike(term),
            PaymentAccount.receiver_tag.ilike(term),
            PaymentAccount.friendly_name.ilike(term),
        )
    )


@router.get("", response_model=list[PaymentEmailSummary])
async def list_payment_emails(
    payment_account_id: int | None = None,
    provider_id: int | None = None,
    processing_status: ProcessingStatus | None = None,
    search: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> list[PaymentEmailSummary]:
    query = (
        select(PaymentEmail)
        .join(PaymentEmail.payment_account)
        .join(PaymentAccount.provider)
        .options(selectinload(PaymentEmail.payment_account).selectinload(PaymentAccount.provider))
        .order_by(func.coalesce(PaymentEmail.received_at, PaymentEmail.created_at).desc())
        .limit(limit)
        .offset(offset)
    )

    if payment_account_id is not None:
        query = query.where(PaymentEmail.payment_account_id == payment_account_id)
    if provider_id is not None:
        query = query.where(Provider.id == provider_id)
    if processing_status is not None:
        query = query.where(PaymentEmail.processing_status == processing_status)
    query = apply_search(query, search)

    result = await db.execute(query)
    return [serialize_summary(email) for email in result.scalars().all()]


@router.get("/{email_id}", response_model=PaymentEmailDetail)
async def get_payment_email(email_id: int, db: AsyncSession = Depends(get_db)) -> PaymentEmailDetail:
    result = await db.execute(
        select(PaymentEmail)
        .options(selectinload(PaymentEmail.payment_account).selectinload(PaymentAccount.provider))
        .where(PaymentEmail.id == email_id)
    )
    email = result.scalar_one_or_none()
    if email is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Payment email not found.")

    summary = serialize_summary(email)
    return PaymentEmailDetail(
        **summary.model_dump(),
        mailbox=email.mailbox,
        raw_text=email.raw_text,
        raw_html=email.raw_html,
        raw_headers_json=email.raw_headers_json,
        parsed_payload_json=email.parsed_payload_json,
        parsed_at=email.parsed_at,
        processing_error=email.processing_error,
        updated_at=email.updated_at,
    )


def serialize_parser_action(email: PaymentEmail) -> ParserActionResponse:
    return ParserActionResponse(
        processing_status=email.processing_status,
        parser_key=email.parser_key,
        parser_version=email.parser_version,
        parsed_payload_json=email.parsed_payload_json,
        parsed_at=email.parsed_at,
        processing_error=email.processing_error,
    )


@router.post("/{email_id}/parse", response_model=ParserActionResponse)
async def parse_email(email_id: int, db: AsyncSession = Depends(get_db)) -> ParserActionResponse:
    email = await get_email_for_parsing(db, email_id)
    if email is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Payment email not found.")
    await parse_payment_email_safely(db, email)
    return serialize_parser_action(email)


@router.post("/{email_id}/reparse", response_model=ParserActionResponse)
async def reparse_email(email_id: int, db: AsyncSession = Depends(get_db)) -> ParserActionResponse:
    email = await get_email_for_parsing(db, email_id)
    if email is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Payment email not found.")
    await parse_payment_email_safely(db, email)
    return serialize_parser_action(email)


@router.get("/{email_id}/parser-inspection", response_model=ParserInspectionResponse)
async def get_parser_inspection(email_id: int, db: AsyncSession = Depends(get_db)) -> ParserInspectionResponse:
    email = await get_email_for_parsing(db, email_id)
    if email is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Payment email not found.")
    return ParserInspectionResponse(**inspect_payment_email(email).to_json())
