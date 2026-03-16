# PURPOSE: Шифрование/дешифрование токенов Метрики при хранении в БД
# MODULE_MAP: TokenCipher
# DEPENDS_ON: [cryptography.fernet, config]
# USED_BY: [handlers.admin, handlers.channel_events]

from cryptography.fernet import Fernet, InvalidToken
from src.config import Config


class TokenCipher:
    PREFIX = "enc:v1:"

    def __init__(self):
        self._fernet = Fernet(Config.ACCESS_TOKEN_ENCRYPTION_KEY.encode("utf-8"))

    def is_encrypted(self, value: str | None) -> bool:
        return bool(value and value.startswith(self.PREFIX))

    def encrypt(self, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            return normalized
        if self.is_encrypted(normalized):
            return normalized
        token = self._fernet.encrypt(normalized.encode("utf-8")).decode("utf-8")
        return f"{self.PREFIX}{token}"

    def decrypt(self, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            return normalized
        if not self.is_encrypted(normalized):
            return normalized
        payload = normalized[len(self.PREFIX):]
        try:
            return self._fernet.decrypt(payload.encode("utf-8")).decode("utf-8")
        except InvalidToken:
            return value
