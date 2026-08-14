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
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
import aiosqlite

# ---------------------------------------------------------
# НАСТРОЙКИ
# ---------------------------------------------------------
logging.basicConfig(level=logging.INFO)
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
DB_PATH = os.getenv("DATABASE_PATH", "bot.db")
CHAT_ID = 4358492509

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
router = Router()
dp.include_router(router)

# ---------------------------------------------------------
# ГЛОБАЛЬНЫЕ СОСТОЯНИЯ
# ---------------------------------------------------------
active_roulettes: Dict[int, dict] = {}
active_duels: Dict[str, dict] = {}
active_cats: Dict[int, dict] = {}
family_name_requests: Dict[int, dict] = {}
family_name_change_requests: Dict[int, dict] = {}

# ---------------------------------------------------------
# КЛАВИАТУРЫ
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
class FamilyNameState(StatesGroup):
    waiting_for_name = State()

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            custom_nick TEXT DEFAULT NULL,
            custom_emoji TEXT DEFAULT '',
            gender TEXT DEFAULT NULL,
            coins INTEGER DEFAULT 1000,
            stars INTEGER DEFAULT 10,
            is_vip INTEGER DEFAULT 0,
            vip_until TEXT DEFAULT NULL,
            is_hidden INTEGER DEFAULT 0,
            insurance INTEGER DEFAULT 0,
            family_id INTEGER DEFAULT NULL,
            family_name TEXT DEFAULT NULL,
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
            quests_completed INTEGER DEFAULT 0,
            quest1 INTEGER DEFAULT 0,
            quest2 INTEGER DEFAULT 0,
            quest3 INTEGER DEFAULT 0,
            quest4 INTEGER DEFAULT 0,
            quest5 INTEGER DEFAULT 0,
            quest1_done INTEGER DEFAULT 0,
            quest2_done INTEGER DEFAULT 0,
            quest3_done INTEGER DEFAULT 0,
            quest4_done INTEGER DEFAULT 0,
            quest5_done INTEGER DEFAULT 0,
            quest_bonus_claimed INTEGER DEFAULT 0,
            tournament_wins INTEGER DEFAULT 0,
            tournament_fee_paid INTEGER DEFAULT 0
        )
        """)

        await db.execute("""
        CREATE TABLE IF NOT EXISTS families (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user1_id INTEGER,
            user2_id INTEGER,
            name TEXT DEFAULT NULL,
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
# РАНГИ, ТИТУЛЫ, АЧИВКИ
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

# ============================================================
# КВЕСТЫ
# ============================================================
QUESTS = [
    {"id": 1, "name": "Сыграть 10 игр", "target": 10, "coins": 30, "stars": 0},
    {"id": 2, "name": "Выиграть 5 дуэлей", "target": 5, "coins": 0, "stars": 2},
    {"id": 3, "name": "Угадать число в рулетке", "target": 1, "coins": 0, "stars": 5},
    {"id": 4, "name": "Сыграть в котиков 3 раза", "target": 3, "coins": 20, "stars": 0},
    {"id": 5, "name": "Выиграть 500 монет за день", "target": 500, "coins": 0, "stars": 3},
]

async def update_quest_progress(user_id: int, quest_id: int, progress: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(f"UPDATE users SET quest{quest_id} = quest{quest_id} + ? WHERE user_id = ?", (progress, user_id))
        await db.commit()
    await check_quest_completion(user_id, quest_id)

async def check_quest_completion(user_id: int, quest_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(f"SELECT quest{quest_id}, quest{quest_id}_done FROM users WHERE user_id = ?", (user_id,)) as c:
            row = await c.fetchone()
        if not row: return
        progress = row[0]
        done = row[1]
        if done: return

        target = QUESTS[quest_id - 1]["target"]
        if progress >= target:
            await db.execute(f"UPDATE users SET quest{quest_id}_done = 1 WHERE user_id = ?", (user_id,))
            coins = QUESTS[quest_id - 1]["coins"]
            stars = QUESTS[quest_id - 1]["stars"]
            await db.execute("UPDATE users SET coins = coins + ?, stars = stars + ? WHERE user_id = ?", (coins, stars, user_id))
            await db.commit()
            try:
                await bot.send_message(user_id, f"✅ КВЕСТ ВЫПОЛНЕН!\n{QUESTS[quest_id - 1]['name']}\nНаграда: +{coins}💰, +{stars}⭐")
            except Exception:
                pass
            await check_all_quests_bonus(user_id)

async def check_all_quests_bonus(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT quest1_done, quest2_done, quest3_done, quest4_done, quest5_done, quest_bonus_claimed FROM users WHERE user_id = ?", (user_id,)) as c:
            row = await c.fetchone()
        if not row: return
        if row["quest_bonus_claimed"]: return
        if all([row["quest1_done"], row["quest2_done"], row["quest3_done"], row["quest4_done"], row["quest5_done"]]):
            await db.execute("UPDATE users SET stars = stars + 10, quest_bonus_claimed = 1 WHERE user_id = ?", (user_id,))
            await db.commit()
            try:
                await bot.send_message(user_id, "🎁 БОНУС ЗА ВСЕ КВЕСТЫ! +10⭐")
            except Exception:
                pass

# ============================================================
# ТУРНИР (АВТОМАТИЧЕСКИЙ)
# ============================================================
async def add_tournament_win(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET tournament_wins = tournament_wins + 1 WHERE user_id = ?", (user_id,))
        await db.commit()

async def get_tournament_end_time() -> datetime.datetime:
    now = datetime.datetime.now()
    days_until_sunday = (6 - now.weekday()) % 7
    if days_until_sunday == 0 and now.hour >= 23 and now.minute >= 59:
        days_until_sunday = 7
    end_time = now.replace(hour=23, minute=59, second=0, microsecond=0) + datetime.timedelta(days=days_until_sunday)
    return end_time

async def get_tournament_time_left() -> str:
    end_time = await get_tournament_end_time()
    now = datetime.datetime.now()
    diff = end_time - now
    total_hours = int(diff.total_seconds() // 3600)
    minutes = int((diff.total_seconds() % 3600) // 60)
    return f"{total_hours}ч {minutes}м"

async def check_tournament_end():
    while True:
        now = datetime.datetime.now()
        end_time = await get_tournament_end_time()
        if now >= end_time:
            await finish_tournament()
            await asyncio.sleep(3600)
        else:
            await asyncio.sleep(600)

async def finish_tournament():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT user_id, tournament_wins FROM users WHERE tournament_wins > 0 ORDER BY tournament_wins DESC LIMIT 3") as c:
            winners = await c.fetchall()

        if not winners:
            return

        async with db.execute("SELECT COUNT(*) FROM users WHERE tournament_fee_paid = 1") as c:
            count = await c.fetchone()
        participants = count[0] if count else 0
        prize_pool = 500 + participants * 10

        rewards = [int(prize_pool * 0.6), int(prize_pool * 0.3), int(prize_pool * 0.1)]
        
        for idx, winner in enumerate(winners):
            if idx >= 3: break
            stars = rewards[idx]
            await db.execute("UPDATE users SET stars = stars + ? WHERE user_id = ?", (stars, winner['user_id']))
            await db.execute("UPDATE users SET tournament_wins = 0, tournament_fee_paid = 0 WHERE user_id = ?", (winner['user_id'],))

        await db.commit()

        message = "🏆 **ТУРНИР ЗАВЕРШЁН!**\n\n"
        for idx, winner in enumerate(winners):
            if idx >= 3: break
            user = await get_or_create_user(winner['user_id'], None)
            name = user.get('custom_nick') or user.get('username') or f"Игрок_{winner['user_id']}"
            message += f"{'🥇' if idx == 0 else '🥈' if idx == 1 else '🥉'} {name} — +{rewards[idx]}⭐\n"
        
        try:
            await bot.send_message(CHAT_ID, message, parse_mode="Markdown")
        except Exception:
            pass

# ============================================================
# АВТОРАЗДАЧА ТОПОВ
# ============================================================
async def daily_prize_distribution():
    while True:
        now = datetime.datetime.now()
        next_run = now.replace(hour=0, minute=0, second=0, microsecond=0) + datetime.timedelta(days=1)
        if now >= next_run:
            next_run += datetime.timedelta(days=1)
        wait_seconds = (next_run - now).total_seconds()
        await asyncio.sleep(wait_seconds)
        await distribute_daily_rewards()

async def distribute_daily_rewards():
    if not CHAT_ID:
        return

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        async with db.execute("SELECT user_id, username, custom_nick, wins FROM users ORDER BY wins DESC LIMIT 10") as c:
            top_players = await c.fetchall()

        rewards = [
            {"stars": 25, "coins": 100},
            {"stars": 15, "coins": 50},
            {"stars": 10, "coins": 25}
        ]

        text_players = "🏆 ЕЖЕДНЕВНЫЙ ТОП ИГРОКОВ:\n\n"
        for idx, player in enumerate(top_players[:3], start=1):
            name = player['custom_nick'] or player['username'] or f"Игрок_{player['user_id']}"
            reward = rewards[idx - 1]
            await db.execute("UPDATE users SET stars = stars + ?, coins = coins + ? WHERE user_id = ?",
                             (reward["stars"], reward["coins"], player['user_id']))
            text_players += f"{'🥇' if idx == 1 else '🥈' if idx == 2 else '🥉'} {name} — +{reward['stars']}⭐ +{reward['coins']}💰\n"

        async with db.execute("SELECT id, name, user1_id, user2_id, score FROM families ORDER BY score DESC LIMIT 10") as c:
            top_families = await c.fetchall()

        family_rewards = [30, 20, 10]
        text_families = "\n💍 ТОП СЕМЕЙ:\n\n"
        for idx, fam in enumerate(top_families[:3], start=1):
            name = fam['name'] or f"Семья #{fam['id']}"
            stars = family_rewards[idx - 1]
            await db.execute("UPDATE users SET stars = stars + ? WHERE user_id IN (?, ?)",
                             (stars, fam['user1_id'], fam['user2_id']))
            text_families += f"{'🥇' if idx == 1 else '🥈' if idx == 2 else '🥉'} {name} — +{stars}⭐\n"

        await db.commit()

    message = text_players + text_families
    try:
        await bot.send_message(CHAT_ID, message, parse_mode="Markdown")
    except Exception as e:
        logging.error(f"Ошибка отправки уведомления: {e}")

# ---------------------------------------------------------
# ПОМОЩЬ
# ---------------------------------------------------------
HELP_TEXT = """🎮 ПОЛНЫЙ СПИСОК ВОЗМОЖНОСТЕЙ БОТА

⭐ Звёзды — ИГРОВАЯ ВАЛЮТА!

━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🎲 ИГРЫ
🎰 Рулетка — р / рулетка
🤠 Дуэль — дуэль (сумма) (ТОЛЬКО В ЧАТАХ)
🐱 Котики — котики (ставка) (ТОЛЬКО В ЧАТАХ)
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
💍 обручиться @username [имя_семьи]
👶 ребёнок @username (доступно, если семья была в топ-1 5 раз)
💔 развод
👨‍👩‍👧‍👦 семья
🎂 годовщина
✏️ сменить_имя_семьи @партнёр НовоеИмя (5⭐)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🎯 КВЕСТЫ И ТУРНИРЫ
🎯 квесты
🏆 турнир
"""

# ============================================================
# ВЫБОР ПОЛА
# ============================================================
@router.message(Command("start"))
async def cmd_gender_choice(message: Message):
    user = await get_or_create_user(message.from_user.id, message.from_user.username)
    if user.get('gender'):
        await cmd_help(message)
        return

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👨 Мужчина", callback_data="gender_male"),
         InlineKeyboardButton(text="👩 Женщина", callback_data="gender_female")]
    ])
    await message.answer("👤 Выберите свой пол:", reply_markup=kb)

@router.callback_query(F.data.startswith("gender_"))
async def process_gender(callback: CallbackQuery):
    gender = callback.data.split("_")[1]
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET gender = ? WHERE user_id = ?", (gender, callback.from_user.id))
        await db.commit()
    await callback.message.edit_text(f"✅ Пол выбран: {'👨 Мужчина' if gender == 'male' else '👩 Женщина'}")
    await callback.answer()

@router.message(Command("start"))
@router.message(F.text.lower().in_(["помощь", "help", "❓ помощь"]))
async def cmd_help(message: Message):
    user = await get_or_create_user(message.from_user.id, message.from_user.username)
    if not user.get('gender'):
        await cmd_gender_choice(message)
        return
    if message.chat.type == ChatType.PRIVATE:
        await message.answer(HELP_TEXT, parse_mode="Markdown", reply_markup=get_main_keyboard())
    else:
        await message.answer(HELP_TEXT, parse_mode="Markdown")

# ============================================================
# РУЛЕТКА
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
                await add_tournament_win(uid)
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
# ДУЭЛЬ (ТОЛЬКО В ЧАТАХ)
# ============================================================
@router.message(F.text.lower().startswith(("дуэль", "🤠 дуэль")))
async def cmd_duel(message: Message):
    if message.chat.type == ChatType.PRIVATE:
        await message.answer("🤠 Перейди в чат, чтобы поставить ставку!")
        return

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
        "chat_id": message.chat.id,
        "turn": None,
        "p1_hp": 100,
        "p2_hp": 100
    }

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"🎯 Принять дуэль ({amount}💰)", callback_data=f"accept_duel_{duel_id}")],
        [InlineKeyboardButton(text="❌ Отменить", callback_data=f"cancel_duel_{duel_id}")]
    ])
    await message.answer(f"🤠 {uname} вызывает на дуэль! Ставка: {amount}💰", reply_markup=kb, parse_mode="Markdown")

