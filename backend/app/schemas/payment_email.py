from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.payment_email import ProcessingStatus


class PaymentEmailSummary(BaseModel):
    id: int
    payment_account_id: int
    provider_id: int
    provider_name: str
    friendly_name: str
    receiver_tag: str | None
    gmail_uid: int
    gmail_message_id: str | None
    sender_address: str | None
    subject: str | None
    received_at: datetime | None
    processing_status: ProcessingStatus
    parser_key: str | None
    parser_version: str | None
    created_at: datetime


class PaymentEmailDetail(PaymentEmailSummary):
    mailbox: str
    raw_text: str | None
    raw_html: str | None
    raw_headers_json: dict | None
    parsed_payload_json: dict | None
    parsed_at: datetime | None
    processing_error: str | None
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ListenerStatusResponse(BaseModel):
    worker_heartbeat: str | None
    last_heartbeat_at: datetime | None
    enabled_account_count: int
    connected_account_count: int
    error_account_count: int
    latest_captured_email_time: datetime | None


class ParserActionResponse(BaseModel):
    processing_status: ProcessingStatus
    parser_key: str | None
    parser_version: str | None
    parsed_payload_json: dict | None
    parsed_at: datetime | None
    processing_error: str | None


class ParserInspectionResponse(BaseModel):
    normalized_subject: str
    normalized_plain_text: str
    visible_text_from_html: str
    detected_dollar_prefixed_tags: list[str]
    detected_monetary_amounts: list[int]
    detected_date_time_candidates: list[str]
    detected_sender_name_candidates: list[str]
    gmail_message_id: str | None
    provider_name: str
    parser_key: str
    parser_version: str
    parsed_result: dict | None
