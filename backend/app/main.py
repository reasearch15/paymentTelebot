from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.auth import router as auth_router
from app.api.listener import router as listener_router
from app.api.payment_accounts import router as payment_accounts_router
from app.api.payment_emails import router as payment_emails_router
from app.api.providers import router as providers_router
from app.api.settlements import router as settlements_router
from app.api.telegram import router as telegram_router
from app.api.transactions import router as transactions_router
from app.core.config import settings
from app.core.encryption import validate_encryption_key

app = FastAPI(title="Payment Ledger API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.backend_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(auth_router)
app.include_router(providers_router)
app.include_router(payment_accounts_router)
app.include_router(payment_emails_router)
app.include_router(transactions_router)
app.include_router(settlements_router)
app.include_router(listener_router)
app.include_router(telegram_router)


@app.on_event("startup")
async def validate_required_configuration() -> None:
    validate_encryption_key()
