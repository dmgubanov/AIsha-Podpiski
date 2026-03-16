# PURPOSE: Отправка событий в Яндекс Метрику через Measurement Protocol
# MODULE_MAP: MetrikaService
# DEPENDS_ON: [config, httpx]
# USED_BY: [handlers.channel_events, services.max_updates_service]

# START_IMPORTS
import logging
import time
import traceback

import httpx

from src.config import Config
# END_IMPORTS

logger = logging.getLogger(__name__)

COLLECT_URL = "https://mc.yandex.ru/collect"
COLLECT_TIMEOUT_SECONDS = 10


# START_CLASS: MetrikaService
class MetrikaService:
    """Отправка событий в Яндекс Метрику через Measurement Protocol."""

    @staticmethod
    async def send_event(
        client_id: str,
        event_name: str = "",
        page_url: str = "",
        counter_id: str = "",
        mp_token: str = "",
    ) -> bool:
        """Отправляет событие в Яндекс Метрику.

        # CONTRACT:
        IN: client_id (обязательный), event_name, counter_id/mp_token (per-channel)
        OUT: True если запрос прошёл успешно
        SIDE_EFFECTS: POST-запрос к mc.yandex.ru/collect
        """
        tid = counter_id or Config.YANDEX_METRIKA_COUNTER_ID
        ms = mp_token or Config.YANDEX_METRIKA_MP_TOKEN

        if not tid or not ms:
            logger.debug("[SKIP] Метрика MP не настроена, событие не отправлено")
            return False

        if not client_id:
            logger.debug("[SKIP] Нет client_id — событие не отправлено")
            return False

        event = event_name or Config.YANDEX_METRIKA_GOAL_NAME
        ts = int(time.time())

        params = {
            "tid": tid,
            "cid": client_id,
            "ms": ms,
            "t": "event",
            "ea": event,
            "et": str(ts),
        }
        if page_url:
            params["dl"] = page_url

        logger.info(
            f"[START] Отправка события в Метрику MP. "
            f"client_id={client_id}, event={event}, counter={tid}"
        )

        try:
            async with httpx.AsyncClient(timeout=COLLECT_TIMEOUT_SECONDS) as http:
                response = await http.post(COLLECT_URL, data=params)

            if response.status_code == 200:
                logger.info(f"[STATE] Событие отправлено в Метрику. client_id={client_id}")
                return True

            logger.warning(
                f"[WARN] Метрика MP вернула {response.status_code}. "
                f"client_id={client_id}, body={response.text[:300]}"
            )
            return False

        except Exception as e:
            logger.error(
                f"[ERROR] Ошибка отправки события в Метрику MP. "
                f"client_id={client_id}: {e}\n{traceback.format_exc()}"
            )
            return False
# END_CLASS
