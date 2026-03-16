# PURPOSE: Long-poll MAX updates и трекинг конверсий подписок MAX
# MODULE_MAP: MaxUpdatesService
# DEPENDS_ON: [config, database.repository, services.metrika_service]
# USED_BY: [main]

import asyncio
import logging
import traceback
from typing import Any, Dict, List, Optional

import requests

from src.config import Config
from src.database.repository import Repository
from src.services.metrika_service import MetrikaService


logger = logging.getLogger(__name__)


# START_CLASS: MaxUpdatesService
class MaxUpdatesService:
    """Long-poll worker для MAX updates: трекинг подписок."""

    def __init__(self, telegram_bot):
        self._bot = telegram_bot
        self._api_base = Config.MAX_API_BASE_URL.rstrip("/")
        self._token = (Config.MAX_BOT_TOKEN or "").strip()

    # START_FUNCTION: poll_once
    async def poll_once(self):
        """Один цикл long-poll для обработки MAX-событий."""
        if not Config.MAX_AUTO_CONNECT_ENABLED:
            return
        if not self._token:
            return

        marker = await Repository.get_max_updates_marker()
        payload = await self._fetch_updates(marker=marker)
        if payload is None:
            return

        updates = self._extract_updates(payload)
        next_marker = self._extract_marker(payload) or marker
        if next_marker != marker:
            await Repository.set_max_updates_marker(next_marker)

        if not updates:
            return

        for update in updates:
            event_type = self._extract_event_type(update)

            if event_type == "user_added":
                await self._handle_user_added(update)
    # END_FUNCTION

    # START_FUNCTION: _handle_user_added
    async def _handle_user_added(self, update: Dict[str, Any]) -> None:
        """Обрабатывает событие user_added для трекинга конверсий подписок MAX."""
        chat_id = self._extract_chat_id(update)
        user_id = self._extract_user_id(update)

        if chat_id is None or user_id is None:
            logger.debug(
                "[MAX-TRACK] user_added без chat_id или user_id: %s", update
            )
            return

        user_obj = update.get("user") or {}
        if user_obj.get("is_bot"):
            return

        logger.info(
            f"[START] MAX user_added. chat_id={chat_id}, user_id={user_id}"
        )

        try:
            click = await Repository.find_unmatched_max_tracking_click(
                channel_id=str(chat_id),
                max_age_minutes=Config.TRACKING_MAX_MATCH_WINDOW_MINUTES,
            )

            if not click:
                logger.debug(
                    f"[SKIP] Нет незаматченных кликов для MAX chat_id={chat_id}"
                )
                return

            matched = await Repository.mark_max_tracking_subscription(
                click_id=click.id,
                max_user_id=user_id,
            )
            if not matched:
                logger.debug(
                    f"[SKIP] Не удалось заматчить клик id={click.id} с user_id={user_id}"
                )
                return

            logger.info(
                f"[STATE] MAX подписка заматчена. click_id={click.id}, "
                f"client_id={click.client_id}, max_user_id={user_id}"
            )

            asyncio.create_task(
                self._delayed_max_conversion_check(
                    click_id=click.id,
                    client_id=click.client_id,
                    channel_id=str(chat_id),
                    max_user_id=user_id,
                    delay_seconds=Config.TRACKING_CONVERSION_DELAY_SECONDS,
                )
            )

        except Exception as e:
            logger.error(
                f"[ERROR] Ошибка обработки MAX user_added. "
                f"chat_id={chat_id}, user_id={user_id}: {e}\n{traceback.format_exc()}"
            )
    # END_FUNCTION

    # START_FUNCTION: _delayed_max_conversion_check
    async def _delayed_max_conversion_check(
        self,
        click_id: int,
        client_id: str,
        channel_id: str,
        max_user_id: int,
        delay_seconds: int,
    ) -> None:
        """Отложенная проверка: пользователь всё ещё подписан → отправка конверсии."""
        logger.info(
            f"[START] Отложенная проверка MAX конверсии через {delay_seconds} сек. "
            f"click_id={click_id}, max_user_id={max_user_id}"
        )

        await asyncio.sleep(delay_seconds)

        try:
            still_member = await self._check_max_membership(
                chat_id=int(channel_id),
                user_id=max_user_id,
            )

            if not still_member:
                logger.info(
                    f"[STATE] MAX пользователь отписался до проверки — конверсия отклонена. "
                    f"max_user_id={max_user_id}, channel_id={channel_id}"
                )
                return

            updated = await Repository.mark_max_tracking_conversion(click_id)
            if not updated:
                logger.debug(
                    f"[SKIP] MAX конверсия уже отправлена или запись не найдена: click_id={click_id}"
                )
                return

            # Загружаем per-channel настройки Метрики
            project_counter_id = ""
            project_mp_token = ""
            try:
                channel = await Repository.get_channel_metrika_by_channel_id(channel_id, "max")
                if channel and channel.metrika_counter_id and channel.metrika_token:
                    from src.utils.crypto import TokenCipher
                    cipher = TokenCipher()
                    project_counter_id = channel.metrika_counter_id
                    project_mp_token = cipher.decrypt(channel.metrika_token) or ""
                    logger.debug(
                        f"[STATE] Используются per-channel настройки Метрики для MAX. "
                        f"channel_db_id={channel.id}, counter={project_counter_id}"
                    )
            except Exception as e:
                logger.warning(f"[WARN] Не удалось загрузить per-channel Метрику для MAX: {e}")

            success = await MetrikaService.send_event(
                client_id=client_id,
                event_name=Config.TRACKING_MAX_METRIKA_GOAL_NAME,
                counter_id=project_counter_id,
                mp_token=project_mp_token,
            )

            if success:
                logger.info(
                    f"[STATE] MAX конверсия отправлена в Метрику. "
                    f"client_id={client_id}, max_user_id={max_user_id}"
                )
            else:
                logger.warning(
                    f"[WARN] Не удалось отправить MAX конверсию в Метрику. "
                    f"client_id={client_id}, max_user_id={max_user_id}"
                )

        except Exception as e:
            logger.error(
                f"[ERROR] Ошибка отложенной проверки MAX конверсии. "
                f"click_id={click_id}, max_user_id={max_user_id}: {e}\n{traceback.format_exc()}"
            )
    # END_FUNCTION

    # START_FUNCTION: _check_max_membership
    async def _check_max_membership(self, chat_id: int, user_id: int) -> bool:
        """Проверяет, является ли пользователь участником MAX-канала."""
        url = f"{self._api_base}/chats/{chat_id}/members"
        params = {"user_ids": str(user_id), "count": 1}
        payload = await self._request_json("GET", url, params=params, timeout=15)

        if payload is None:
            logger.warning(
                f"[WARN] Не удалось проверить членство MAX. "
                f"chat_id={chat_id}, user_id={user_id}. Считаем подписанным."
            )
            return True

        members = payload.get("members") or payload.get("participants") or []
        if isinstance(members, list):
            for member in members:
                member_user = member.get("user") or member
                member_id = member_user.get("user_id")
                try:
                    if member_id is not None and int(member_id) == user_id:
                        return True
                except (TypeError, ValueError):
                    continue

        if not members and payload.get("marker") is None:
            return False

        return True
    # END_FUNCTION

    # --- API methods ---

    async def _fetch_updates(self, marker: Optional[str]) -> Optional[Dict[str, Any]]:
        """Получает обновления из MAX API."""
        params: Dict[str, Any] = {
            "limit": Config.MAX_UPDATES_LIMIT,
            "timeout": Config.MAX_UPDATES_TIMEOUT_SECONDS,
            "types": "user_added",
        }
        if marker:
            params["marker"] = marker
        url = f"{self._api_base}/updates"
        return await self._request_json("GET", url, params=params, timeout=Config.MAX_UPDATES_TIMEOUT_SECONDS + 10)

    async def _request_json(
        self,
        method: str,
        url: str,
        params: Optional[Dict[str, Any]] = None,
        timeout: int = 20,
    ) -> Optional[Dict[str, Any]]:
        """Выполняет HTTP-запрос к MAX API."""
        if not self._token:
            return None

        headers_variants = self._auth_header_variants(self._token)
        last_error = None
        for headers in headers_variants:
            try:
                response = await asyncio.to_thread(
                    requests.request,
                    method,
                    url,
                    params=params,
                    headers=headers,
                    timeout=timeout,
                )
                if response.status_code in (401, 403):
                    last_error = f"auth_{response.status_code}"
                    continue
                if response.status_code >= 400:
                    logger.warning(
                        "[MAX-API] Ошибка %s для %s %s: %s",
                        response.status_code, method, url, response.text[:300],
                    )
                    return None
                data = response.json()
                if isinstance(data, dict):
                    return data
                if isinstance(data, list):
                    return {"updates": data}
                return None
            except Exception as exc:
                last_error = str(exc)
                continue

        if last_error:
            logger.warning("[MAX-API] Запрос неудачен: %s %s: %s", method, url, last_error)
        return None

    # --- Static helpers ---

    @staticmethod
    def _auth_header_variants(token: str) -> List[Dict[str, str]]:
        raw = token.strip()
        variants: List[Dict[str, str]] = [{"Authorization": raw, "Content-Type": "application/json"}]
        if raw and not raw.lower().startswith("bearer "):
            variants.append({"Authorization": f"Bearer {raw}", "Content-Type": "application/json"})
        return variants

    @staticmethod
    def _extract_updates(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        for key in ("updates", "items", "results"):
            val = payload.get(key)
            if isinstance(val, list):
                return [item for item in val if isinstance(item, dict)]
        return []

    @staticmethod
    def _extract_marker(payload: Dict[str, Any]) -> Optional[str]:
        for key in ("marker", "next_marker", "continuation_marker", "next"):
            marker = payload.get(key)
            if isinstance(marker, str) and marker.strip():
                return marker.strip()
        return None

    @staticmethod
    def _extract_event_type(update: Dict[str, Any]) -> Optional[str]:
        for key in ("update_type", "type", "event_type"):
            value = update.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip().lower()
        if isinstance(update.get("event"), dict):
            event = update["event"]
            value = event.get("type") or event.get("update_type")
            if isinstance(value, str) and value.strip():
                return value.strip().lower()
        return None

    @staticmethod
    def _extract_chat_id(update: Dict[str, Any]) -> Optional[int]:
        candidates: List[Any] = [
            update.get("chat_id"),
            (update.get("chat") or {}).get("chat_id") if isinstance(update.get("chat"), dict) else None,
            (update.get("event") or {}).get("chat_id") if isinstance(update.get("event"), dict) else None,
            ((update.get("message") or {}).get("recipient") or {}).get("chat_id")
            if isinstance(update.get("message"), dict) else None,
        ]
        for item in candidates:
            try:
                if item is None:
                    continue
                return int(item)
            except (TypeError, ValueError):
                continue
        return None

    @staticmethod
    def _extract_user_id(update: Dict[str, Any]) -> Optional[int]:
        """Извлекает user_id из user_added события MAX."""
        candidates: List[Any] = [
            update.get("user_id"),
            (update.get("user") or {}).get("user_id") if isinstance(update.get("user"), dict) else None,
            (update.get("event") or {}).get("user_id") if isinstance(update.get("event"), dict) else None,
        ]
        for item in candidates:
            try:
                if item is None:
                    continue
                return int(item)
            except (TypeError, ValueError):
                continue
        return None
# END_CLASS
