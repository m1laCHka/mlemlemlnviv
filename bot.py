import os
import random
import asyncio
import datetime
import logging
from typing import Optional, Dict, Any, List

from aiogram import Bot, Dispatcher, Router, F
from aiogram.enums import ChatType
from aiogram.types import (
    Message, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton
)
from aiogram.filters import Command, CommandObject
from aiogram.fsm.storage.memory import MemoryStorage
import aiosqlite

# ---------------------------------------------------------
# НАСТРОЙКИ И ИНИЦИАЛИЗАЦИЯ
# ---------------------------------------------------------
logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
DB_PATH = os.getenv("DATABASE_PATH", "bot.db")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
router = Router()
dp.include_router(router)

# ---------------------------------------------------------
# ГЛОБАЛЬНЫЕ ИГРОВЫЕ СОСТОЯНИЯ (В ОЗУ)
# ---------------------------------------------------------
active_roulettes: Dict[int, dict] = {}
active_duels: Dict[str, dict] = {}
active_cats: Dict[int, dict] = {}

# ---------------------------------------------------------
# КЛАВИАТУРЫ (Нижнее меню ТОЛЬКО в ЛС)
# ---------------------------------------------------------
def get_main_keyboard() -> ReplyKeyboardMarkup:
    kb = [
        [KeyboardButton(text="🎰 Рулетка"), KeyboardButton(text="🤠 Дуэль"), KeyboardButton(text="🐱 Котики")],
        [KeyboardButton(text="🎰 Казино"), KeyboardButton(text="🎁 Приз"), KeyboardButton(text="🏪 Магазин")],
        [KeyboardButton(text="👤 Профиль"), KeyboardButton(text="📊 Статистика"), KeyboardButton(text="⭐ Перевод")],
        [KeyboardButton(text="🏆 Топ"), KeyboardButton(text="💍 Семья"), KeyboardButton(text="🎯 Квесты")],
        [KeyboardButton(text="🏆 Турнир"), KeyboardButton(text="❓ Помощь")]
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

# ---------------------------------------------------------
# БАЗА ДАННЫХ
# ---------------------------------------------------------
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            custom_nick TEXT DEFAULT NULL,
            custom_emoji TEXT DEFAULT '',
            coins INTEGER DEFAULT 1000,
            stars INTEGER DEFAULT 10,
            is_vip INTEGER DEFAULT 0,
            vip_until TEXT DEFAULT NULL,
            is_hidden INTEGER DEFAULT 0,
            insurance INTEGER DEFAULT 0,
            family_id INTEGER DEFAULT NULL,
            spouse_id INTEGER DEFAULT NULL,
            divorce_until TEXT DEFAULT NULL,
            daily_stars_transferred INTEGER DEFAULT 0,
            last_transfer_date TEXT DEFAULT NULL,
            last_prize_date TEXT DEFAULT NULL,
            wins INTEGER DEFAULT 0,
            losses INTEGER DEFAULT 0,
            total_games INTEGER DEFAULT 0,
            total_coins_won INTEGER DEFAULT 0,
            daily_net_win INTEGER DEFAULT 0,
            streak_days INTEGER DEFAULT 0,
            last_active_date TEXT DEFAULT NULL,
            last_game_result TEXT DEFAULT 'Нет',
            top3_family_count INTEGER DEFAULT 0,
            roulette_games INTEGER DEFAULT 0,
            roulette_wins INTEGER DEFAULT 0,
            duel_games INTEGER DEFAULT 0,
            duel_wins INTEGER DEFAULT 0,
            cat_games INTEGER DEFAULT 0,
            cat_wins INTEGER DEFAULT 0,
            casino_games INTEGER DEFAULT 0,
            casino_wins INTEGER DEFAULT 0,
            quests_completed INTEGER DEFAULT 0
        )
        """)

        await db.execute("""
        CREATE TABLE IF NOT EXISTS families (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user1_id INTEGER,
            user2_id INTEGER,
            score INTEGER DEFAULT 0,
            created_at TEXT,
            top1_count INTEGER DEFAULT 0,
            last_anniversary_month INTEGER DEFAULT 0
        )
        """)

        await db.execute("""
        CREATE TABLE IF NOT EXISTS children (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            family_id INTEGER,
            child_id INTEGER
        )
        """)

        await db.execute("""
        CREATE TABLE IF NOT EXISTS achievements (
            user_id INTEGER,
            ach_id TEXT,
            PRIMARY KEY (user_id, ach_id)
        )
        """)

        await db.execute("""
        CREATE TABLE IF NOT EXISTS user_quests (
            user_id INTEGER,
            quest_id INTEGER,
            progress INTEGER DEFAULT 0,
            completed INTEGER DEFAULT 0,
            PRIMARY KEY (user_id, quest_id)
        )
        """)

        await db.execute("""
        CREATE TABLE IF NOT EXISTS tournament (
            user_id INTEGER PRIMARY KEY,
            wins INTEGER DEFAULT 0,
            fee_paid INTEGER DEFAULT 0
        )
        """)

        await db.execute("""
        CREATE TABLE IF NOT EXISTS promos (
            code TEXT PRIMARY KEY,
            stars INTEGER DEFAULT 0,
            coins INTEGER DEFAULT 0,
            vip_days INTEGER DEFAULT 0,
            max_uses INTEGER DEFAULT 1,
            uses INTEGER DEFAULT 0
        )
        """)

        await db.execute("""
        CREATE TABLE IF NOT EXISTS user_promos (
            user_id INTEGER,
            code TEXT,
            PRIMARY KEY (user_id, code)
        )
        """)

        await db.commit()

async def get_or_create_user(user_id: int, username: Optional[str]) -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)) as cursor:
            user = await cursor.fetchone()
            today = datetime.date.today().isoformat()
            if not user:
                await db.execute(
                    "INSERT INTO users (user_id, username, last_active_date) VALUES (?, ?, ?)",
                    (user_id, username or f"Игрок_{user_id}", today)
                )
                await db.commit()
                async with db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)) as c2:
                    user = await c2.fetchone()
            else:
                if username and user['username'] != username:
                    await db.execute("UPDATE users SET username = ? WHERE user_id = ?", (username, user_id))
                    await db.commit()
            return dict(user)

# ---------------------------------------------------------
# РАНГИ И ТИТУЛЫ
# ---------------------------------------------------------
def calculate_rank(wins: int, total: int) -> str:
    if total < 10: return "🆕 Новобранец"
    winrate = (wins / total) * 100
    if winrate <= 20: return "🥚 Новичок"
    elif winrate <= 40: return "🪶 Любитель"
    elif winrate <= 55: return "⚔️ Боец"
    elif winrate <= 70: return "🛡️ Ветеран"
    elif winrate <= 85: return "🏅 Мастер"
    elif winrate <= 95: return "👑 Легенда"
    else: return "🌟 Бог игры"

async def get_user_titles(user: dict) -> list:
    titles = []
    if user['roulette_games'] >= 100: titles.append("🃏 Картёжник")
    if user['duel_wins'] >= 100: titles.append("🤠 Шериф")
    if user['cat_games'] >= 100: titles.append("🐱 Котолюб")
    if user['coins'] >= 50000: titles.append("💰 Миллионер")
    if user['stars'] >= 200: titles.append("⭐ Коллекционер")
    if user['casino_wins'] >= 50: titles.append("🎰 Казино-король")

    if user['family_id']:
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT created_at FROM families WHERE id = ?", (user['family_id'],)) as c:
                fam = await c.fetchone()
                if fam:
                    created = datetime.date.fromisoformat(fam['created_at'])
                    if (datetime.date.today() - created).days >= 365:
                        titles.append("👑 Золотая семья")
    return titles

async def check_achievements(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)) as c:
            u = await c.fetchone()
        if not u: return

        async with db.execute("SELECT ach_id FROM achievements WHERE user_id = ?", (user_id,)) as c:
            unlocked = {r['ach_id'] for r in await c.fetchall()}

        ach_data = [
            ("first_step", u['total_games'] >= 1, 10, 0, "🆕 Первый шаг"),
            ("first_win", u['wins'] >= 1, 20, 1, "🎯 Первый выигрыш"),
            ("wins_10", u['wins'] >= 10, 50, 2, "💪 10 побед"),
            ("wins_50", u['wins'] >= 50, 100, 5, "🏅 50 побед"),
            ("wins_100", u['wins'] >= 100, 200, 10, "👑 100 побед"),
            ("rouletto", u['roulette_wins'] >= 10, 50, 2, "🎰 Рулеточник"),
            ("duelist", u['duel_wins'] >= 10, 50, 2, "🤠 Дуэлянт"),
            ("catlover", u['cat_wins'] >= 10, 50, 2, "🐱 Котовод"),
            ("rich", u['coins'] >= 10000, 200, 5, "💰 Богач"),
            ("starry", u['stars'] >= 50, 0, 10, "⭐ Звёздный"),
            ("games_1000", u['total_games'] >= 1000, 1000, 20, "👾 1000 игр"),
            ("in_love", u['family_id'] is not None, 0, 5, "❤️ Любовь"),
        ]

        for ach_id, cond, r_coins, r_stars, title in ach_data:
            if cond and ach_id not in unlocked:
                await db.execute("INSERT INTO achievements (user_id, ach_id) VALUES (?, ?)", (user_id, ach_id))
                await db.execute("UPDATE users SET coins = coins + ?, stars = stars + ? WHERE user_id = ?", (r_coins, r_stars, user_id))
                await db.commit()
                try:
                    await bot.send_message(user_id, f"🏆 АЧИВКА! {title}\n+{r_coins}💰, +{r_stars}⭐")
                except Exception:
                    pass

# ---------------------------------------------------------
# ПОМОЩЬ
# ---------------------------------------------------------
HELP_TEXT = """🎮 ПОЛНЫЙ СПИСОК ВОЗМОЖНОСТЕЙ БОТА

