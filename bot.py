import os
import asyncio
import logging
import aiohttp
from datetime import datetime
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from db import Database
from moderation import is_admin, mod_action, parse_mod_args, send_server_command
from ptero_ws import PteroConsoleWatcher
from aiogram.fsm.state import State, StatesGroup
from admin_panel import (
    AdminState, admin_main_kb, admin_main_text, back_kb,
    BAN_HELP, KICK_HELP, MUTE_HELP, SEARCH_HELP, SEARCH_MC_HELP, UNLINK_HELP,
    format_ban_log, format_tg_search_result, format_player_card, parse_panel_ban,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN       = os.getenv("BOT_TOKEN")
PTERODACTYL_URL = os.getenv("PTERODACTYL_URL", "https://my.aurorix.net")
PTERODACTYL_KEY = os.getenv("PTERODACTYL_KEY")
SERVER_ID       = os.getenv("SERVER_ID", "6daf8160-16ab-4a5b-ac25-3e35cb75a3d4")
TG_CHANNEL      = os.getenv("TG_CHANNEL", "@zerkavich")
CHECK_SUB       = os.getenv("CHECK_SUBSCRIPTION", "false").lower() == "true"
APPEAL_URL      = os.getenv("APPEAL_URL", "@zerkavich")

bot = Bot(token=BOT_TOKEN)
dp  = Dispatcher(storage=MemoryStorage())
db  = Database("data.json")

watcher = PteroConsoleWatcher(
    panel_url   = PTERODACTYL_URL,
    api_key     = PTERODACTYL_KEY or '',
    server_id   = SERVER_ID,
    output_file = 'pfids.json',
    appeal_url  = APPEAL_URL,
    db          = db,
)



# ─── FSM состояние для ввода кода верификации ─────────────────────────────────

class VerifyState(StatesGroup):
    waiting_code = State()


async def check_subscription(user_id: int) -> bool:
    if not CHECK_SUB:
        return True
    try:
        member = await bot.get_chat_member(TG_CHANNEL, user_id)
        return member.status not in ("left", "kicked")
    except Exception:
        return True


# ─── Главное меню верификации (кнопки) ───────────────────────────────────────

def main_menu_kb(is_verified: bool = False) -> InlineKeyboardMarkup:
    if is_verified:
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📋 Мой статус", callback_data="menu:status")],
            [InlineKeyboardButton(text="❓ Помощь",     callback_data="menu:help")],
        ])
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Верифицироваться", callback_data="menu:verify")],
        [InlineKeyboardButton(text="📋 Мой статус",       callback_data="menu:status")],
        [InlineKeyboardButton(text="❓ Как это работает", callback_data="menu:help")],
    ])


# ─── /start ──────────────────────────────────────────────────────────────────

@dp.message(Command("start"))
async def cmd_start(msg: Message):
    uid = str(msg.from_user.id)
    data = db.get_user(uid)
    is_verified = bool(data and data.get("verified"))

    admin_hint = ""
    if is_admin(msg.from_user.id):
        admin_hint = "\n\n🔧 <b>Режим администратора активен.</b> /admin"

    await msg.answer(
        "👋 <b>Добро пожаловать!</b>\n\n"
        "Бот верификации сервера Minecraft.\n"
        "Привяжи Telegram-аккаунт к своему MC-профилю.\n"
        f"📢 Канал: {TG_CHANNEL}"
        f"{admin_hint}",
        reply_markup=main_menu_kb(is_verified),
        parse_mode="HTML"
    )


# ─── Callback: главное меню ───────────────────────────────────────────────────

@dp.callback_query(F.data == "menu:help")
async def cb_menu_help(call: CallbackQuery):
    await call.message.edit_text(
        "❓ <b>Как верифицироваться:</b>\n\n"
        "1️⃣ Зайдите на сервер Minecraft\n"
        "2️⃣ Введите <code>.econ verify</code>\n"
        "3️⃣ Скопируйте код из игры\n"
        "4️⃣ Нажмите «Верифицироваться» и введите код\n\n"
        "✅ После верификации вы получите:\n"
        "• Титул <b>«Гражданин»</b>\n"
        "• <b>+200 T</b> на баланс\n"
        "• <b>+10 Trust Score</b>\n\n"
        "⚠️ Код действителен <b>30 минут</b>.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="◀️ Назад", callback_data="menu:back"),
        ]]),
        parse_mode="HTML"
    )
    await call.answer()