@router.callback_query(F.data.startswith("cancel_duel_"))
async def cancel_duel(callback: CallbackQuery):
    duel_id = callback.data.replace("cancel_duel_", "")
    if duel_id in active_duels:
        del active_duels[duel_id]
    await callback.message.edit_text("❌ Дуэль отменена.")
    await callback.answer()

@router.callback_query(F.data.startswith("accept_duel_"))
async def process_accept_duel(callback: CallbackQuery):
    duel_id = callback.data.replace("accept_duel_", "")
    if duel_id not in active_duels:
        await callback.answer("Дуэль устарела!", show_alert=True)
        return

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
    duel["turn"] = random.choice([p1_id, p2_id])
    duel["p1_hp"] = 100
    duel["p2_hp"] = 100

    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="💥 ВЫСТРЕЛ!", callback_data=f"shoot_{duel_id}")
    ]])
    await callback.message.edit_text(
        f"🤠 ДУЭЛЬ!\n{duel['p1_name']} vs {p2_name}\n\n👉 Ход: {duel['p1_name'] if duel['turn'] == p1_id else p2_name}",
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
            await db.execute("UPDATE families SET score = score + 1 WHERE user1_id = ? OR user2_id = ?", (winner_id, winner_id))
            await db.commit()
        
        await add_tournament_win(winner_id)

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
# КОТИКИ (ТОЛЬКО В ЧАТАХ) С КНОПКОЙ ОТМЕНЫ
# ============================================================
@router.message(F.text.lower().startswith(("котики", "🐱 котики")))
async def cmd_cats(message: Message):
    if message.chat.type == ChatType.PRIVATE:
        await message.answer("🐱 Перейди в чат, чтобы поставить ставку!")
        return

    chat_id = message.chat.id

    if chat_id in active_cats and active_cats[chat_id]["active"]:
        await message.answer("❌ Игра уже идёт! Дождись окончания.")
        return

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

    active_cats[chat_id] = {
        "count": yellow_cats,
        "pot": bet,
        "attempts": {},
        "active": True,
        "chat_id": chat_id,
        "starter_id": user['user_id'],
        "starter_bet": bet
    }

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отменить игру", callback_data=f"cancel_cats_{chat_id}")]
    ])

    text = f"🐱 Считай жёлтых 🐈 (чёрные 🐈‍⬛ не считаем!)\nСтавка: {bet}💰\n\n" + "".join(cats_list)
    await message.answer(text, parse_mode="Markdown", reply_markup=kb)