⭐ Звёзды — ИГРОВАЯ ВАЛЮТА!

━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🎲 ИГРЫ
🎰 Рулетка — р / рулетка (красное, черное, чет, нечет, число)
🤠 Дуэль — дуэль (сумма) — пошагово
🐱 Котики — котики (ставка) — посчитай жёлтых 🐈
🎰 Казино — казино — за 25⭐

━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💰 ЭКОНОМИКА
🎁 Приз — приз (1 раз/день, ТОЛЬКО в ЛС)
🏪 Магазин — магазин
⭐ Перевод — перевод @username (сумма) (макс 25⭐/день)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━
👤 ПРОФИЛЬ И ТОПЫ
👤 Профиль — профиль / п
📊 Статистика — статистика / стата
🏆 Топы — топ, топ_семей

━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💍 СЕМЬЯ
💍 обручиться @username
👶 ребёнок @username
💔 развод
👨‍👩‍👧‍👦 семья
🎂 годовщина

━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🎯 КВЕСТЫ И ТУРНИРЫ
🎯 квесты
🏆 турнир
"""

@router.message(Command("start"))
@router.message(F.text.lower().in_(["помощь", "help", "❓ помощь"]))
async def cmd_help(message: Message):
    await get_or_create_user(message.from_user.id, message.from_user.username)
    if message.chat.type == ChatType.PRIVATE:
        await message.answer(HELP_TEXT, parse_mode="Markdown", reply_markup=get_main_keyboard())
    else:
        await message.answer(HELP_TEXT, parse_mode="Markdown")

# ============================================================
# 🎰 РУЛЕТКА
# ============================================================
@router.message(F.text.lower().startswith(("рулетка", "р", "🎰 рулетка")))
async def cmd_roulette(message: Message):
    chat_id = message.chat.id
    args = message.text.split()

    if len(args) < 3:
        await message.answer(
            "🎰 Ставка:\nр красное 100\nр 15 100\nр 0 100\nМин. 50💰, таймер 60 сек, макс 10 ставок/чат",
            parse_mode="Markdown"
        )
        return

    bet_type = args[1].lower()
    try:
        bet_amount = int(args[2])
    except ValueError:
        await message.answer("❌ Ставка должна быть числом!")
        return

    if bet_amount < 50:
        await message.answer("❌ Мин. ставка: 50 монет!")
        return

    user = await get_or_create_user(message.from_user.id, message.from_user.username)
    if user['coins'] < bet_amount:
        await message.answer("❌ Недостаточно монет!")
        return

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET coins = coins - ? WHERE user_id = ?", (bet_amount, user['user_id']))
        await db.commit()

    if chat_id not in active_roulettes:
        active_roulettes[chat_id] = {"bets": [], "timer_task": asyncio.create_task(run_roulette_timer(chat_id))}

    roul = active_roulettes[chat_id]
    if len(roul["bets"]) >= 10:
        await message.answer("❌ Лимит 10 ставок!")
        return

    uname = user['custom_nick'] or user['username'] or f"Игрок_{user['user_id']}"
    roul["bets"].append({
        "user_id": user['user_id'],
        "username": uname,
        "type": bet_type,
        "amount": bet_amount,
        "insurance": user['insurance']
    })

    await message.answer(f"✅ Ставка {uname} — {bet_amount}💰 на {bet_type}! ({len(roul['bets'])}/10)", parse_mode="Markdown")

async def run_roulette_timer(chat_id: int):
    await asyncio.sleep(60)
    if chat_id not in active_roulettes: return

    roul = active_roulettes.pop(chat_id)
    bets = roul["bets"]

    winning_number = random.randint(0, 30)
    is_zero = (winning_number == 0)
    is_even = (winning_number % 2 == 0) if not is_zero else None
    is_red = (winning_number % 2 != 0) if not is_zero else None

    color_str = "🟢 0" if is_zero else ("🔴 " + str(winning_number) if is_red else "⚫ " + str(winning_number))
    res_text = f"🎰 РУЛЕТКА! Выпало: {color_str}\n\n"

    async with aiosqlite.connect(DB_PATH) as db:
        for b in bets:
            uid, uname, b_type, amount = b["user_id"], b["username"], b["type"], b["amount"]
            won, mult = False, 0

            if b_type in ["красное", "red"] and is_red: won, mult = True, 2
            elif b_type in ["черное", "black"] and (not is_red and not is_zero): won, mult = True, 2
            elif b_type in ["чет", "even"] and is_even: won, mult = True, 2
            elif b_type in ["нечет", "odd"] and (not is_even and not is_zero): won, mult = True, 2
            elif b_type == "0" and is_zero: won, mult = True, 100
            elif b_type.isdigit() and int(b_type) == winning_number: won, mult = True, 50

            if won:
                win_coins = amount * mult
                await db.execute("""UPDATE users SET coins = coins + ?, wins = wins + 1, total_games = total_games + 1,
                    roulette_games = roulette_games + 1, roulette_wins = roulette_wins + 1, total_coins_won = total_coins_won + ?
                    WHERE user_id = ?""", (win_coins, win_coins, uid))
                res_text += f"✅ {uname}: +{win_coins}💰\n"
            else:
                refund = (amount // 2) if b["insurance"] == 1 else 0
                if refund > 0:
                    await db.execute("UPDATE users SET coins = coins + ?, insurance = 0 WHERE user_id = ?", (refund, uid))

                await db.execute("UPDATE users SET losses = losses + 1, total_games = total_games + 1, roulette_games = roulette_games + 1 WHERE user_id = ?", (uid,))
                res_text += f"❌ {uname}: -{amount}💰 {'(страховка)' if refund else ''}\n"

        await db.commit()
    await bot.send_message(chat_id, res_text, parse_mode="Markdown")

# ============================================================
# 🤠 ДУЭЛЬ (ПОШАГОВАЯ)
# ============================================================
@router.message(F.text.lower().startswith(("дуэль", "🤠 дуэль")))
async def cmd_duel(message: Message):
    args = message.text.split()
    if len(args) < 2:
        await message.answer("🤠 Формат: дуэль 100", parse_mode="Markdown")
        return
    try:
        amount = int(args[1])
    except ValueError:
        return

    user = await get_or_create_user(message.from_user.id, message.from_user.username)
    if user['coins'] < amount:
        await message.answer("❌ Недостаточно монет!")
        return

    duel_id = f"{message.chat.id}_{message.from_user.id}_{random.randint(100,999)}"
    uname = user['custom_nick'] or user['username'] or message.from_user.first_name

    active_duels[duel_id] = {
        "p1": user['user_id'],
        "p1_name": uname,
        "p2": None,
        "p2_name": None,
        "amount": amount,
        "chat_id": message.chat.id
    }

    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=f"🎯 Принять дуэль ({amount}💰)", callback_data=f"accept_duel_{duel_id}")
    ]])
    await message.answer(f"🤠 {uname} вызывает на дуэль! Ставка: {amount}💰", reply_markup=kb, parse_mode="Markdown")

@router.callback_query(F.data.startswith("accept_duel_"))
async def process_accept_duel(callback: CallbackQuery):
    duel_id = callback.data.replace("accept_duel_", "")
    if duel_id not in active_duels: return

    duel = active_duels[duel_id]
    p1_id, p2_id = duel["p1"], callback.from_user.id
    if p1_id == p2_id:
        await callback.answer("Нельзя с собой!", show_alert=True)
        return

    p2_user = await get_or_create_user(p2_id, callback.from_user.username)
    if p2_user['coins'] < duel["amount"]:
        await callback.answer("Недостаточно монет!", show_alert=True)
        return

    p2_name = p2_user['custom_nick'] or p2_user['username'] or callback.from_user.first_name
    duel["p2"] = p2_id
    duel["p2_name"] = p2_name
    duel["turn"] = p1_id
    duel["p1_hp"] = 100
    duel["p2_hp"] = 100

    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="💥 ВЫСТРЕЛ!", callback_data=f"shoot_{duel_id}")
    ]])
    await callback.message.edit_text(
        f"🤠 ДУЭЛЬ!\n{duel['p1_name']} vs {p2_name}\n\n👉 Ход: {duel['p1_name']}",
        reply_markup=kb, parse_mode="Markdown"
    )

@router.callback_query(F.data.startswith("shoot_"))
async def process_shoot(callback: CallbackQuery):
    duel_id = callback.data.replace("shoot_", "")
    if duel_id not in active_duels: return

    duel = active_duels[duel_id]
    shooter_id = callback.from_user.id
    if shooter_id != duel["turn"]:
        await callback.answer("Не твой ход!", show_alert=True)
        return

    is_p1 = (shooter_id == duel["p1"])
    target_id = duel["p2"] if is_p1 else duel["p1"]
    shooter_name = duel["p1_name"] if is_p1 else duel["p2_name"]
    target_name = duel["p2_name"] if is_p1 else duel["p1_name"]

    damage = random.randint(25, 55)
    if is_p1:
        duel["p2_hp"] -= damage
        target_hp = max(0, duel["p2_hp"])
    else:
        duel["p1_hp"] -= damage
        target_hp = max(0, duel["p1_hp"])

    if target_hp <= 0:
        winner_id, loser_id = shooter_id, target_id
        tot_pot = duel["amount"] * 2
        win_amt = int(tot_pot * 0.9)

        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("UPDATE users SET coins = coins - ?, losses = losses + 1, duel_games = duel_games + 1 WHERE user_id = ?", (duel["amount"], loser_id))
            await db.execute("UPDATE users SET coins = coins + ?, wins = wins + 1, duel_games = duel_games + 1, duel_wins = duel_wins + 1 WHERE user_id = ?", (win_amt - duel["amount"], winner_id))
            await db.commit()

        del active_duels[duel_id]
        await callback.message.edit_text(f"💀 {shooter_name} убивает {target_name}!\n🏆 {shooter_name} +{win_amt}💰", parse_mode="Markdown")
    else:
        duel["turn"] = target_id
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="💥 ВЫСТРЕЛ!", callback_data=f"shoot_{duel_id}")
        ]])
        await callback.message.edit_text(
            f"🔫 {shooter_name} наносит {damage} урона!\n"
            f"{duel['p1_name']}: {duel['p1_hp']} HP\n"
            f"{duel['p2_name']}: {duel['p2_hp']} HP\n\n👉 Ход: {target_name}",
            reply_markup=kb, parse_mode="Markdown"
        )

# ============================================================
# 🐱 КОТИКИ
# ============================================================
@router.message(F.text.lower().startswith(("котики", "🐱 котики")))
async def cmd_cats(message: Message):
    chat_id = message.chat.id
    args = message.text.split()
    bet = int(args[1]) if len(args) > 1 and args[1].isdigit() else 100

    user = await get_or_create_user(message.from_user.id, message.from_user.username)
    if user['coins'] < bet:
        await message.answer("❌ Недостаточно монет!")
        return

    yellow_cats = random.randint(1, 40)
    black_cats = random.randint(0, 15)
    cats_list = ["🐈"] * yellow_cats + ["🐈‍⬛"] * black_cats
    random.shuffle(cats_list)

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET coins = coins - ? WHERE user_id = ?", (bet, user['user_id']))
        await db.commit()

    active_cats[chat_id] = {"count": yellow_cats, "pot": bet, "attempts": {}, "active": True}

    text = f"🐱 Считай жёлтых 🐈 (чёрные 🐈‍⬛ не считаем!)\nСтавка: {bet}💰\n\n" + "".join(cats_list)
    await message.answer(text, parse_mode="Markdown")

@router.message(F.text.isdigit())
async def process_cats_answer(message: Message):
    chat_id = message.chat.id
    if chat_id not in active_cats or not active_cats[chat_id]["active"]: return

    game = active_cats[chat_id]
    user_id = message.from_user.id
    if game["attempts"].get(user_id, 0) >= 3: return

    game["attempts"][user_id] = game["attempts"].get(user_id, 0) + 1
    val = int(message.text)

    if val == game["count"]:
        game["active"] = False
        win_pot = int(game["pot"] * 1.8)
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("""UPDATE users SET coins = coins + ?, wins = wins + 1, total_games = total_games + 1,
                cat_games = cat_games + 1, cat_wins = cat_wins + 1 WHERE user_id = ?""", (win_pot, user_id))
            await db.commit()
        await message.answer(f"🎉 Правильно! Жёлтых котиков было {val}.\n+{win_pot}💰", parse_mode="Markdown")

# ============================================================
# 🎰 КАЗИНО
# ============================================================
@router.message(F.text.lower().in_(["казино", "🎰 казино"]))
async def cmd_casino(message: Message):
    user = await get_or_create_user(message.from_user.id, message.from_user.username)
    if user['stars'] < 25:
        await message.answer("❌ Нужно 25⭐!")
        return

    rand = random.random()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET stars = stars - 25, casino_games = casino_games + 1 WHERE user_id = ?", (user['user_id'],))

        if rand < 0.40:
            win_c = random.randint(50, 3000)
            await db.execute("UPDATE users SET coins = coins + ?, wins = wins + 1 WHERE user_id = ?", (win_c, user['user_id']))
            res = f"💰 +{win_c} монет!"
        elif rand < 0.70:
            days = random.randint(4, 10)
            await db.execute("UPDATE users SET is_vip = 1, wins = wins + 1 WHERE user_id = ?", (user['user_id'],))
            res = f"👑 VIP на {days} дней!"
        elif rand < 0.99:
            win_s = random.randint(10, 75)
            await db.execute("UPDATE users SET stars = stars + ?, wins = wins + 1 WHERE user_id = ?", (user['user_id'],))
            res = f"⭐ +{win_s} звёзд!"
        else:
            await db.execute("UPDATE users SET coins = coins + 5000, stars = stars + 50, is_vip = 1, wins = wins + 1 WHERE user_id = ?", (user['user_id'],))
            res = "🔥 ДЖЕКПОТ! 5000💰 + 50⭐ + VIP!"

        await db.commit()
    await message.answer(f"🎰 Казино (-25⭐)\n\n{res}", parse_mode="Markdown")

# ============================================================
# 🎁 ПРИЗ (ТОЛЬКО В ЛС)
# ============================================================
@router.message(F.text.lower().in_(["приз", "🎁 приз"]))
async def cmd_prize(message: Message):
    if message.chat.type != ChatType.PRIVATE:
        await message.answer("⚠️ Зайди в ЛС к боту!")
        return

    user = await get_or_create_user(message.from_user.id, message.from_user.username)
    today = datetime.date.today().isoformat()

    if user['last_prize_date'] == today:
        await message.answer("⏳ Уже сегодня!")
        return

    buttons = [[InlineKeyboardButton(text=f"📦 Сундук #{i}", callback_data=f"chest_{i}")] for i in range(1, 6)]
    await message.answer("🎁 Выбери сундук:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode="Markdown")

@router.callback_query(F.data.startswith("chest_"))
async def process_chest(callback: CallbackQuery):
    user = await get_or_create_user(callback.from_user.id, callback.from_user.username)
    today = datetime.date.today().isoformat()
    if user['last_prize_date'] == today:
        await callback.answer("Уже сегодня!", show_alert=True)
        return

    chosen = int(callback.data.split("_")[1])
    rewards = []
    for _ in range(5):
        r = random.random()
        if r < 0.30: rewards.append((200, 0))
        elif r < 0.55: rewards.append((500, 0))
        elif r < 0.75: rewards.append((800, 0))
        elif r < 0.90: rewards.append((1000, 1))
        else: rewards.append((1500, 5))

    win_coins, win_stars = rewards[chosen - 1]

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET coins = coins + ?, stars = stars + ?, last_prize_date = ? WHERE user_id = ?",
                         (win_coins, win_stars, today, callback.from_user.id))
        await db.commit()

    text = f"🎉 Сундук #{chosen}\n+{win_coins}💰 +{win_stars}⭐\n\nОстальные:\n"
    for idx, (c, s) in enumerate(rewards, 1):
        text += f"📦 #{idx}: {c}💰 {s}⭐ {'👈' if idx == chosen else ''}\n"

    await callback.message.edit_text(text, parse_mode="Markdown")

# ============================================================
# ⭐ ПЕРЕВОД
# ============================================================
@router.message(F.text.lower().startswith("перевод"))
async def cmd_transfer(message: Message):
    args = message.text.split()
    if len(args) < 3:
        await message.answer("❌ Формат: перевод @username 10", parse_mode="Markdown")
        return

    target_uname = args[1].replace("@", "")
    try:
        amount = int(args[2])
    except ValueError:
        await message.answer("❌ Сумма должна быть числом!")
        return

    sender = await get_or_create_user(message.from_user.id, message.from_user.username)
    today = datetime.date.today().isoformat()
    transferred = sender['daily_stars_transferred'] if sender['last_transfer_date'] == today else 0

    if transferred + amount > 25:
        await message.answer(f"❌ Лимит 25⭐/день! Уже {transferred}⭐")
        return

    if sender['stars'] < amount:
        await message.answer("❌ Недостаточно звёзд!")
        return

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users WHERE username = ?", (target_uname,)) as c:
            target = await c.fetchone()
        if not target:
            await message.answer("❌ Пользователь не найден!")
            return

        await db.execute("UPDATE users SET stars = stars - ?, daily_stars_transferred = ?, last_transfer_date = ? WHERE user_id = ?",
                         (amount, transferred + amount, today, message.from_user.id))
        await db.execute("UPDATE users SET stars = stars + ? WHERE user_id = ?", (amount, target['user_id']))
        await db.commit()

    await message.answer(f"✅ +{amount}⭐ для @{target_uname}!")

# ============================================================
# 🏪 МАГАЗИН
# ============================================================
@router.message(F.text.lower().in_(["магазин", "🏪 магазин"]))
async def cmd_shop(message: Message):
    text = """🏪 МАГАЗИН

