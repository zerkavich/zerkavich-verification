import os
import asyncio
import logging
import aiohttp
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from aiogram.fsm.storage.memory import MemoryStorage
from db import Database

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN       = os.getenv("BOT_TOKEN")
PTERODACTYL_URL = os.getenv("PTERODACTYL_URL", "https://my.aurorix.net")
PTERODACTYL_KEY = os.getenv("PTERODACTYL_KEY")
SERVER_ID       = os.getenv("SERVER_ID", "6daf8160-16ab-4a5b-ac25-3e35cb75a3d4")
TG_CHANNEL      = os.getenv("TG_CHANNEL", "@zerkavich")  # канал для проверки подписки
CHECK_SUB       = os.getenv("CHECK_SUBSCRIPTION", "false").lower() == "true"

bot = Bot(token=BOT_TOKEN)
dp  = Dispatcher(storage=MemoryStorage())
db  = Database("data.json")


# ─── PTERODACTYL API ─────────────────────────────────────────────────────────
async def send_server_command(command: str) -> bool:
    url = f"{PTERODACTYL_URL}/api/client/servers/{SERVER_ID}/command"
    headers = {
        "Authorization": f"Bearer {PTERODACTYL_KEY}",
        "Content-Type":  "application/json",
        "Accept":        "application/json",
    }
    payload = {"command": command}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status in (200, 204):
                    logger.info(f"Command sent OK: {command}")
                    return True
                text = await resp.text()
                logger.error(f"Pterodactyl error {resp.status}: {text}")
                return False
    except Exception as e:
        logger.error(f"send_server_command exception: {e}")
        return False


async def check_subscription(user_id: int) -> bool:
    """Проверяет подписку на канал (необязательно)."""
    if not CHECK_SUB:
        return True
    try:
        member = await bot.get_chat_member(TG_CHANNEL, user_id)
        return member.status not in ("left", "kicked")
    except Exception:
        return True  # если не удалось проверить — пропускаем


# ─── /start ──────────────────────────────────────────────────────────────────
@dp.message(Command("start"))
async def cmd_start(msg: Message):
    await msg.answer(
        "👋 <b>Добро пожаловать!</b>\n\n"
        "Это бот верификации сервера Minecraft.\n\n"
        "📋 <b>Команды:</b>\n"
        "• /verify <code>КОД</code> — подтвердить аккаунт\n"
        "• /status — проверить статус верификации\n"
        "• /help — помощь\n\n"
        f"📢 Канал сервера: {TG_CHANNEL}",
        parse_mode="HTML"
    )


# ─── /help ───────────────────────────────────────────────────────────────────
@dp.message(Command("help"))
async def cmd_help(msg: Message):
    await msg.answer(
        "❓ <b>Как верифицироваться:</b>\n\n"
        "1️⃣ Зайдите на сервер Minecraft\n"
        "2️⃣ Введите команду <code>.econ verify</code>\n"
        "3️⃣ Скопируйте код из игры\n"
        "4️⃣ Отправьте боту: <code>/verify ВАШ_КОД</code>\n\n"
        "✅ После верификации вы получите:\n"
        "• Титул <b>«Гражданин»</b> в чате\n"
        "• <b>+200 T</b> на баланс\n"
        "• <b>+10 Trust Score</b>\n\n"
        "⚠️ Код действителен <b>30 минут</b>.",
        parse_mode="HTML"
    )


# ─── /status ─────────────────────────────────────────────────────────────────
@dp.message(Command("status"))
async def cmd_status(msg: Message):
    uid  = str(msg.from_user.id)
    data = db.get_user(uid)
    if data and data.get("verified"):
        await msg.answer(
            f"✅ <b>Аккаунт верифицирован!</b>\n\n"
            f"👤 Minecraft-ник: <code>{data.get('mc_name', '—')}</code>\n"
            f"📅 Дата: {data.get('verified_at', '—')}",
            parse_mode="HTML"
        )
    else:
        await msg.answer(
            "❌ <b>Аккаунт не верифицирован.</b>\n\n"
            "Используйте /verify <code>КОД</code> из игры.",
            parse_mode="HTML"
        )