@router.callback_query(F.data.startswith("cancel_cats_"))
async def cancel_cats(callback: CallbackQuery):
    chat_id = int(callback.data.split("_")[2])
    
    if chat_id not in active_cats:
        await callback.answer("❌ Игра уже завершена!", show_alert=True)
        return

    game = active_cats[chat_id]
    if not game["active"]:
        await callback.answer("❌ Игра уже завершена!", show_alert=True)
        return

    starter_id = game.get("starter_id")
    starter_bet = game.get("starter_bet", 0)
    
    if starter_id and starter_bet > 0:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("UPDATE users SET coins = coins + ? WHERE user_id = ?", (starter_bet, starter_id))
            await db.commit()

    del active_cats[chat_id]
    
    await callback.message.edit_text("❌ Игра отменена! Ставка возвращена.")
    await callback.answer()

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
            user = await get_or_create_user(user_id, None)
            if user.get('family_id'):
                await db.execute("UPDATE families SET score = score + 1 WHERE id = ?", (user['family_id'],))
            await db.commit()
        
        await add_tournament_win(user_id)
        await message.answer(f"🎉 Правильно! Жёлтых котиков было {val}.\n+{win_pot}💰", parse_mode="Markdown")

# ============================================================
# КАЗИНО
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
            await db.execute("UPDATE users SET stars = stars + ?, wins = wins + 1 WHERE user_id = ?", (win_s, user['user_id']))
            res = f"⭐ +{win_s} звёзд!"
        else:
            await db.execute("UPDATE users SET coins = coins + 5000, stars = stars + 50, is_vip = 1, wins = wins + 1 WHERE user_id = ?", (user['user_id'],))
            res = "🔥 ДЖЕКПОТ! 5000💰 + 50⭐ + VIP!"

        await db.commit()
    await message.answer(f"🎰 Казино (-25⭐)\n\n{res}", parse_mode="Markdown")