👑 VIP 1д — 5⭐
👑 VIP 7д — 30⭐
🛡️ Страховка — 5⭐
💱 1⭐ → 50💰
✏️ Сменить ник — сменить_ник @ник (10⭐)
🔒 Скрыть профиль — скрыть_профиль (1000⭐)
🔓 Открыть профиль — открыть_профиль"""
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👑 VIP 1д (5⭐)", callback_data="buy_vip_1"),
         InlineKeyboardButton(text="👑 VIP 7д (30⭐)", callback_data="buy_vip_7")],
        [InlineKeyboardButton(text="🛡️ Страховка (5⭐)", callback_data="buy_ins"),
         InlineKeyboardButton(text="💱 1⭐→50💰", callback_data="buy_ex")]
    ])
    await message.answer(text, parse_mode="Markdown", reply_markup=kb)

@router.callback_query(F.data.startswith("buy_"))
async def process_shop(callback: CallbackQuery):
    user = await get_or_create_user(callback.from_user.id, callback.from_user.username)
    async with aiosqlite.connect(DB_PATH) as db:
        if callback.data == "buy_vip_1" and user['stars'] >= 5:
            await db.execute("UPDATE users SET stars = stars - 5, is_vip = 1 WHERE user_id = ?", (user['user_id'],))
            await callback.answer("✅ VIP 1 день!", show_alert=True)
        elif callback.data == "buy_vip_7" and user['stars'] >= 30:
            await db.execute("UPDATE users SET stars = stars - 30, is_vip = 1 WHERE user_id = ?", (user['user_id'],))
            await callback.answer("✅ VIP 7 дней!", show_alert=True)
        elif callback.data == "buy_ins" and user['stars'] >= 5:
            await db.execute("UPDATE users SET stars = stars - 5, insurance = 1 WHERE user_id = ?", (user['user_id'],))
            await callback.answer("✅ Страховка!", show_alert=True)
        elif callback.data == "buy_ex" and user['stars'] >= 1:
            await db.execute("UPDATE users SET stars = stars - 1, coins = coins + 50 WHERE user_id = ?", (user['user_id'],))
            await callback.answer("✅ +50💰!", show_alert=True)
        else:
            await callback.answer("❌ Недостаточно звёзд!", show_alert=True)
            return
        await db.commit()

# ============================================================
# ✏️ СМЕНА НИКА
# ============================================================
@router.message(F.text.lower().startswith("сменить_ник"))
async def cmd_change_nick(message: Message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer("❌ Формат: сменить_ник @новый_ник", parse_mode="Markdown")
        return

    new_nick = args[1].strip()
    if new_nick.startswith("@"):
        new_nick = new_nick[1:]

    user = await get_or_create_user(message.from_user.id, message.from_user.username)
    if user['stars'] < 10:
        await message.answer("❌ Нужно 10⭐!")
        return

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET stars = stars - 10, custom_nick = ? WHERE user_id = ?", (new_nick, message.from_user.id))
        await db.commit()

    await message.answer(f"✅ Ник изменён на: {new_nick}")

# ============================================================
# 🔒 СКРЫТИЕ ПРОФИЛЯ
# ============================================================
@router.message(F.text.lower() == "скрыть_профиль")
async def cmd_hide_profile(message: Message):
    user = await get_or_create_user(message.from_user.id, message.from_user.username)
    if user['stars'] < 1000:
        await message.answer("❌ Нужно 1000⭐!")
        return

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET stars = stars - 1000, is_hidden = 1 WHERE user_id = ?", (message.from_user.id,))
        await db.commit()

    await message.answer("🔒 Профиль скрыт за 1000⭐!")

@router.message(F.text.lower() == "открыть_профиль")
async def cmd_unhide_profile(message: Message):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET is_hidden = 0 WHERE user_id = ?", (message.from_user.id,))
        await db.commit()
    await message.answer("🔓 Профиль открыт!")

# ============================================================
# 👤 ПРОФИЛЬ
# ============================================================
@router.message(F.text.lower().startswith(("профиль", "п")))
async def cmd_profile(message: Message):
    user = await get_or_create_user(message.from_user.id, message.from_user.username)
    rank = calculate_rank(user['wins'], user['total_games'])
    display_name = user['custom_nick'] or user['username'] or f"Игрок_{user['user_id']}"

    games_data = [
        ("🎰 Рулетка", user['roulette_wins'], user['roulette_games']),
        ("🤠 Дуэль", user['duel_wins'], user['duel_games']),
        ("🐱 Котики", user['cat_wins'], user['cat_games']),
        ("🎰 Казино", user['casino_wins'], user['casino_games']),
    ]

    stats_text = ""
    best_game = "Нет игр"
    best_ratio = 0.0

    for name, wins, total in games_data:
        if total > 0:
            ratio = round((wins / total) * 100, 1)
            stats_text += f"{name}: {wins}/{total} ({ratio}%)\n"
            if ratio > best_ratio:
                best_ratio = ratio
                best_game = name
        else:
            stats_text += f"{name}: 0/0 (0%)\n"

    titles = await get_user_titles(user)
    titles_str = ", ".join(titles) if titles else "Нет"

    text = (
        f"👤 Профиль: {display_name}\n"
        f"🎖️ Ранг: {rank}\n"
        f"💳 Статус: {'👑 VIP' if user['is_vip'] else 'Обычный'}\n"
        f"💰 Монеты: {user['coins']:,}\n"
        f"⭐ Звёзды: {user['stars']}\n"
        f"🎖️ Титулы: {titles_str}\n"
        f"📊 Всего игр: {user['total_games']}\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"📈 Статистика по играм:\n{stats_text}\n"
        f"🏆 Лучшая игра: {best_game} ({best_ratio}%)"
    )

    reply_markup = get_main_keyboard() if message.chat.type == ChatType.PRIVATE else None
    await message.answer(text, parse_mode="Markdown", reply_markup=reply_markup)

# ============================================================
# 📊 СТАТИСТИКА
# ============================================================
@router.message(F.text.lower().in_(["статистика", "стата"]))
async def cmd_stats(message: Message):
    u = await get_or_create_user(message.from_user.id, message.from_user.username)
    text = f"""📊 СТАТИСТИКА