@dp.callback_query(F.data == "menu:status")
async def cb_menu_status(call: CallbackQuery):
    uid  = str(call.from_user.id)
    data = db.get_user(uid)
    if data and data.get("verified"):
        mc = data.get("mc_name") or "—"
        text = (
            f"✅ <b>Аккаунт верифицирован!</b>\n\n"
            f"🎮 MC-ник: <code>{mc}</code>\n"
            f"📅 Дата: {data.get('verified_at', '—')}"
        )
    else:
        text = (
            "❌ <b>Не верифицирован.</b>\n\n"
            "Нажмите «Верифицироваться» чтобы привязать аккаунт."
        )
    await call.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="◀️ Назад", callback_data="menu:back"),
        ]]),
        parse_mode="HTML"
    )
    await call.answer()


@dp.callback_query(F.data == "menu:back")
async def cb_menu_back(call: CallbackQuery):
    uid = str(call.from_user.id)
    data = db.get_user(uid)
    is_verified = bool(data and data.get("verified"))
    await call.message.edit_text(
        "👋 <b>Добро пожаловать!</b>\n\n"
        "Бот верификации сервера Minecraft.\n"
        "Привяжи Telegram-аккаунт к своему MC-профилю.\n"
        f"📢 Канал: {TG_CHANNEL}",
        reply_markup=main_menu_kb(is_verified),
        parse_mode="HTML"
    )
    await call.answer()


@dp.callback_query(F.data == "menu:verify")
async def cb_menu_verify(call: CallbackQuery, state: FSMContext):
    uid = str(call.from_user.id)
    data = db.get_user(uid)
    if data and data.get("verified"):
        await call.answer("✅ Вы уже верифицированы!", show_alert=True)
        return

    if not await check_subscription(call.from_user.id):
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📢 Подписаться", url=f"https://t.me/{TG_CHANNEL.lstrip('@')}")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="menu:back")],
        ])
        await call.message.edit_text(
            f"⚠️ Для верификации сначала подпишитесь на {TG_CHANNEL}",
            reply_markup=kb,
            parse_mode="HTML"
        )
        await call.answer()
        return

    await state.set_state(VerifyState.waiting_code)
    await call.message.edit_text(
        "🔑 <b>Введите код верификации</b>\n\n"
        "Получите код в игре командой <code>.econ verify</code>\n"
        "и отправьте его сюда одним сообщением.\n\n"
        "⚠️ Код действителен 30 минут.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="❌ Отмена", callback_data="menu:back"),
        ]]),
        parse_mode="HTML"
    )
    await call.answer()


# ─── /help ───────────────────────────────────────────────────────────────────