# ============================================================
# ПРИЗ (ТОЛЬКО В ЛС)
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
# ПЕРЕВОД
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
# МАГАЗИН
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
# СМЕНА НИКА
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
# СКРЫТИЕ ПРОФИЛЯ
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
# ПРОФИЛЬ (С ФОТО)
# ============================================================
@router.message(F.text.lower().in_(["профиль", "п"]))
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

    gender_emoji = "👨" if user.get('gender') == 'male' else "👩" if user.get('gender') == 'female' else ""

    # ФОТО В ПРОФИЛЕ (ПО ПОЛУ)
    gender = user.get('gender')
    if gender == 'male':
        photo_url = "https://i.ibb.co/dJpfCxPp/5472030760698059296-120.jpg"
    elif gender == 'female':
        photo_url = "https://i.ibb.co/PZbYnjh8/5472030760698059295-121.jpg"
    else:
        photo_url = None

    text = (
        f"{gender_emoji} 👤 Профиль: {display_name}\n"
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
    
    if photo_url:
        await message.answer_photo(photo=photo_url, caption=text, parse_mode="Markdown", reply_markup=reply_markup)
    else:
        await message.answer(text, parse_mode="Markdown", reply_markup=reply_markup)

# ============================================================
# СТАТИСТИКА
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
# ТОПЫ
# ============================================================
@router.message(F.text.lower().in_(["топ", "🏆 топ"]))
async def cmd_top(message: Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💍 Топ Семей", callback_data="switch_top_families")],
        [InlineKeyboardButton(text="🏆 Топ Игроков", callback_data="switch_top_players")]
    ])
    await show_top_players(message, kb)

