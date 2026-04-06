"""
ptero_ws.py — Pterodactyl WebSocket listener
=============================================

Подключается к консоли BDS через Pterodactyl WebSocket API,
парсит строки "Player Spawned" с pfid/xuid,
сохраняет в pfids.json, отправляет scriptevent в аддон,
и автоматически обновляет MC-ник в базе верификации по xuid.

Запускается автоматически при старте бота как фоновая задача asyncio.
"""

import asyncio
import json
import logging
import re
import time
from pathlib import Path

import aiohttp

logger = logging.getLogger(__name__)

# ─── Паттерны парсинга логов BDS ─────────────────────────────────────────────

PATTERN_SPAWNED = re.compile(
    r'Player Spawned:\s+(\S+)\s+xuid:\s*(\d+),\s*pfid:\s*([a-f0-9]+)',
    re.IGNORECASE
)
PATTERN_CONNECTED = re.compile(
    r'Player connected:\s+([^,]+),\s+xuid:\s*(\d+)',
    re.IGNORECASE
)
PATTERN_DISCONNECTED = re.compile(
    r'Player disconnected:\s+([^,]+),\s+xuid:\s*(\d+)',
    re.IGNORECASE
)

# scriptevent econ:tg_verify_result выводится в консоль BDS как:
# [Scripting] econ:tg_verify_result {"ok":true,"tg_id":"123","mc_name":"Steve","playerId":"..."}
PATTERN_VERIFY_RESULT = re.compile(
    r'econ:tg_verify_result\s+(\{.+\})',
    re.IGNORECASE
)

# Если аддон не нашёл код — пишет warn без scriptevent:
# [Scripting] [TG Verify] Код не найден или истек: XXXXXXX
# Ловим код, чтобы найти pending-запись в БД по коду и откатить её.
PATTERN_VERIFY_FAIL_WARN = re.compile(
    r'\[TG Verify\].{0,60}(?:не найден|not found).{0,30}:\s*([A-Z0-9]{5,20})',
    re.IGNORECASE
)


def parse_console_line(line: str) -> dict | None:
    m = PATTERN_SPAWNED.search(line)
    if m:
        return {
            'event': 'spawned',
            'name': m.group(1),
            'xuid': m.group(2),
            'pfid': m.group(3).lower(),
        }
    m = PATTERN_CONNECTED.search(line)
    if m:
        return {
            'event': 'connected',
            'name': m.group(1).strip(),
            'xuid': m.group(2),
            'pfid': '',
        }
    m = PATTERN_DISCONNECTED.search(line)
    if m:
        return {
            'event': 'disconnected',
            'name': m.group(1).strip(),
            'xuid': m.group(2),
        }
    m = PATTERN_VERIFY_RESULT.search(line)
    if m:
        try:
            import json as _json
            payload = _json.loads(m.group(1))
            return {
                'event': 'verify_result',
                'ok':      payload.get('ok', False),
                'tg_id':   str(payload.get('tg_id', '')),
                'mc_name': payload.get('mc_name', ''),
            }
        except Exception:
            pass
    # Аддон не нашёл код и написал warn вместо scriptevent:
    # [Scripting] [TG Verify] Код не найден или истек: GHJHGHI
    m = PATTERN_VERIFY_FAIL_WARN.search(line)
    if m:
        return {
            'event': 'verify_fail_warn',
            'code':  m.group(1).upper(),
        }
    return None


# ─── Основной класс ───────────────────────────────────────────────────────────