# ─── /verify КОД ─────────────────────────────────────────────────────────────
@dp.message(Command("verify"))
async def cmd_verify(msg: Message):
    uid      = str(msg.from_user.id)
    username = msg.from_user.username or msg.from_user.first_name

    # Уже верифицирован?
    data = db.get_user(uid)
    if data and data.get("verified"):
        mc = data.get("mc_name", "—")
        await msg.answer(
            f"✅ Вы уже верифицированы как <code>{mc}</code>!\n"
            "Повторная верификация невозможна.",
            parse_mode="HTML"
        )
        return

    # Проверка подписки
    if not await check_subscription(msg.from_user.id):
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text=f"📢 Подписаться на {TG_CHANNEL}", url=f"https://t.me/{TG_CHANNEL.lstrip('@')}")
        ]])
        await msg.answer(
            f"⚠️ Для верификации нужно подписаться на канал {TG_CHANNEL}",
            reply_markup=kb
        )
        return

    # Парсим код
    parts = msg.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await msg.answer(
            "❌ Укажите код из игры.\n\n"
            "Пример: <code>/verify STEVE123456</code>\n\n"
            "Код можно получить через <code>.econ verify</code> в Minecraft.",
            parse_mode="HTML"
        )
        return

    code = parts[1].strip().upper()

    # Базовая валидация кода
    if len(code) < 5 or len(code) > 20 or not code.isalnum():
        await msg.answer("❌ Неверный формат кода. Проверьте и попробуйте снова.")
        return

    # Проверяем код в локальной БД (anti-abuse)
    if db.is_code_used(code):
        await msg.answer("❌ Этот код уже был использован.")
        return

    # Отправляем команду на сервер
    tg_name = f"@{username}" if msg.from_user.username else username
    command = f'scriptevent econ:tg_verify {{"code":"{code}","tg_username":"{tg_name}","tg_id":"{uid}"}}'

    wait_msg = await msg.answer("⏳ Отправляю верификацию на сервер...")

    success = await send_server_command(command)

    if success:
        # Сохраняем в локальную БД
        from datetime import datetime
        db.mark_verified(uid, code, tg_name)
        db.mark_code_used(code)

        await wait_msg.delete()
        await msg.answer(
            "✅ <b>Верификация отправлена!</b>\n\n"
            f"🔑 Код: <code>{code}</code>\n"
            f"👤 TG: {tg_name}\n\n"
            "Зайдите в игру — вы получите:\n"
            "• Титул <b>«Гражданин»</b>\n"
            "• <b>+200 T</b> на баланс\n"
            "• <b>+10 Trust Score</b>\n\n"
            "⚠️ Если сервер был оффлайн — награда придёт при следующем входе.",
            parse_mode="HTML"
        )
        logger.info(f"Verified: tg={uid} ({tg_name}), code={code}")
    else:
        await wait_msg.delete()
        await msg.answer(
            "❌ <b>Не удалось подключиться к серверу.</b>\n\n"
            "Возможные причины:\n"
            "• Сервер выключен\n"
            "• Технические работы\n\n"
            "Попробуйте позже или обратитесь к администратору.",
            parse_mode="HTML"
        )


# ─── НЕИЗВЕСТНЫЕ СООБЩЕНИЯ ───────────────────────────────────────────────────
@dp.message()
async def unknown(msg: Message):
    await msg.answer(
        "❓ Используйте /verify <code>КОД</code> для верификации\n"
        "или /help для справки.",
        parse_mode="HTML"
    )


# ─── ЗАПУСК ──────────────────────────────────────────────────────────────────
async def main():
    logger.info("Bot starting...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