async def show_top_players(message: Message, kb: InlineKeyboardMarkup = None):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT user_id, username, custom_nick, wins, coins FROM users ORDER BY wins DESC LIMIT 10") as c:
            rows = await c.fetchall()

    text = "🏆 ТОП ИГРОКОВ:\n\n"
    for idx, r in enumerate(rows, start=1):
        name = r['custom_nick'] or r['username'] or f"Игрок_{r['user_id']}"
        text += f"{idx}. {name} — {r['wins']} побед ({r['coins']}💰)\n"

    text += "\n━━━━━━━━━━━━━━━━━━━\n"
    text += "🎁 Награды:\n🥇 1 место — 25⭐ + 100💰\n🥈 2 место — 15⭐ + 50💰\n🥉 3 место — 10⭐ + 25💰"

    if not kb:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💍 Топ Семей", callback_data="switch_top_families")]
        ])

    await message.answer(text, parse_mode="Markdown", reply_markup=kb)

@router.callback_query(F.data == "switch_top_families")
async def switch_top_families(callback: CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏆 Топ Игроков", callback_data="switch_top_players")]
    ])
    await show_top_families(callback.message, kb)

async def show_top_families(message: Message, kb: InlineKeyboardMarkup = None):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT id, name, score, created_at FROM families ORDER BY score DESC LIMIT 10") as c:
            rows = await c.fetchall()

    text = "💍 ТОП СЕМЕЙ:\n\n"
    for idx, r in enumerate(rows, start=1):
        name = r['name'] or f"Семья #{r['id']}"
        text += f"{idx}. {name} — {r['score']} очков ({r['created_at']})\n"

    text += "\n━━━━━━━━━━━━━━━━━━━\n"
    text += "🎁 Награды:\n🥇 1 место — 30⭐\n🥈 2 место — 20⭐\n🥉 3 место — 10⭐"

    if not kb:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🏆 Топ Игроков", callback_data="switch_top_players")]
        ])

    await message.answer(text, parse_mode="Markdown", reply_markup=kb)

@router.callback_query(F.data == "switch_top_players")
async def switch_top_players(callback: CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💍 Топ Семей", callback_data="switch_top_families")]
    ])
    await show_top_players(callback.message, kb)