class PteroConsoleWatcher:
    """
    Слушает консоль сервера через Pterodactyl WebSocket.
    Парсит pfid/xuid, сохраняет в pfids.json, отправляет scriptevent.
    Автоматически обновляет MC-ник в базе верификации по xuid.
    """

    def __init__(
        self,
        panel_url: str,
        api_key: str,
        server_id: str,
        output_file: str = 'pfids.json',
        appeal_url: str = '@zerkavich',
        db=None,   # опционально: экземпляр Database для обновления MC-ников
    ):
        self.panel_url   = panel_url.rstrip('/')
        self.api_key     = api_key
        self.server_id   = server_id
        self.output_file = output_file
        self.appeal_url  = appeal_url
        self.db          = db   # Database instance, привязывается из bot.py
        self.bot         = None  # aiogram Bot instance, привязывается из bot.py для уведомлений

        self.known_players: dict = {}
        self.online_players: set = set()

        self._load_known()

        self._running = False
        self._ws = None

    def set_db(self, db):
        """Привязывает базу данных верификации для обновления MC-ников."""
        self.db = db

    def set_bot(self, bot):
        """Привязывает aiogram Bot для отправки уведомлений пользователям."""
        self.bot = bot

    # ─── pfids.json ──────────────────────────────────────────────────────────

    def _load_known(self):
        p = Path(self.output_file)
        if p.exists():
            try:
                self.known_players = json.loads(p.read_text(encoding='utf-8'))
                logger.info(f'[ptero_ws] Загружено {len(self.known_players)} игроков')
            except Exception as e:
                logger.warning(f'[ptero_ws] Ошибка загрузки {self.output_file}: {e}')

    def _save_known(self):
        try:
            Path(self.output_file).write_text(
                json.dumps(self.known_players, ensure_ascii=False, indent=2),
                encoding='utf-8'
            )
        except Exception as e:
            logger.warning(f'[ptero_ws] Ошибка сохранения: {e}')

    # ─── Pterodactyl API ─────────────────────────────────────────────────────

    async def _get_ws_credentials(self, session: aiohttp.ClientSession) -> tuple[str, str] | None:
        url = f'{self.panel_url}/api/client/servers/{self.server_id}/websocket'
        headers = {
            'Authorization': f'Bearer {self.api_key}',
            'Accept': 'application/json',
        }
        try:
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    logger.error(f'[ptero_ws] Ошибка WS credentials: HTTP {resp.status}: {text[:200]}')
                    return None
                data = await resp.json()
                return data['data']['token'], data['data']['socket']
        except Exception as e:
            logger.error(f'[ptero_ws] Ошибка запроса WS credentials: {e}')
            return None

    async def _send_command(self, session: aiohttp.ClientSession, command: str) -> bool:
        url = f'{self.panel_url}/api/client/servers/{self.server_id}/command'
        headers = {
            'Authorization': f'Bearer {self.api_key}',
            'Content-Type': 'application/json',
            'Accept': 'application/json',
        }
        try:
            async with session.post(
                url, json={'command': command}, headers=headers,
                timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                return resp.status in (200, 204)
        except Exception as e:
            logger.warning(f'[ptero_ws] Ошибка отправки команды: {e}')
            return False

    # ─── Обработка данных игрока ─────────────────────────────────────────────

    def _update_mc_name_in_db(self, name: str, xuid: str):
        """
        Обновляет MC-ник в базе верификации если нашли запись с этим xuid.
        Вызывается при каждом Player Spawned.
        """
        if not self.db or not xuid:
            return
        # Ищем верифицированного пользователя у которого сохранён xuid
        for uid, user in self.db._data["users"].items():
            # Совпадение по xuid (если уже был записан scriptevent)
            stored_xuid = user.get("xuid") or ""
            if stored_xuid and stored_xuid == xuid:
                old_mc = user.get("mc_name")
                if old_mc != name:
                    logger.info(f'[ptero_ws] Обновляем MC-ник: {old_mc} → {name} (xuid={xuid})')
                    self.db.set_mc_name(uid, name)
                return
            # Совпадение по коду: аддон сохраняет xuid через scriptevent econ:playerdata
            # Если xuid ещё не записан — попробуем матч по mc_name
            if (user.get("mc_name") or "").lower() == name.lower():
                # Дозаписываем xuid
                if not stored_xuid:
                    user["xuid"] = xuid
                    self.db._save()
                return

    async def _handle_player_data(self, data: dict, session: aiohttp.ClientSession):
        name = data['name']
        xuid = data.get('xuid', '')
        pfid = data.get('pfid', '')

        prev = self.known_players.get(name, {})

        changed = (
            (pfid and pfid != prev.get('pfid')) or
            (xuid and xuid != prev.get('xuid')) or
            name not in self.known_players
        )

        if changed:
            entry = {
                'name':    name,
                'xuid':    xuid or prev.get('xuid', ''),
                'pfid':    pfid or prev.get('pfid', ''),
                'updated': time.strftime('%Y-%m-%d %H:%M:%S'),
            }
            self.known_players[name] = entry
            self._save_known()

            payload = json.dumps({
                'name': entry['name'],
                'xuid': entry['xuid'],
                'pfid': entry['pfid'],
            }, ensure_ascii=False)
            cmd = f'scriptevent econ:playerdata {payload}'
            ok = await self._send_command(session, cmd)

            pfid_str = entry['pfid'] or '—'
            xuid_str = entry['xuid'] or '—'
            status = '✓' if ok else '✗'
            logger.info(f'[ptero_ws] {status} {name} pfid={pfid_str} xuid={xuid_str}')

        # Всегда пробуем обновить MC-ник в базе верификации
        if xuid:
            self._update_mc_name_in_db(name, xuid)

    # ─── Обработка результата верификации от аддона ─────────────────────────

    async def _handle_verify_result(self, data: dict):
        """
        Аддон прислал результат econ:tg_verify_result.
        ok=True  → верификация прошла, записываем mc_name в базу.
        ok=False → код не найден в аддоне, нужно откатить mark_verified в боте.
        """
        if not self.db:
            return

        tg_id   = data.get('tg_id', '')
        mc_name = data.get('mc_name', '')
        ok      = data.get('ok', False)

        if ok and tg_id and mc_name:
            # Аддон подтвердил код — только теперь выставляем verified=True
            user = self.db.find_by_tg_id(tg_id)
            if user:
                self.db.confirm_verified(tg_id, mc_name)
                logger.info(f'[ptero_ws] verify_result OK: tg_id={tg_id} mc_name={mc_name} → verified=True')
                # Помечаем код использованным
                code = user.get('code', '')
                if code:
                    self.db.mark_code_used(code)
            else:
                logger.warning(f'[ptero_ws] verify_result ok но tg_id={tg_id} не найден в db')
        elif not ok:
            # Код не существовал в аддоне — откатываем привязку
            logger.warning(f'[ptero_ws] verify_result FAIL tg_id={tg_id}, откатываем')
            if tg_id:
                self.db.unlink_tg(tg_id)

    # ─── Обработка warn-строки когда аддон не нашёл код ────────────────────────

    async def _handle_verify_fail_warn(self, data: dict):
        """
        Аддон вывел warn вместо scriptevent — код не найден.
        Ищем pending-запись по коду, откатываем и уведомляем пользователя в TG.
        """
        logger.info(f'[ptero_ws] _handle_verify_fail_warn вызван: {data}')

        if not self.db:
            logger.error('[ptero_ws] verify_fail_warn: db не привязан!')
            return

        code = data.get('code', '')
        if not code:
            logger.error('[ptero_ws] verify_fail_warn: code пустой')
            return

        logger.info(f'[ptero_ws] verify_fail_warn: ищем pending по коду {code}')
        tg_id, user = self.db.find_pending_by_code(code)
        logger.info(f'[ptero_ws] verify_fail_warn: find_pending_by_code → tg_id={tg_id}, user={user}')

        if not tg_id:
            logger.warning(f'[ptero_ws] verify_fail_warn: pending-запись для кода {code} не найдена')
            # Даже если запись не найдена — попробуем найти по tg_id из данных
            # (на случай если запись уже помечена verified=True ошибочно)
            return

        logger.warning(f'[ptero_ws] verify_fail_warn: код {code} не найден, откатываем tg_id={tg_id}')
        self.db.unlink_tg(tg_id)

        if not self.bot:
            logger.error('[ptero_ws] verify_fail_warn: bot не привязан, уведомление не отправлено!')
            return

        try:
            await self.bot.send_message(
                chat_id=int(tg_id),
                text=(
                    "❌ <b>Верификация не пройдена.</b>\n\n"
                    f"Код <code>{code}</code> не найден или истёк на сервере.\n\n"
                    "Убедитесь, что вы ввели правильный код из игры командой "
                    "<code>.econ verify</code>, и попробуйте снова."
                ),
                parse_mode="HTML"
            )
            logger.info(f'[ptero_ws] verify_fail_warn: уведомление отправлено tg_id={tg_id}')
        except Exception as e:
            logger.warning(f'[ptero_ws] Не удалось отправить уведомление tg_id={tg_id}: {e}')

    # ─── WebSocket цикл ──────────────────────────────────────────────────────

    async def _ws_loop(self, session: aiohttp.ClientSession):
        creds = await self._get_ws_credentials(session)
        if not creds:
            return False
        token, socket_url = creds

        logger.info(f'[ptero_ws] Подключаемся к {socket_url}')

        try:
            async with session.ws_connect(
                socket_url,
                headers={'Origin': self.panel_url},
                heartbeat=30,
                timeout=aiohttp.ClientWSTimeout(ws_close=10),
            ) as ws:
                self._ws = ws

                await ws.send_json({'event': 'auth', 'args': [token]})
                logger.info('[ptero_ws] Auth отправлен...')

                async for msg in ws:
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        try:
                            event = json.loads(msg.data)
                        except Exception:
                            continue

                        ev_name = event.get('event', '')
                        args    = event.get('args', [])

                        # Дебаг: логируем любой эвент содержащий TG Verify
                        if args and any('tg verify' in str(a).lower() or 'tg_verify' in str(a).lower() for a in args):
                            logger.info(f'[ptero_ws] [DBG] ev={repr(ev_name)} args={repr(args)}')

                        if ev_name == 'auth success':
                            logger.info('[ptero_ws] ✓ Авторизован, слушаем консоль...')
                            await ws.send_json({'event': 'send logs', 'args': [None]})

                        elif ev_name == 'token expiring':
                            logger.info('[ptero_ws] Токен истекает, обновляем...')
                            new_creds = await self._get_ws_credentials(session)
                            if new_creds:
                                new_token, _ = new_creds
                                await ws.send_json({'event': 'auth', 'args': [new_token]})

                        elif ev_name == 'token expired':
                            logger.warning('[ptero_ws] Токен истёк, переподключаемся')
                            return True

                        elif ev_name == 'jwt error':
                            logger.error(f'[ptero_ws] JWT ошибка: {args}')
                            return True

                        elif ev_name == 'console output' and args:
                            line = args[0] if isinstance(args[0], str) else ''
                            # Убираем ANSI escape-коды которые Pterodactyl добавляет к цветным строкам
                            line_clean = re.sub(r'\x1b\[[0-9;]*m', '', line)
                            # Дебаг: логируем все строки содержащие TG Verify
                            if 'tg verify' in line_clean.lower() or 'tg_verify' in line_clean.lower():
                                logger.info(f'[ptero_ws] [DBG] TG line: {repr(line_clean)}')
                            parsed = parse_console_line(line_clean)
                            if parsed:
                                ev = parsed['event']
                                if ev in ('spawned', 'connected'):
                                    self.online_players.add(parsed['name'])
                                    await self._handle_player_data(parsed, session)
                                elif ev == 'disconnected':
                                    self.online_players.discard(parsed['name'])
                                    logger.info(f"[ptero_ws] ← {parsed['name']} вышел")
                                elif ev == 'verify_result':
                                    await self._handle_verify_result(parsed)
                                elif ev == 'verify_fail_warn':
                                    await self._handle_verify_fail_warn(parsed)

                        elif ev_name == 'status':
                            status = args[0] if args else '?'
                            if status == 'offline':
                                self.online_players.clear()

                    elif msg.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSE):
                        logger.warning(f'[ptero_ws] WS закрыт: {msg.type}')
                        return True

        except aiohttp.ClientError as e:
            logger.warning(f'[ptero_ws] WS ошибка соединения: {e}')
            return True
        except Exception as e:
            logger.error(f'[ptero_ws] Неожиданная ошибка: {e}')
            return True

        return True

    # ─── Публичный интерфейс ─────────────────────────────────────────────────

    async def run(self):
        self._running = True
        retry_delay = 5
        logger.info('[ptero_ws] Запущен')

        async with aiohttp.ClientSession() as session:
            while self._running:
                try:
                    should_retry = await self._ws_loop(session)
                    if not should_retry:
                        await asyncio.sleep(retry_delay)
                        retry_delay = min(retry_delay * 2, 60)
                    else:
                        retry_delay = 5
                except Exception as e:
                    logger.error(f'[ptero_ws] Критическая ошибка: {e}')

                if self._running:
                    logger.info(f'[ptero_ws] Переподключение через {retry_delay}с...')
                    await asyncio.sleep(retry_delay)

        logger.info('[ptero_ws] Остановлен')

    def stop(self):
        self._running = False

    def get_player(self, name: str) -> dict | None:
        if name in self.known_players:
            return self.known_players[name]
        name_lower = name.lower()
        for key, val in self.known_players.items():
            if key.lower() == name_lower:
                return val
        return None

    def search_players(self, query: str) -> list[dict]:
        q = query.lower()
        return [v for k, v in self.known_players.items() if q in k.lower()][:10]

    @property
    def online(self) -> set[str]:
        return self.online_players
