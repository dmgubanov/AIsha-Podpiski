# PURPOSE: Dataclass-модели для трекинг-бота
# MODULE_MAP: TrackingClick, MaxTrackingClick, Channel
# DEPENDS_ON: []
# USED_BY: [database.repository, handlers, services]

from dataclasses import dataclass
from typing import Optional


# START_CLASS: Channel
@dataclass
class Channel:
    """Канал (Telegram или MAX) с настройками Метрики."""
    id: Optional[int]
    platform: str  # 'telegram' или 'max'
    channel_id: str  # chat_id
    name: str = ""
    metrika_counter_id: Optional[str] = None
    metrika_token: Optional[str] = None

    @classmethod
    def from_row(cls, row: tuple) -> "Channel":
        """Создаёт Channel из строки БД."""
        return cls(
            id=row[0],
            platform=row[1],
            channel_id=row[2],
            name=row[3] if len(row) > 3 else "",
            metrika_counter_id=row[4] if len(row) > 4 else None,
            metrika_token=row[5] if len(row) > 5 else None,
        )
# END_CLASS


# START_CLASS: TrackingClick
@dataclass
class TrackingClick:
    """Запись клика для трекинга конверсий подписок через invite-ссылки."""
    id: Optional[int]
    client_id: str
    invite_link: str
    channel_id: str
    subscribed_user_id: Optional[int] = None
    conversion_sent: bool = False
    created_at: Optional[str] = None
    subscribed_at: Optional[str] = None

    @classmethod
    def from_row(cls, row: tuple) -> "TrackingClick":
        """Создаёт TrackingClick из строки БД."""
        return cls(
            id=row[0],
            client_id=row[1],
            invite_link=row[2],
            channel_id=row[3],
            subscribed_user_id=row[4],
            conversion_sent=bool(row[5]) if row[5] is not None else False,
            created_at=row[6] if len(row) > 6 else None,
            subscribed_at=row[7] if len(row) > 7 else None,
        )
# END_CLASS


# START_CLASS: MaxTrackingClick
@dataclass
class MaxTrackingClick:
    """Запись клика для трекинга конверсий подписок MAX по корреляции времени."""
    id: Optional[int]
    client_id: str
    channel_id: str
    max_user_id: Optional[int] = None
    conversion_sent: bool = False
    created_at: Optional[str] = None
    matched_at: Optional[str] = None

    @classmethod
    def from_row(cls, row: tuple) -> "MaxTrackingClick":
        """Создаёт MaxTrackingClick из строки БД."""
        return cls(
            id=row[0],
            client_id=row[1],
            channel_id=row[2],
            max_user_id=row[3],
            conversion_sent=bool(row[4]) if row[4] is not None else False,
            created_at=row[5] if len(row) > 5 else None,
            matched_at=row[6] if len(row) > 6 else None,
        )
# END_CLASS