🎰 Рулетка: {u['roulette_wins']}/{u['roulette_games']} побед
🤠 Дуэль: {u['duel_wins']}/{u['duel_games']} побед
🐱 Котики: {u['cat_wins']}/{u['cat_games']} побед
🎰 Казино: {u['casino_wins']}/{u['casino_games']} побед

🔥 Всего игр: {u['total_games']}
🏆 Побед: {u['wins']}
💀 Поражений: {u['losses']}"""
    await message.answer(text, parse_mode="Markdown")

# ============================================================
# 🏆 ТОПЫ
# ============================================================
@router.message(F.text.lower().in_(["топ", "🏆 топ"]))
async def cmd_top(message: Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="💍 Топ Семей", callback_data="top_families")
    ]])
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT user_id, username, custom_nick, wins, coins FROM users ORDER BY wins DESC LIMIT 10") as c:
            rows = await c.fetchall()

    text = "🏆 ТОП ИГРОКОВ:\n\n"
    for idx, r in enumerate(rows, start=1):
        name = r['custom_nick'] or r['username'] or f"Игрок_{r['user_id']}"
        text += f"{idx}. {name} — {r['wins']} побед ({r['coins']}💰)\n"
    await message.answer(text, parse_mode="Markdown", reply_markup=kb)

@router.callback_query(F.data == "top_families")
@router.message(F.text.lower() == "топ_семей")
async def cmd_top_families(event):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT id, score, created_at FROM families ORDER BY score DESC LIMIT 10") as c:
            rows = await c.fetchall()

    text = "💍 ТОП СЕМЕЙ:\n\n"
    for idx, r in enumerate(rows, start=1):
        text += f"{idx}. Семья #{r['id']} — {r['score']} очков ({r['created_at']})\n"

    if isinstance(event, CallbackQuery):
        await event.message.edit_text(text, parse_mode="Markdown")
    else:
        await event.answer(text, parse_mode="Markdown")

# ============================================================
# 🎯 КВЕСТЫ
# ============================================================
@router.message(F.text.lower().in_(["квесты", "🎯 квесты"]))
async def cmd_quests(message: Message):
    text = """🎯 ЕЖЕНЕДЕЛЬНЫЕ КВЕСТЫ