# ============================================================
# КВЕСТЫ
# ============================================================
@router.message(F.text.lower().in_(["квесты", "🎯 квесты"]))
async def cmd_quests(message: Message):
    user = await get_or_create_user(message.from_user.id, message.from_user.username)
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT quest1, quest1_done, quest2, quest2_done, quest3, quest3_done,
                   quest4, quest4_done, quest5, quest5_done, quest_bonus_claimed
            FROM users WHERE user_id = ?
        """, (message.from_user.id,)) as c:
            q = await c.fetchone()

    if not q: return

    text = "🎯 **ЕЖЕНЕДЕЛЬНЫЕ КВЕСТЫ**\n\n"
    for i in range(1, 6):
        progress = q[f"quest{i}"]
        done = q[f"quest{i}_done"]
        target = QUESTS[i-1]["target"]
        icon = "[✅]" if done else "[ ]"
        text += f"{icon} {QUESTS[i-1]['name']} — {progress}/{target}\n"

    bonus = "✅" if q["quest_bonus_claimed"] else "❌"
    text += f"\n🎁 Бонус за все 5 квестов: {bonus} +10⭐"

    await message.answer(text, parse_mode="Markdown")

# ============================================================
# ТУРНИР
# ============================================================
@router.message(F.text.lower().in_(["турнир", "🏆 турнир"]))
async def cmd_tournament(message: Message):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT user_id, tournament_wins FROM users WHERE tournament_wins > 0 ORDER BY tournament_wins DESC LIMIT 10") as c:
            rows = await c.fetchall()

    text = "🏆 **ЕЖЕНЕДЕЛЬНЫЙ ТУРНИР**\n\n"
    if rows:
        for idx, r in enumerate(rows, start=1):
            user = await get_or_create_user(r['user_id'], None)
            name = user.get('custom_nick') or user.get('username') or f"Игрок_{r['user_id']}"
            text += f"{idx}. {name} — {r['tournament_wins']} побед\n"
    else:
        text += "❌ В турнире пока нет участников.\n"

    time_left = await get_tournament_time_left()
    text += f"\n⏳ До завершения турнира: **{time_left}**\n\n"
    text += "🎟️ Участие: 10⭐\n💰 Призовой фонд: 500⭐ + взносы\n🥇 1 место — 60%\n🥈 2 место — 30%\n🥉 3 место — 10%"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎟️ Участвовать (10⭐)", callback_data="join_tournament")],
        [InlineKeyboardButton(text="🏆 Топ недели", callback_data="tournament_top")]
    ])
    await message.answer(text, parse_mode="Markdown", reply_markup=kb)

@router.callback_query(F.data == "join_tournament")
async def process_join_tournament(callback: CallbackQuery):
    user = await get_or_create_user(callback.from_user.id, callback.from_user.username)
    if user['stars'] < 10:
        await callback.answer("❌ Нужно 10⭐!", show_alert=True)
        return

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET stars = stars - 10, tournament_fee_paid = 1 WHERE user_id = ?", (user['user_id'],))
        await db.commit()

    await callback.answer("✅ Ты в турнире!", show_alert=True)

@router.callback_query(F.data == "tournament_top")
async def tournament_top(callback: CallbackQuery):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT user_id, tournament_wins FROM users WHERE tournament_wins > 0 ORDER BY tournament_wins DESC LIMIT 10") as c:
            rows = await c.fetchall()

    text = "🏆 **ТОП НЕДЕЛИ (ТУРНИР)**\n\n"
    if rows:
        for idx, r in enumerate(rows, start=1):
            user = await get_or_create_user(r['user_id'], None)
            name = user.get('custom_nick') or user.get('username') or f"Игрок_{r['user_id']}"
            text += f"{'🥇' if idx == 1 else '🥈' if idx == 2 else '🥉' if idx == 3 else f'{idx}.'} {name} — {r['tournament_wins']} побед\n"
        text += "\n🎁 **Награды за турнир:**\n🥇 1 место — 60% фонда\n🥈 2 место — 30% фонда\n🥉 3 место — 10% фонда"
    else:
        text += "❌ В турнире пока нет участников."

    await callback.message.edit_text(text, parse_mode="Markdown")
    await callback.answer()

# ============================================================
# СЕМЬЯ (С ИМЕНЕМ)
# ============================================================
@router.message(F.text.lower().startswith("обручиться"))
async def cmd_marry(message: Message):
    if not message.reply_to_message:
        await message.answer("❌ Ответь на сообщение человека, с которым хочешь создать семью!")
        return

    u1 = await get_or_create_user(message.from_user.id, message.from_user.username)
    u2 = await get_or_create_user(message.reply_to_message.from_user.id, message.reply_to_message.from_user.username)

    if u1['family_id'] or u2['family_id']:
        await message.answer("❌ Кто-то уже в браке!")
        return

    args = message.text.split(maxsplit=1)
    family_name = args[1] if len(args) > 1 else f"Семья {u1['username']} и {u2['username']}"

    family_name_requests[u2['user_id']] = {
        "from": u1['user_id'],
        "from_name": u1['custom_nick'] or u1['username'] or f"Игрок_{u1['user_id']}",
        "to_name": u2['custom_nick'] or u2['username'] or f"Игрок_{u2['user_id']}",
        "name": family_name
    }

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Согласиться", callback_data=f"family_accept_{u1['user_id']}_{u2['user_id']}")],
        [InlineKeyboardButton(text="✏️ Предложить своё", callback_data=f"family_own_{u1['user_id']}_{u2['user_id']}")]
    ])
    await message.answer(
        f"💍 {message.reply_to_message.from_user.first_name}, тебе предлагают создать семью!\n"
        f"Имя семьи: {family_name}\n\n"
        "Согласишься или предложишь своё имя?",
        reply_markup=kb
    )

@router.callback_query(F.data.startswith("family_accept_"))
async def family_accept(callback: CallbackQuery):
    _, u1, u2 = callback.data.split("_")
    u1, u2 = int(u1), int(u2)

    if callback.from_user.id != u2:
        await callback.answer("Не тебе!", show_alert=True)
        return

    req = family_name_requests.get(u2)
    if not req:
        await callback.answer("Запрос устарел!", show_alert=True)
        return

    today = datetime.date.today().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        c = await db.execute("INSERT INTO families (user1_id, user2_id, name, created_at) VALUES (?, ?, ?, ?)",
                             (u1, u2, req['name'], today))
        fam_id = c.lastrowid
        await db.execute("UPDATE users SET family_id = ?, spouse_id = ?, stars = stars + 5 WHERE user_id IN (?, ?)", (fam_id, u1, u2, u1, u2))
        await db.commit()

    del family_name_requests[u2]
    await callback.message.edit_text(f"🎉 СЕМЬЯ СОЗДАНА!\nИмя: {req['name']}\n+5⭐ каждому!")

@router.callback_query(F.data.startswith("family_own_"))
async def family_own(callback: CallbackQuery, state: FSMContext):
    _, u1, u2 = callback.data.split("_")
    u1, u2 = int(u1), int(u2)

    if callback.from_user.id != u2:
        await callback.answer("Не тебе!", show_alert=True)
        return

    req = family_name_requests.get(u2)
    if not req:
        await callback.answer("Запрос устарел!", show_alert=True)
        return

    await state.update_data(u1=u1, u2=u2)
    await state.set_state(FamilyNameState.waiting_for_name)
    await callback.message.edit_text("✏️ Напиши своё имя для семьи:")

@router.message(FamilyNameState.waiting_for_name)
async def family_name_input(message: Message, state: FSMContext):
    data = await state.get_data()
    u1, u2 = data.get("u1"), data.get("u2")
    new_name = message.text

    today = datetime.date.today().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        c = await db.execute("INSERT INTO families (user1_id, user2_id, name, created_at) VALUES (?, ?, ?, ?)",
                             (u1, u2, new_name, today))
        fam_id = c.lastrowid
        await db.execute("UPDATE users SET family_id = ?, spouse_id = ?, stars = stars + 5 WHERE user_id IN (?, ?)", (fam_id, u1, u2, u1, u2))
        await db.commit()

    await state.clear()
    await message.answer(f"🎉 СЕМЬЯ СОЗДАНА!\nИмя: {new_name}\n+5⭐ каждому!")

# ============================================================
# СМЕНА НАЗВАНИЯ СЕМЬИ ЗА 5⭐
# ============================================================
@router.message(F.text.lower().startswith("сменить_имя_семьи"))
async def cmd_change_family_name(message: Message):
    args = message.text.split(maxsplit=2)
    if len(args) < 3:
        await message.answer("❌ Формат: сменить_имя_семьи @партнёр НовоеИмя", parse_mode="Markdown")
        return

    partner_username = args[1].replace("@", "")
    new_name = args[2].strip()

    if not new_name:
        await message.answer("❌ Название не может быть пустым!")
        return

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT user_id FROM users WHERE username = ? OR custom_nick = ?", (partner_username, partner_username)) as c:
            partner = await c.fetchone()

    if not partner:
        await message.answer("❌ Партнёр не найден!")
        return

    partner_id = partner['user_id']

    user = await get_or_create_user(message.from_user.id, message.from_user.username)
    if not user.get('family_id'):
        await message.answer("❌ У тебя нет семьи!")
        return

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT user1_id, user2_id, name FROM families WHERE id = ?", (user['family_id'],)) as c:
            fam = await c.fetchone()

    if not fam:
        await message.answer("❌ Семья не найдена!")
        return

    if partner_id not in (fam['user1_id'], fam['user2_id']):
        await message.answer("❌ Этот игрок не состоит с тобой в семье!")
        return

    if user['stars'] < 5:
        await message.answer("❌ Нужно 5⭐ для смены названия семьи!")
        return

    family_name_change_requests[partner_id] = {
        "from": user['user_id'],
        "family_id": user['family_id'],
        "new_name": new_name,
        "old_name": fam['name'] or f"Семья #{fam['id']}"
    }

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Согласиться", callback_data=f"family_name_accept_{user['user_id']}_{partner_id}_{user['family_id']}")],
        [InlineKeyboardButton(text="❌ Отказаться", callback_data=f"family_name_reject_{partner_id}")]
    ])

    await message.answer(
        f"✏️ @{partner_username}, {message.from_user.username} предлагает сменить название семьи на **{new_name}**!\n\n"
        f"💰 Стоимость: 5⭐ (списывается у инициатора после подтверждения)\n"
        f"Текущее название: {fam['name'] or f'Семья #{fam['id']}'}",
        reply_markup=kb,
        parse_mode="Markdown"
    )

@router.callback_query(F.data.startswith("family_name_accept_"))
async def family_name_accept(callback: CallbackQuery):
    _, u1, u2, fam_id = callback.data.split("_")
    u1, u2, fam_id = int(u1), int(u2), int(fam_id)

    if callback.from_user.id != u2:
        await callback.answer("❌ Не тебе!", show_alert=True)
        return

    req = family_name_change_requests.get(u2)
    if not req:
        await callback.answer("❌ Запрос устарел!", show_alert=True)
        return

    new_name = req["new_name"]

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET stars = stars - 5 WHERE user_id = ?", (u1,))
        await db.execute("UPDATE families SET name = ? WHERE id = ?", (new_name, fam_id))
        await db.commit()

    del family_name_change_requests[u2]

    await callback.message.edit_text(f"✅ Название семьи успешно изменено на **{new_name}**! (5⭐ списано у инициатора)", parse_mode="Markdown")
    await callback.answer()

@router.callback_query(F.data.startswith("family_name_reject_"))
async def family_name_reject(callback: CallbackQuery):
    user_id = int(callback.data.split("_")[2])
    if callback.from_user.id != user_id:
        await callback.answer("❌ Не тебе!", show_alert=True)
        return

    if user_id in family_name_change_requests:
        del family_name_change_requests[user_id]

    await callback.message.edit_text("❌ Смена названия семьи отменена.")
    await callback.answer()

# ============================================================
# РЕБЁНОК (ТОЛЬКО ПОСЛЕ 5 РАЗ В ТОП-1)
# ============================================================
@router.message(F.text.lower().startswith("ребёнок"))
async def cmd_adopt(message: Message):
    if not message.reply_to_message:
        await message.answer("❌ Ответь на сообщение человека, которого хочешь усыновить!")
        return

    parent = await get_or_create_user(message.from_user.id, message.from_user.username)
    child = await get_or_create_user(message.reply_to_message.from_user.id, message.reply_to_message.from_user.username)

    if not parent['family_id']:
        await message.answer("❌ Сначала создай семью!")
        return

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT top1_count FROM families WHERE id = ?", (parent['family_id'],)) as c:
            fam = await c.fetchone()

    if not fam or fam['top1_count'] < 5:
        await message.answer("❌ Семья должна быть в топ-1 хотя бы 5 раз, чтобы завести ребёнка!")
        return

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT 1 FROM children WHERE family_id = ? AND child_id = ?", (parent['family_id'], child['user_id'])) as c:
            if await c.fetchone():
                await message.answer("❌ Этот игрок уже усыновлён!")
                return

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO children (family_id, child_id) VALUES (?, ?)", (parent['family_id'], child['user_id']))
        await db.commit()

    await message.answer(f"👶 {message.reply_to_message.from_user.first_name} усыновлён!")

# ============================================================
# СЕМЬЯ (ИНФО)
# ============================================================
@router.message(F.text.lower().in_(["семья", "💍 семья"]))
async def cmd_family(message: Message):
    user = await get_or_create_user(message.from_user.id, message.from_user.username)
    if not user['family_id']:
        await message.answer("❌ Нет семьи!")
        return

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM families WHERE id = ?", (user['family_id'],)) as c:
            fam = await c.fetchone()
        async with db.execute("SELECT child_id FROM children WHERE family_id = ?", (user['family_id'],)) as c2:
            children = await c2.fetchall()

    text = f"""💍 СЕМЬЯ: {fam['name'] or f"Семья #{fam['id']}"}
