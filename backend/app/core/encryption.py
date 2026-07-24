from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings


class EncryptionConfigurationError(RuntimeError):
    pass


def get_fernet() -> Fernet:
    if not settings.app_encryption_key:
        raise EncryptionConfigurationError("APP_ENCRYPTION_KEY must be set.")

    try:
        return Fernet(settings.app_encryption_key.encode("utf-8"))
    except ValueError as exc:
        raise EncryptionConfigurationError(
            "APP_ENCRYPTION_KEY must be a valid Fernet key. Generate one with: "
            "python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
        ) from exc


def validate_encryption_key() -> None:
    get_fernet()


def encrypt_secret(plaintext: str) -> str:
    return get_fernet().encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt_secret(ciphertext: str) -> str:
    try:
        return get_fernet().decrypt(ciphertext.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError("Encrypted value cannot be decrypted with APP_ENCRYPTION_KEY.") from exc