@dp.message(Command("help"))
async def cmd_help(msg: Message):
    await msg.answer(
        "❓ <b>Как верифицироваться:</b>\n\n"
        "1️⃣ Зайдите на сервер Minecraft\n"
        "2️⃣ Введите <code>.econ verify</code>\n"
        "3️⃣ Скопируйте код из игры\n"
        "4️⃣ Нажмите «Верифицироваться» в меню или /verify <code>КОД</code>\n\n"
        "✅ После верификации вы получите:\n"
        "• Титул <b>«Гражданин»</b>\n"
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
        mc = data.get("mc_name") or "—"
        await msg.answer(
            f"✅ <b>Аккаунт верифицирован!</b>\n\n"
            f"🎮 MC-ник: <code>{mc}</code>\n"
            f"📅 Дата: {data.get('verified_at', '—')}",
            parse_mode="HTML"
        )
    else:
        await msg.answer(
            "❌ <b>Не верифицирован.</b>\n\n"
            "Используйте /verify <code>КОД</code> из игры.",
            parse_mode="HTML"
        )


# ─── Общая функция верификации ────────────────────────────────────────────────

async def do_verify(msg: Message, code: str, state: FSMContext | None = None):
    """
    Флоу верификации:
      1. Бот шлёт scriptevent econ:tg_verify → аддон проверяет, существует ли код
      2. Аддон шлёт scriptevent econ:tg_verify_result → ptero_ws ловит в консоли
         ok=True  → ptero_ws записывает mc_name и помечает код использованным
         ok=False → ptero_ws откатывает mark_verified через unlink_tg
      Таким образом привязка к несуществующему коду автоматически откатывается.
    """
    uid      = str(msg.from_user.id)
    username = msg.from_user.username or msg.from_user.first_name

    data = db.get_user(uid)
    if data and data.get("verified"):
        if state:
            await state.clear()
        await msg.answer("✅ Вы уже верифицированы!\nПовторная верификация невозможна.")
        return

    code = code.strip().upper()
    if len(code) < 5 or len(code) > 20 or not code.isalnum():
        await msg.answer(
            "❌ Неверный формат кода.\n\n"
            "Код должен быть от 5 до 20 символов (буквы и цифры).\n"
            "Получите код командой <code>.econ verify</code> в игре.",
            parse_mode="HTML"
        )
        return

    if db.is_code_used(code):
        await msg.answer("❌ Этот код уже был использован.")
        return

    tg_name = f"@{username}" if msg.from_user.username else username
    command = f'scriptevent econ:tg_verify {{"code":"{code}","tg_username":"{tg_name}","tg_id":"{uid}"}}'

    wait_msg = await msg.answer("⏳ Проверяю код на сервере...")
    ok, err = await send_server_command(command)

    if ok:
        # HTTP 204 — команда дошла до сервера (но код мог не существовать в аддоне).
        # Сохраняем pending-запись только сейчас — verified=False, mc_name=None.
        # Если аддон ответит ok=True  → ptero_ws запишет mc_name и выставит verified=True.
        # Если аддон ответит ok=False → ptero_ws откатит запись через unlink_tg.
        # НЕ выставляем verified=True здесь — это делает только ptero_ws после подтверждения.
        db.save_pending(uid, code, tg_name)  # сохраняет verified=False
        if state:
            await state.clear()
        await wait_msg.delete()
        await msg.answer(
            "⏳ <b>Запрос отправлен, ожидаем подтверждения сервера.</b>\n\n"
            f"🔑 Код: <code>{code}</code>\n\n"
            "Если код верный, в игре вы получите:\n"
            "• Титул <b>«Гражданин»</b>\n"
            "• <b>+200 T</b> на баланс\n"
            "• <b>+10 Trust Score</b>\n\n"
            "⚠️ Верификация будет подтверждена только после ответа сервера.\n"
            "Если код неверный — привязка не произойдёт.",
            reply_markup=main_menu_kb(is_verified=False),  # не показываем как верифицированного
            parse_mode="HTML"
        )
    else:
        # Сервер недоступен — не сохраняем ничего
        await wait_msg.delete()
        await msg.answer(
            f"❌ <b>Сервер недоступен.</b>\n\n"
            f"Попробуйте позже или обратитесь в {APPEAL_URL}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="🔄 Попробовать снова", callback_data="menu:verify"),
            ]]) if state else None,
            parse_mode="HTML"
        )


# ─── /verify КОД ─────────────────────────────────────────────────────────────

@dp.message(Command("verify"))
async def cmd_verify(msg: Message):
    if not await check_subscription(msg.from_user.id):
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="📢 Подписаться", url=f"https://t.me/{TG_CHANNEL.lstrip('@')}")
        ]])
        await msg.answer(f"⚠️ Для верификации подпишитесь на {TG_CHANNEL}", reply_markup=kb)
        return

    parts = msg.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await msg.answer(
            "❌ Укажите код из игры.\n\nПример: <code>/verify STEVE123456</code>",
            parse_mode="HTML"
        )
        return

    await do_verify(msg, parts[1].strip())


# ─── Обработка кода верификации из меню ──────────────────────────────────────

@dp.message(VerifyState.waiting_code)
async def handle_verify_code(msg: Message, state: FSMContext):
    await do_verify(msg, msg.text or "", state)


# ═══════════════════════════════════════════════════════════════════════════════
# СКРЫТАЯ АДМИН-ПАНЕЛЬ
# ═══════════════════════════════════════════════════════════════════════════════

@dp.message(Command("admin"))
async def cmd_admin(msg: Message, state: FSMContext):
    if not is_admin(msg.from_user.id):
        return
    await state.clear()
    await msg.answer(
        admin_main_text(db),
        reply_markup=admin_main_kb(),
        parse_mode="HTML"
    )


