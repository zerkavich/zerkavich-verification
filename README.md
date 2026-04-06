# 🤖 ECON Verify Bot — Telegram верификация для Minecraft

Бот верификации игроков через Pterodactyl API.

---

## 🚀 Деплой на Railway (бесплатно)

### Шаг 1 — Создай бота в Telegram
1. Найди **@BotFather** в Telegram
2. `/newbot` → введи имя → получи токен вида `1234567890:AAA...`

### Шаг 2 — Загрузи код на GitHub
1. Зайди на [github.com](https://github.com) → **New repository**
2. Название: `econ-verify-bot` → **Create repository**
3. Загрузи все файлы из этой папки (кроме `.env`)

```bash
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/ТВО_НИК/econ-verify-bot.git
git push -u origin main
```

### Шаг 3 — Деплой на Railway
1. Зайди на [railway.app](https://railway.app) → **Login with GitHub**
2. **New Project** → **Deploy from GitHub repo**
3. Выбери `econ-verify-bot`
4. Railway автоматически обнаружит Python

### Шаг 4 — Переменные окружения
В Railway → твой проект → **Variables** → добавь:

| Переменная | Значение |
|---|---|
| `BOT_TOKEN` | Токен от @BotFather |
| `PTERODACTYL_URL` | `https://my.aurorix.net` |
| `PTERODACTYL_KEY` | Твой API ключ Pterodactyl |
| `SERVER_ID` | `6daf8160-16ab-4a5b-ac25-3e35cb75a3d4` |
| `TG_CHANNEL` | `@zerkavich` |
| `CHECK_SUBSCRIPTION` | `false` (или `true` если нужна проверка подписки) |

### Шаг 5 — Deploy!
Railway сам задеплоит. В логах должно появиться:
```
Bot starting...
INFO: Polling started
```

---

## ⚙️ Как это работает

```
Игрок в игре          Telegram бот           Pterodactyl API        Minecraft сервер
     │                     │                       │                      │
     │  .econ verify        │                       │                      │
     │─────────────────────>│ (показывает код)      │                      │
     │                     │                       │                      │
     │  /verify КОД        │                       │                      │
     │─────────────────────>│                       │                      │
     │                     │ POST /api/client/...  │                      │
     │                     │──────────────────────>│                      │
     │                     │                       │ scriptevent          │
     │                     │                       │─────────────────────>│
     │                     │                       │                      │ выдаёт титул + бонус
     │  ✅ Верифицирован!  │                       │                      │
     │<─────────────────────│                       │                      │
```

---

## 📋 Команды бота

| Команда | Описание |
|---|---|
| `/start` | Приветствие |
| `/verify КОД` | Верификация (код из `.econ verify` в игре) |
| `/status` | Проверить свой статус |
| `/help` | Инструкция |

---

## 🔑 Где взять API ключ Pterodactyl

1. Зайди в панель → правый верхний угол → **Account**
2. **API Credentials** → **Create new**
3. Описание: `TG Bot` → **Create**
4. Скопируй ключ — он показывается **один раз**!

---

## ❓ Проблемы

**Бот не отвечает** → проверь `BOT_TOKEN` в переменных Railway

**"Не удалось подключиться к серверу"** → проверь `PTERODACTYL_KEY` и что сервер запущен

**Верификация не приходит в игру** → проверь что аддон EA15_BP загружен и сервер запущен с ним
