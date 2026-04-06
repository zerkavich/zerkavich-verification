import os
import json
import aiohttp
import logging

logger = logging.getLogger(__name__)

PTERODACTYL_URL = os.getenv("PTERODACTYL_URL", "https://my.aurorix.net")
PTERODACTYL_KEY = os.getenv("PTERODACTYL_KEY")
SERVER_ID       = os.getenv("SERVER_ID", "6daf8160-16ab-4a5b-ac25-3e35cb75a3d4")
ADMIN_IDS_RAW   = os.getenv("ADMIN_TG_IDS", "")
APPEAL_URL      = os.getenv("APPEAL_URL", "@zerkavich")

# Путь к pfids.json на сервере (относительно /home/container/)
# pfid_bridge.py пишет туда при каждом входе игрока
PFIDS_PATH      = os.getenv("PFIDS_PATH", "/home/container/pfids.json")


def get_admin_ids() -> set[int]:
    ids = set()
    for part in ADMIN_IDS_RAW.split(","):
        part = part.strip()
        if part.isdigit():
            ids.add(int(part))
    return ids


def is_admin(tg_id: int) -> bool:
    admins = get_admin_ids()
    if not admins:
        return False
    return tg_id in admins


# ─── Pterodactyl: отправка команды ───────────────────────────────────────────

async def send_server_command(command: str) -> tuple[bool, str]:
    """Отправляет команду на сервер через Pterodactyl API."""
    url = f"{PTERODACTYL_URL}/api/client/servers/{SERVER_ID}/command"
    headers = {
        "Authorization": f"Bearer {PTERODACTYL_KEY}",
        "Content-Type":  "application/json",
        "Accept":        "application/json",
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url, json={"command": command}, headers=headers,
                timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status in (200, 204):
                    return True, "OK"
                text = await resp.text()
                return False, f"HTTP {resp.status}: {text[:200]}"
    except Exception as e:
        return False, str(e)


# ─── Pterodactyl: чтение файла с сервера ─────────────────────────────────────

async def read_server_file(path: str) -> str | None:
    """
    Читает содержимое файла на сервере через Pterodactyl File API.
    path — абсолютный путь на сервере, напр. /home/container/pfids.json
    """
    url = f"{PTERODACTYL_URL}/api/client/servers/{SERVER_ID}/files/contents"
    headers = {
        "Authorization": f"Bearer {PTERODACTYL_KEY}",
        "Accept":        "application/json",
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url, params={"file": path}, headers=headers,
                timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status == 200:
                    return await resp.text()
                logger.warning(f"[pfid] read_server_file {path}: HTTP {resp.status}")
                return None
    except Exception as e:
        logger.warning(f"[pfid] read_server_file error: {e}")
        return None


# ─── pfids.json: поиск игрока ────────────────────────────────────────────────

async def fetch_pfids() -> dict:
    """
    Загружает pfids.json с сервера.
    Формат: { "PlayerName": { "name": "...", "xuid": "...", "pfid": "...", "updated": "..." } }
    Возвращает пустой dict при ошибке.
    """
    raw = await read_server_file(PFIDS_PATH)
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except Exception as e:
        logger.warning(f"[pfid] JSON parse error: {e}")
        return {}


async def lookup_player(name: str) -> dict | None:
    """
    Ищет игрока по MC-нику в pfids.json.
    Возвращает { name, xuid, pfid } или None.
    """
    pfids = await fetch_pfids()
    # Точное совпадение
    if name in pfids:
        return pfids[name]
    # Регистронезависимое совпадение
    name_lower = name.lower()
    for key, val in pfids.items():
        if key.lower() == name_lower:
            return val
    return None


async def search_players(query: str) -> list[dict]:
    """
    Поиск по подстроке ника. Возвращает список совпадений.
    """
    pfids = await fetch_pfids()
    query_lower = query.lower()
    results = []
    for key, val in pfids.items():
        if query_lower in key.lower():
            results.append(val)
    return results[:10]  # не больше 10


# ─── Модерация ────────────────────────────────────────────────────────────────

async def mod_action(action: str, db=None, **kwargs) -> tuple[bool, str]:
    """
    Отправляет moderation action через scriptevent econ:mod.

    Автоподстановки:
    - pfid/xuid по нику из pfids.json
    - tgId/tgNick по MC-нику из TG-базы верификации (db)
    """
    name   = kwargs.get("name")
    tg_id  = kwargs.get("tg_id")
    tg_nick = kwargs.get("tg_nick") or kwargs.get("tgNick")

    # 1. Автоподстановка pfid/xuid по нику из pfids.json
    if name and not kwargs.get("pfid") and not kwargs.get("xuid"):
        player_data = await lookup_player(name)
        if player_data:
            if player_data.get("pfid"):
                kwargs["pfid"] = player_data["pfid"]
                logger.info(f"[mod] pfid={kwargs['pfid']} для {name}")
            if player_data.get("xuid"):
                kwargs["xuid"] = player_data["xuid"]
                logger.info(f"[mod] xuid={kwargs['xuid']} для {name}")

    # 2. Если бан — пробуем найти TG-данные по MC-нику в базе верификации
    if action == "ban" and db is not None:
        tg_user = None
        if name:
            tg_user = db.find_by_mc_name_any(name)
        if tg_user:
            if not kwargs.get("tgId") and tg_user.get("tg_id"):
                kwargs["tgId"]  = tg_user["tg_id"]
                logger.info(f"[mod] tgId={kwargs['tgId']} для {name}")
            if not kwargs.get("tgNick") and tg_user.get("tg_name"):
                kwargs["tgNick"] = tg_user["tg_name"].lstrip("@")
                logger.info(f"[mod] tgNick={kwargs['tgNick']} для {name}")
        # Если бан по tgId — пробуем подтянуть pfid/xuid последнего ника
        if tg_id and not name:
            all_names = db.get_all_mc_names_for_tg(str(tg_id))
            if all_names:
                last = all_names[-1]
                if not kwargs.get("name"):
                    kwargs["name"] = last
                if not kwargs.get("pfid"):
                    pdata = await lookup_player(last)
                    if pdata:
                        if pdata.get("pfid"): kwargs["pfid"] = pdata["pfid"]
                        if pdata.get("xuid"): kwargs["xuid"] = pdata["xuid"]

    payload = {"action": action, **kwargs}
    payload["appeal_url"] = APPEAL_URL
    command = f"scriptevent econ:mod {json.dumps(payload, ensure_ascii=False)}"
    return await send_server_command(command)


# ─── Парсер аргументов модерации ─────────────────────────────────────────────

def parse_mod_args(text: str) -> dict:
    """
    Парсит строку вида:
      Steve Читы
      pfid:abc123 Читы
      xuid:253544 Читы
      Steve 60 Спам   (для мута)
    """
    parts = text.strip().split(maxsplit=1)
    if not parts:
        return {}

    target_raw = parts[0]
    rest = parts[1] if len(parts) > 1 else ""

    result = {}

    if target_raw.startswith("pfid:"):
        result["pfid"] = target_raw[5:].lower()
    elif target_raw.startswith("xuid:"):
        result["xuid"] = target_raw[5:]
    else:
        result["name"] = target_raw

    rest_parts = rest.split(maxsplit=1)
    if rest_parts and rest_parts[0].isdigit():
        result["duration_min"] = int(rest_parts[0])
        result["reason"] = rest_parts[1] if len(rest_parts) > 1 else "Нарушение правил"
    else:
        result["reason"] = rest if rest else "Нарушение правил"

    return result