@dp.callback_query(F.data == "adm:main")
async def cb_admin_main(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("❌", show_alert=True)
        return
    await state.clear()
    await call.message.edit_text(
        admin_main_text(db),
        reply_markup=admin_main_kb(),
        parse_mode="HTML"
    )
    await call.answer()


@dp.callback_query(F.data == "adm:close")
async def cb_admin_close(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.message.delete()
    await call.answer("Панель закрыта.")


# ─── Бан ─────────────────────────────────────────────────────────────────────

@dp.callback_query(F.data == "adm:ban")
async def cb_admin_ban(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("❌", show_alert=True)
        return
    await state.set_state(AdminState.waiting_ban_input)
    await call.message.edit_text(BAN_HELP, reply_markup=back_kb(), parse_mode="HTML")
    await call.answer()


@dp.message(AdminState.waiting_ban_input)
async def process_ban_input(msg: Message, state: FSMContext):
    if not is_admin(msg.from_user.id):
        return
    if msg.text and msg.text.startswith("/admin"):
        await state.clear()
        await msg.answer(admin_main_text(db), reply_markup=admin_main_kb(), parse_mode="HTML")
        return

    data = await state.get_data()
    prefill_name = data.get("prefill_name")

    if prefill_name:
        reason = (msg.text or "Нарушение правил").strip() or "Нарушение правил"
        args = {"name": prefill_name, "reason": reason}
    else:
        args = parse_panel_ban(msg.text or "", db)

    if not args.get("name") and not args.get("pfid") and not args.get("xuid"):
        await msg.answer("❌ Не удалось определить цель. Попробуй ещё раз.", parse_mode="HTML")
        return

    wait = await msg.answer("⏳ Баню...")
    ok, err = await mod_action("ban", db=db, watcher=watcher,
        name=args.get("name"),
        pfid=args.get("pfid"),
        xuid=args.get("xuid"),
        reason=args.get("reason", "Нарушение правил"),
        by=msg.from_user.username or msg.from_user.first_name,
    )
    await wait.delete()

    target_str = args.get("name") or args.get("pfid") or args.get("xuid") or "?"
    tg_note    = args.get("tg_note")

    if ok:
        db.add_ban_log(target_str, args["reason"], str(msg.from_user.id))
        note_line = f"\n📱 TG: {tg_note}" if tg_note else ""
        await msg.answer(
            f"🔨 <b>Игрок заблокирован!</b>\n\n"
            f"👤 Цель: <code>{target_str}</code>{note_line}\n"
            f"📝 Причина: {args['reason']}\n"
            f"👮 {msg.from_user.username or msg.from_user.first_name}",
            reply_markup=back_kb(),
            parse_mode="HTML"
        )
    else:
        await msg.answer(
            f"❌ Ошибка: <code>{err}</code>",
            reply_markup=back_kb(),
            parse_mode="HTML"
        )
    await state.clear()


# ─── Разбан ──────────────────────────────────────────────────────────────────

@dp.callback_query(F.data == "adm:unban")
async def cb_admin_unban(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("❌", show_alert=True)
        return

    bans = db.get_ban_log()
    if not bans:
        await call.message.edit_text(
            "📋 <b>Лог банов пуст.</b>\n\nНечего разбанивать.",
            reply_markup=back_kb(),
            parse_mode="HTML"
        )
        await call.answer()
        return

    rows = []
    for b in bans[:10]:
        t   = b.get("target", "?")[:20]
        rows.append([InlineKeyboardButton(
            text=f"🔓 {t}",
            callback_data=f"adm:unban_do:{b['target']}"
        )])
    rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data="adm:main")])

    await call.message.edit_text(
        "🔓 <b>Выбери игрока для разбана:</b>\n\nПоследние 10 забаненных.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        parse_mode="HTML"
    )
    await call.answer()


@dp.callback_query(F.data.startswith("adm:unban_do:"))
async def cb_unban_do(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("❌", show_alert=True)
        return

    target = call.data.split(":", 2)[2]
    ok, err = await mod_action(
        "unban",
        name=target,
        by=call.from_user.username or call.from_user.first_name,
    )
    if ok:
        await call.message.edit_text(
            f"✅ <b>{target}</b> разблокирован!",
            reply_markup=back_kb(),
            parse_mode="HTML"
        )
    else:
        await call.message.edit_text(
            f"❌ Ошибка: <code>{err}</code>",
            reply_markup=back_kb(),
            parse_mode="HTML"
        )
    await call.answer()


# ─── Кик ─────────────────────────────────────────────────────────────────────

@dp.callback_query(F.data == "adm:kick")
async def cb_admin_kick(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("❌", show_alert=True)
        return
    await state.set_state(AdminState.waiting_kick_input)
    await call.message.edit_text(KICK_HELP, reply_markup=back_kb(), parse_mode="HTML")
    await call.answer()


@dp.message(AdminState.waiting_kick_input)
async def process_kick_input(msg: Message, state: FSMContext):
    if not is_admin(msg.from_user.id):
        return
    if msg.text and msg.text.startswith("/admin"):
        await state.clear()
        await msg.answer(admin_main_text(db), reply_markup=admin_main_kb(), parse_mode="HTML")
        return

    parts  = (msg.text or "").split("|", 1)
    name   = parts[0].strip()
    reason = parts[1].strip() if len(parts) > 1 else "Кик администратором"

    ok, err = await mod_action(
        "kick",
        name=name,
        reason=reason,
        by=msg.from_user.username or msg.from_user.first_name,
    )
    if ok:
        await msg.answer(
            f"👢 <b>{name}</b> кикнут.\n📝 {reason}",
            reply_markup=back_kb(),
            parse_mode="HTML"
        )
    else:
        await msg.answer(
            f"❌ Ошибка: <code>{err}</code>",
            reply_markup=back_kb(),
            parse_mode="HTML"
        )
    await state.clear()


# ─── Мут ─────────────────────────────────────────────────────────────────────

@dp.callback_query(F.data == "adm:mute")
async def cb_admin_mute(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("❌", show_alert=True)
        return
    await state.set_state(AdminState.waiting_mute_input)
    await call.message.edit_text(MUTE_HELP, reply_markup=back_kb(), parse_mode="HTML")
    await call.answer()


@dp.message(AdminState.waiting_mute_input)
async def process_mute_input(msg: Message, state: FSMContext):
    if not is_admin(msg.from_user.id):
        return
    if msg.text and msg.text.startswith("/admin"):
        await state.clear()
        await msg.answer(admin_main_text(db), reply_markup=admin_main_kb(), parse_mode="HTML")
        return

    parts  = (msg.text or "").split("|")
    name   = parts[0].strip() if len(parts) > 0 else ""
    dur    = int(parts[1].strip()) if len(parts) > 1 and parts[1].strip().isdigit() else 60
    reason = parts[2].strip() if len(parts) > 2 else "Нарушение правил"

    ok, err = await mod_action(
        "mute",
        name=name,
        reason=reason,
        duration_min=dur,
        by=msg.from_user.username or msg.from_user.first_name,
    )
    if ok:
        await msg.answer(
            f"🔇 <b>{name}</b> заглушен на {dur} мин.\n📝 {reason}",
            reply_markup=back_kb(),
            parse_mode="HTML"
        )
    else:
        await msg.answer(
            f"❌ Ошибка: <code>{err}</code>",
            reply_markup=back_kb(),
            parse_mode="HTML"
        )
    await state.clear()


# ─── Поиск MC-игрока ─────────────────────────────────────────────────────────

@dp.callback_query(F.data == "adm:search_mc")
async def cb_admin_search_mc(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("❌", show_alert=True)
        return
    await state.set_state(AdminState.waiting_search_mc)
    await call.message.edit_text(SEARCH_MC_HELP, reply_markup=back_kb(), parse_mode="HTML")
    await call.answer()


@dp.message(AdminState.waiting_search_mc)
async def process_search_mc(msg: Message, state: FSMContext):
    if not is_admin(msg.from_user.id):
        return
    if msg.text and msg.text.startswith("/admin"):
        await state.clear()
        await msg.answer(admin_main_text(db), reply_markup=admin_main_kb(), parse_mode="HTML")
        return

    query = (msg.text or "").strip()
    wait = await msg.answer("⏳ Ищу игрока...")
    results = watcher.search_players(query)
    await wait.delete()

    if not results:
        await msg.answer(
            f"❌ Игрок <code>{query}</code> не найден.\n\n"
            "💡 Игрок должен зайти на сервер хотя бы раз после старта бота.",
            reply_markup=back_kb(),
            parse_mode="HTML"
        )
        await state.clear()
        return

    if len(results) == 1:
        p = results[0]
        name = p.get("name", "?")
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔨 Забанить", callback_data=f"adm:quickban:{name}")],
            [InlineKeyboardButton(text="◀️ Назад",    callback_data="adm:main")],
        ])
        await msg.answer(
            f"✅ <b>Найден игрок:</b>\n\n{format_player_card(p)}",
            reply_markup=kb,
            parse_mode="HTML"
        )
    else:
        lines = [f"🔎 Найдено <b>{len(results)}</b> игроков по запросу <code>{query}</code>:\n"]
        for p in results:
            lines.append(format_player_card(p))
        lines.append("\n💡 Уточни запрос для точного совпадения.")
        await msg.answer("\n".join(lines), reply_markup=back_kb(), parse_mode="HTML")
    await state.clear()


@dp.callback_query(F.data.startswith("adm:quickban:"))
async def cb_quickban(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("❌", show_alert=True)
        return
    name = call.data.split(":", 2)[2]
    await state.set_state(AdminState.waiting_ban_input)
    await state.update_data(prefill_name=name)
    await call.message.edit_text(
        f"🔨 <b>Бан игрока</b> <code>{name}</code>\n\n"
        "Отправь причину бана:\n"
        "<code>Читы</code>\n"
        "<code>X-Ray</code>\n"
        "<code>Дюп</code>\n\n"
        "pfid и xuid подставятся автоматически.\n\n"
        "Отправь /admin чтобы отменить.",
        reply_markup=back_kb(),
        parse_mode="HTML"
    )
    await call.answer()


# ─── Поиск по TG ─────────────────────────────────────────────────────────────

@dp.callback_query(F.data == "adm:search_tg")
async def cb_admin_search_tg(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("❌", show_alert=True)
        return
    await state.set_state(AdminState.waiting_search_tg)
    await call.message.edit_text(SEARCH_HELP, reply_markup=back_kb(), parse_mode="HTML")
    await call.answer()


@dp.message(AdminState.waiting_search_tg)
async def process_search_tg(msg: Message, state: FSMContext):
    if not is_admin(msg.from_user.id):
        return
    if msg.text and msg.text.startswith("/admin"):
        await state.clear()
        await msg.answer(admin_main_text(db), reply_markup=admin_main_kb(), parse_mode="HTML")
        return

    query = (msg.text or "").strip()
    result = None

    if query.isdigit():
        result = db.find_by_tg_id(query)
    else:
        result = db.find_by_tg_name(query.lstrip("@"))
        if not result:
            result = db.find_by_mc_name(query)

    await msg.answer(
        format_tg_search_result(result, query),
        reply_markup=back_kb(),
        parse_mode="HTML"
    )
    await state.clear()


# ─── Отвязка TG ──────────────────────────────────────────────────────────────

@dp.callback_query(F.data == "adm:unlink")
async def cb_admin_unlink(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("❌", show_alert=True)
        return
    await state.set_state(AdminState.waiting_unlink_input)
    await call.message.edit_text(UNLINK_HELP, reply_markup=back_kb(), parse_mode="HTML")
    await call.answer()


@dp.message(AdminState.waiting_unlink_input)
async def process_unlink_input(msg: Message, state: FSMContext):
    if not is_admin(msg.from_user.id):
        return
    if msg.text and msg.text.startswith("/admin"):
        await state.clear()
        await msg.answer(admin_main_text(db), reply_markup=admin_main_kb(), parse_mode="HTML")
        return

    query = (msg.text or "").strip().lstrip("@")

    # Пробуем по TG ID
    if query.isdigit():
        ok, user = db.unlink_tg(query)
        if ok:
            mc = user.get("mc_name") or "—"
            tg = user.get("tg_name") or "—"
            await msg.answer(
                f"✅ <b>TG отвязан успешно.</b>\n\n"
                f"🆔 TG ID: <code>{query}</code>\n"
                f"📱 TG ник: {tg}\n"
                f"🎮 MC ник: <code>{mc}</code>\n\n"
                "Пользователь может пройти верификацию заново.",
                reply_markup=back_kb(),
                parse_mode="HTML"
            )
        else:
            # Возможно это MC-ник из цифр — маловероятно, но проверим
            await msg.answer(
                f"❌ Пользователь с TG ID <code>{query}</code> не найден в базе.",
                reply_markup=back_kb(),
                parse_mode="HTML"
            )
        await state.clear()
        return

    # Пробуем по TG-нику
    user_by_tg = db.find_by_tg_name(query)
    if user_by_tg:
        tg_id = user_by_tg.get("tg_id", "")
        ok, user = db.unlink_tg(tg_id)
        if ok:
            mc = user.get("mc_name") or "—"
            await msg.answer(
                f"✅ <b>TG отвязан успешно.</b>\n\n"
                f"📱 TG ник: @{query}\n"
                f"🎮 MC ник: <code>{mc}</code>\n\n"
                "Пользователь может пройти верификацию заново.",
                reply_markup=back_kb(),
                parse_mode="HTML"
            )
        else:
            await msg.answer("❌ Ошибка при удалении.", reply_markup=back_kb(), parse_mode="HTML")
        await state.clear()
        return

    # Пробуем по MC-нику
    ok, tg_id, user = db.unlink_tg_by_mc(query)
    if ok:
        tg = user.get("tg_name") or "—"
        await msg.answer(
            f"✅ <b>TG отвязан успешно.</b>\n\n"
            f"🎮 MC ник: <code>{query}</code>\n"
            f"📱 TG ник: {tg}\n"
            f"🆔 TG ID: <code>{tg_id}</code>\n\n"
            "Пользователь может пройти верификацию заново.",
            reply_markup=back_kb(),
            parse_mode="HTML"
        )
    else:
        await msg.answer(
            f"❌ Пользователь <code>{query}</code> не найден.\n\n"
            "Попробуй TG ID, @ник или MC-ник.",
            reply_markup=back_kb(),
            parse_mode="HTML"
        )
    await state.clear()


# ─── Лог банов ───────────────────────────────────────────────────────────────

@dp.callback_query(F.data == "adm:banlog")
async def cb_admin_banlog(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("❌", show_alert=True)
        return
    bans = db.get_ban_log()
    await call.message.edit_text(
        format_ban_log(bans, limit=10),
        reply_markup=back_kb(),
        parse_mode="HTML"
    )
    await call.answer()


# ─── Статистика ──────────────────────────────────────────────────────────────

@dp.callback_query(F.data == "adm:stats")
async def cb_admin_stats(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("❌", show_alert=True)
        return
    stats   = db.get_stats()
    verified = db.get_all_verified()
    total   = stats.get("total", 0)
    ver_cnt = stats.get("verified", 0)

    recent = sorted(verified, key=lambda u: u.get("verified_at", ""), reverse=True)[:5]
    recent_lines = "\n".join(
        f"  • {u.get('tg_name','?')} → <code>{u.get('mc_name') or '—'}</code> ({u.get('verified_at','?')})"
        for u in recent
    ) or "  (нет)"

    text = (
        f"📊 <b>Статистика верификаций</b>\n\n"
        f"👤 Всего пользователей: <b>{total}</b>\n"
        f"✅ Верифицировано: <b>{ver_cnt}</b>\n"
        f"❌ Не верифицировано: <b>{total - ver_cnt}</b>\n\n"
        f"🕐 <b>Последние верификации:</b>\n{recent_lines}"
    )
    await call.message.edit_text(text, reply_markup=back_kb(), parse_mode="HTML")
    await call.answer()


# ═══════════════════════════════════════════════════════════════════════════════
# ТЕКСТОВЫЕ КОМАНДЫ МОДЕРАЦИИ
# ═══════════════════════════════════════════════════════════════════════════════

@dp.message(Command("ban"))
async def cmd_ban(msg: Message):
    if not is_admin(msg.from_user.id):
        return
    parts = msg.text.split(maxsplit=1)
    if len(parts) < 2:
        await msg.answer(
            "📋 <b>Использование:</b>\n"
            "/ban <code>Ник</code> <code>Причина</code>\n"
            "/ban <code>pfid:abc123</code> <code>Причина</code>\n"
            "/ban <code>xuid:253544</code> <code>Причина</code>",
            parse_mode="HTML"
        )
        return
    args = parse_mod_args(parts[1])
    wait = await msg.answer("⏳ Бан...")
    ok, err = await mod_action("ban", db=db, watcher=watcher,
        name=args.get("name"),
        pfid=args.get("pfid"),
        xuid=args.get("xuid"),
        reason=args.get("reason", "Нарушение правил"),
        by=msg.from_user.username or msg.from_user.first_name,
    )
    await wait.delete()
    target_str = args.get("name") or args.get("pfid") or args.get("xuid")
    if ok:
        db.add_ban_log(target_str, args.get("reason"), str(msg.from_user.id))
        await msg.answer(
            f"🔨 <b>Заблокирован!</b>\n\n"
            f"👤 Цель: <code>{target_str}</code>\n"
            f"📝 Причина: {args.get('reason')}",
            parse_mode="HTML"
        )
    else:
        await msg.answer(f"❌ Ошибка: {err}")


@dp.message(Command("unban"))
async def cmd_unban(msg: Message):
    if not is_admin(msg.from_user.id):
        return
    parts = msg.text.split(maxsplit=1)
    if len(parts) < 2:
        await msg.answer("📋 /unban <code>Ник/pfid:/xuid:</code>", parse_mode="HTML")
        return
    args = parse_mod_args(parts[1])
    wait = await msg.answer("⏳ Разбан...")
    ok, err = await mod_action(
        "unban",
        name=args.get("name"),
        pfid=args.get("pfid"),
        xuid=args.get("xuid"),
        by=msg.from_user.username or msg.from_user.first_name,
    )
    await wait.delete()
    target_str = args.get("name") or args.get("pfid") or args.get("xuid")
    if ok:
        await msg.answer(f"✅ <b>{target_str}</b> разблокирован!", parse_mode="HTML")
    else:
        await msg.answer(f"❌ Ошибка: {err}")


@dp.message(Command("kick"))
async def cmd_kick(msg: Message):
    if not is_admin(msg.from_user.id):
        return
    parts = msg.text.split(maxsplit=1)
    if len(parts) < 2:
        await msg.answer("📋 /kick <code>Ник</code> <code>Причина</code>", parse_mode="HTML")
        return
    args = parse_mod_args(parts[1])
    ok, err = await mod_action(
        "kick",
        name=args.get("name"),
        reason=args.get("reason", "Кик администратором"),
        by=msg.from_user.username or msg.from_user.first_name,
    )
    if ok:
        await msg.answer(f"👢 <b>{args.get('name','?')}</b> кикнут.", parse_mode="HTML")
    else:
        await msg.answer(f"❌ Ошибка: {err}")


@dp.message(Command("mute"))
async def cmd_mute(msg: Message):
    if not is_admin(msg.from_user.id):
        return
    parts = msg.text.split(maxsplit=1)
    if len(parts) < 2:
        await msg.answer(
            "📋 /mute <code>Ник</code> <code>минуты</code> <code>Причина</code>",
            parse_mode="HTML"
        )
        return
    args = parse_mod_args(parts[1])
    ok, err = await mod_action(
        "mute",
        name=args.get("name"),
        reason=args.get("reason", "Нарушение правил"),
        duration_min=args.get("duration_min", 60),
        by=msg.from_user.username or msg.from_user.first_name,
    )
    if ok:
        await msg.answer(
            f"🔇 <b>{args.get('name','?')}</b> заглушен на {args.get('duration_min', 60)} мин.",
            parse_mode="HTML"
        )
    else:
        await msg.answer(f"❌ Ошибка: {err}")


# ─── Неизвестные сообщения ───────────────────────────────────────────────────

@dp.message()
async def unknown(msg: Message, state: FSMContext):
    current = await state.get_state()
    if current:
        return
    uid = str(msg.from_user.id)
    data = db.get_user(uid)
    is_verified = bool(data and data.get("verified"))
    hint = " Или /admin для панели управления." if is_admin(msg.from_user.id) else ""
    await msg.answer(
        f"❓ Используйте меню ниже для верификации.{hint}",
        reply_markup=main_menu_kb(is_verified),
        parse_mode="HTML"
    )


# ─── Запуск ──────────────────────────────────────────────────────────────────

async def main():
    logger.info("Bot starting...")
    watcher.set_bot(bot)   # ← чтобы watcher мог слать уведомления в TG
    asyncio.create_task(watcher.run())
    logger.info("[bot] ptero_ws watcher запущен")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