1. Сыграть 10 игр — 30💰
2. Выиграть 5 дуэлей — 2⭐
3. Угадать число в рулетке — 5⭐
4. Сыграть в котиков 3 раза — 20💰
5. Выиграть 500 монет за день — 3⭐

🎁 Бонус за все 5: +10⭐!"""
    await message.answer(text, parse_mode="Markdown")

# ============================================================
# 🏆 ТУРНИРЫ
# ============================================================
@router.message(F.text.lower().in_(["турнир", "🏆 турнир"]))
async def cmd_tournament(message: Message):
    text = """🏆 ЕЖЕНЕДЕЛЬНЫЙ ТУРНИР

🗓️ Пн - Вс
💰 Призовой фонд: 500⭐ + взносы

🥇 1 место — 60%
🥈 2 место — 30%
🥉 3 место — 10%

🎟️ Участие: 10⭐"""
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🎟️ Участвовать (10⭐)", callback_data="join_tournament")
    ]])
    await message.answer(text, parse_mode="Markdown", reply_markup=kb)

@router.callback_query(F.data == "join_tournament")
async def process_join_tournament(callback: CallbackQuery):
    user = await get_or_create_user(callback.from_user.id, callback.from_user.username)
    if user['stars'] < 10:
        await callback.answer("❌ Нужно 10⭐!", show_alert=True)
        return

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET stars = stars - 10 WHERE user_id = ?", (user['user_id'],))
        await db.execute("INSERT OR REPLACE INTO tournament (user_id, fee_paid) VALUES (?, 1)", (user['user_id'],))
        await db.commit()

    await callback.answer("✅ Ты в турнире!", show_alert=True)

# ============================================================
# 💍 СЕМЬЯ
# ============================================================
@router.message(F.text.lower().startswith("обручиться"))
async def cmd_marry(message: Message):
    if not message.reply_to_message:
        await message.answer("❌ Ответь на сообщение!")
        return

    u1 = await get_or_create_user(message.from_user.id, message.from_user.username)
    u2 = await get_or_create_user(message.reply_to_message.from_user.id, message.reply_to_message.from_user.username)

    if u1['family_id'] or u2['family_id']:
        await message.answer("❌ Кто-то уже в браке!")
        return

    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="❤️ Да", callback_data=f"marry_{u1['user_id']}_{u2['user_id']}"),
        InlineKeyboardButton(text="💔 Нет", callback_data="marry_no")
    ]])
    await message.answer(f"💍 {message.reply_to_message.from_user.first_name}, согласие?", reply_markup=kb)

@router.callback_query(F.data.startswith("marry_"))
async def process_marry(callback: CallbackQuery):
    if callback.data == "marry_no":
        await callback.message.delete()
        return

    _, u1, u2 = callback.data.split("_")
    u1, u2 = int(u1), int(u2)

    if callback.from_user.id != u2:
        await callback.answer("Не тебе!", show_alert=True)
        return

    today = datetime.date.today().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        c = await db.execute("INSERT INTO families (user1_id, user2_id, created_at) VALUES (?, ?, ?)", (u1, u2, today))
        fam_id = c.lastrowid
        await db.execute("UPDATE users SET family_id = ?, spouse_id = ?, stars = stars + 5 WHERE user_id IN (?, ?)", (fam_id, u2, u1, u2))
        await db.commit()

    await callback.message.edit_text("🎉 СЕМЬЯ СОЗДАНА! +5⭐ каждому!", parse_mode="Markdown")

@router.message(F.text.lower() == "развод")
async def cmd_divorce(message: Message):
    user = await get_or_create_user(message.from_user.id, message.from_user.username)
    if not user['family_id']:
        await message.answer("❌ Ты не в браке!")
        return

    ban_date = (datetime.date.today() + datetime.timedelta(days=7)).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM families WHERE id = ?", (user['family_id'],))
        await db.execute("UPDATE users SET family_id = NULL, spouse_id = NULL, divorce_until = ? WHERE user_id IN (?, ?)",
                         (ban_date, user['user_id'], user['spouse_id']))
        await db.commit()

    await message.answer("💔 Развод! Бан на брак 7 дней.")

@router.message(F.text.lower().startswith("ребёнок"))
async def cmd_adopt(message: Message):
    if not message.reply_to_message:
        await message.answer("❌ Ответь на сообщение!")
        return

    parent = await get_or_create_user(message.from_user.id, message.from_user.username)
    child = await get_or_create_user(message.reply_to_message.from_user.id, message.reply_to_message.from_user.username)

    if not parent['family_id']:
        await message.answer("❌ Сначала семья!")
        return

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO children (family_id, child_id) VALUES (?, ?)", (parent['family_id'], child['user_id']))
        await db.commit()

    await message.answer(f"👶 {message.reply_to_message.from_user.first_name} усыновлён!")

@router.message(F.text.lower() == "семья")
async def cmd_family(message: Message):
    user = await get_or_create_user(message.from_user.id, message.from_user.username)
    if not user['family_id']:
        await message.answer("❌ Нет семьи!")
        return

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM families WHERE id = ?", (user['family_id'],)) as c:
            fam = await c.fetchone()

    text = f"""💍 СЕМЬЯ