📅 Создана: {fam['created_at']}
⭐ Очки: {fam['score']}
🎖️ Топ-1 раз: {fam['top1_count']}
👶 Детей: {len(children)}"""
    await message.answer(text, parse_mode="Markdown")

# ============================================================
# РАЗВОД
# ============================================================
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

# ============================================================
# ГОДОВЩИНА
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
        await db.execute("UPDATE families SET last_anniversary_month = ? WHERE id = ?", (months, user['family_id']))
        await db.execute("UPDATE users SET stars = stars + ? WHERE user_id IN (?, ?)", (reward, user['user_id'], user['spouse_id']))
        await db.commit()
        await message.answer(f"🎂 ГОДОВЩИНА! {months} месяцев! +{reward}⭐ каждому!", parse_mode="Markdown")
    else:
        await message.answer(f"📅 Вместе {months} месяцев. Следующая годовщина через {months - fam['last_anniversary_month']} мес.")

# ============================================================
# ПРОМОКОДЫ (АДМИН)
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
# ЗАПУСК
# ============================================================
async def main():
    await init_db()
    asyncio.create_task(daily_prize_distribution())
    asyncio.create_task(check_tournament_end())
    print("🤖 БОТ ЗАПУЩЕН! ВСЁ РАБОТАЕТ!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