📅 Создана: {fam['created_at']}
⭐ Очки: {fam['score']}
🎖️ Топ-1 раз: {fam['top1_count']}"""
    await message.answer(text, parse_mode="Markdown")

# ============================================================
# 🎂 ГОДОВЩИНА
# ============================================================
@router.message(F.text.lower() == "годовщина")
async def cmd_anniversary(message: Message):
    user = await get_or_create_user(message.from_user.id, message.from_user.username)
    if not user['family_id']:
        await message.answer("❌ Нет семьи!")
        return

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT created_at, last_anniversary_month FROM families WHERE id = ?", (user['family_id'],)) as c:
            fam = await c.fetchone()

    created = datetime.date.fromisoformat(fam['created_at'])
    months = (datetime.date.today().year - created.year) * 12 + datetime.date.today().month - created.month

    if months > fam['last_anniversary_month']:
        reward = min(5 * months, 50)
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("UPDATE families SET last_anniversary_month = ? WHERE id = ?", (months, user['family_id']))
            await db.execute("UPDATE users SET stars = stars + ? WHERE user_id IN (?, ?)", (reward, user['user_id'], user['spouse_id']))
            await db.commit()
        await message.answer(f"🎂 ГОДОВЩИНА! {months} месяцев! +{reward}⭐ каждому!", parse_mode="Markdown")
    else:
        await message.answer(f"📅 Вместе {months} месяцев. Следующая годовщина через {months - fam['last_anniversary_month']} мес.")

# ============================================================
# 🎫 ПРОМОКОДЫ (АДМИН)
# ============================================================
@router.message(Command("create_promo"))
async def cmd_create_promo(message: Message, command: CommandObject):
    if message.from_user.id != ADMIN_ID: return
    args = command.args.split() if command.args else []
    if len(args) < 3:
        await message.answer("Формат: /create_promo КОД ЗВЕЗДЫ МОНЕТЫ", parse_mode="Markdown")
        return
    code, stars, coins = args[0], int(args[1]), int(args[2])
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR REPLACE INTO promos (code, stars, coins, max_uses) VALUES (?, ?, ?, 100)", (code, stars, coins))
        await db.commit()
    await message.answer(f"✅ Промокод {code} создан!", parse_mode="Markdown")

@router.message(Command("promo"))
async def cmd_use_promo(message: Message, command: CommandObject):
    if not command.args: return
    code = command.args.strip()
    uid = message.from_user.id
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM promos WHERE code = ?", (code,)) as c:
            p = await c.fetchone()
        if not p:
            await message.answer("❌ Нет такого промокода!")
            return
        await db.execute("UPDATE users SET stars = stars + ?, coins = coins + ? WHERE user_id = ?", (p['stars'], p['coins'], uid))
        await db.commit()
    await message.answer(f"🎉 +{p['coins']}💰 +{p['stars']}⭐!")

# ============================================================
# 🚀 ЗАПУСК
# ============================================================
async def main():
    await init_db()
    print("🤖 БОТ ЗАПУЩЕН! ВСЁ РАБОТАЕТ!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
