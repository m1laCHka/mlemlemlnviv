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
from aiohttp import web
import asyncpg

# ---------------------------------------------------------
# НАСТРОЙКИ И ИНИЦИАЛИЗАЦИЯ
# ---------------------------------------------------------
logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
DATABASE_URL = os.getenv("DATABASE_URL")
PORT = int(os.getenv("PORT", 8080))

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
router = Router()
dp.include_router(router)

db_pool: Optional[asyncpg.Pool] = None

# Глобальные игровые состояния (в ОЗУ)
active_roulettes: Dict[int, dict] = {}
active_duels: Dict[str, dict] = {}
active_cats: Dict[str, dict] = {}

# ---------------------------------------------------------
# ВЕБ-СЕРВЕР ДЛЯ РЕНДЕРА
# ---------------------------------------------------------
async def handle_ping(request):
    return web.Response(text="Bot is running 24/7!")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()

# ---------------------------------------------------------
# КЛАВИАТУРЫ (Нижнее меню ТОЛЬКО в ЛС)
# ---------------------------------------------------------
def get_main_keyboard() -> ReplyKeyboardMarkup:
    kb = [
        [KeyboardButton(text="🎰 Казино"), KeyboardButton(text="🎁 Приз"), KeyboardButton(text="🏪 Магазин")],
        [KeyboardButton(text="🏆 Топ"), KeyboardButton(text="💍 Семья"), KeyboardButton(text="🎯 Квесты")],
        [KeyboardButton(text="🏆 Турнир"), KeyboardButton(text="❓ Помощь")]
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

# ---------------------------------------------------------
# БАЗА ДАННЫХ (POSTGRESQL)
# ---------------------------------------------------------
async def init_db():
    global db_pool
    clean_url = DATABASE_URL.replace("?sslmode=require", "").replace("&sslmode=require", "")
    db_pool = await asyncpg.create_pool(clean_url, ssl="require")

    async with db_pool.acquire() as conn:
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id BIGINT PRIMARY KEY,
            username TEXT,
            custom_nick TEXT DEFAULT NULL,
            custom_emoji TEXT DEFAULT '',
            coins BIGINT DEFAULT 1000,
            stars INT DEFAULT 10,
            is_vip INT DEFAULT 0,
            vip_until TEXT DEFAULT NULL,
            vip_start_date TEXT DEFAULT NULL,
            is_hidden INT DEFAULT 0,
            insurance INT DEFAULT 0,
            family_id INT DEFAULT NULL,
            spouse_id INT DEFAULT NULL,
            divorce_until TEXT DEFAULT NULL,
            daily_stars_transferred INT DEFAULT 0,
            last_transfer_date TEXT DEFAULT NULL,
            last_prize_date TEXT DEFAULT NULL,
            wins INT DEFAULT 0,
            losses INT DEFAULT 0,
            total_games INT DEFAULT 0,
            total_coins_won BIGINT DEFAULT 0,
            daily_net_win INT DEFAULT 0,
            streak_days INT DEFAULT 0,
            last_active_date TEXT DEFAULT NULL,
            last_game_result TEXT DEFAULT 'Нет',
            top3_family_count INT DEFAULT 0,
            roulette_games INT DEFAULT 0,
            roulette_wins INT DEFAULT 0,
            duel_games INT DEFAULT 0,
            duel_wins INT DEFAULT 0,
            cat_games INT DEFAULT 0,
            cat_wins INT DEFAULT 0,
            casino_games INT DEFAULT 0,
            casino_wins INT DEFAULT 0,
            quests_completed INT DEFAULT 0
        );
        """)

        await conn.execute("""
        CREATE TABLE IF NOT EXISTS families (
            id SERIAL PRIMARY KEY,
            user1_id BIGINT,
            user2_id BIGINT,
            score INT DEFAULT 0,
            created_at TEXT,
            top1_count INT DEFAULT 0,
            last_anniversary_month INT DEFAULT 0
        );
        """)

        await conn.execute("""
        CREATE TABLE IF NOT EXISTS children (
            id SERIAL PRIMARY KEY,
            family_id INT,
            child_id BIGINT
        );
        """)

        await conn.execute("""
        CREATE TABLE IF NOT EXISTS achievements (
            user_id BIGINT,
            ach_id TEXT,
            PRIMARY KEY (user_id, ach_id)
        );
        """)

        await conn.execute("""
        CREATE TABLE IF NOT EXISTS user_quests (
            user_id BIGINT,
            quest_id INT,
            progress INT DEFAULT 0,
            completed INT DEFAULT 0,
            PRIMARY KEY (user_id, quest_id)
        );
        """)

        await conn.execute("""
        CREATE TABLE IF NOT EXISTS tournament (
            user_id BIGINT PRIMARY KEY,
            wins INT DEFAULT 0,
            fee_paid INT DEFAULT 0
        );
        """)

        await conn.execute("""
        CREATE TABLE IF NOT EXISTS promos (
            code TEXT PRIMARY KEY,
            stars INT DEFAULT 0,
            coins BIGINT DEFAULT 0,
            vip_days INT DEFAULT 0,
            max_uses INT DEFAULT 100,
            uses INT DEFAULT 0
        );
        """)

        await conn.execute("""
        CREATE TABLE IF NOT EXISTS user_promos (
            user_id BIGINT,
            code TEXT,
            PRIMARY KEY (user_id, code)
        );
        """)

async def get_or_create_user(user_id: int, username: Optional[str]) -> dict:
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM users WHERE user_id = $1", user_id)
        today = datetime.date.today().isoformat()
        if not row:
            await conn.execute(
                "INSERT INTO users (user_id, username, last_active_date) VALUES ($1, $2, $3)",
                user_id, username or f"Игрок_{user_id}", today
            )
            row = await conn.fetchrow("SELECT * FROM users WHERE user_id = $1", user_id)
        else:
            if username and row['username'] != username:
                await conn.execute("UPDATE users SET username = $1 WHERE user_id = $2", username, user_id)
                row = await conn.fetchrow("SELECT * FROM users WHERE user_id = $1", user_id)
        return dict(row)

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
        async with db_pool.acquire() as conn:
            fam = await conn.fetchrow("SELECT created_at FROM families WHERE id = $1", user['family_id'])
            if fam:
                created = datetime.date.fromisoformat(fam['created_at'])
                if (datetime.date.today() - created).days >= 365:
                    titles.append("👑 Золотая семья")
    return titles

async def check_achievements(user_id: int):
    async with db_pool.acquire() as conn:
        u = await conn.fetchrow("SELECT * FROM users WHERE user_id = $1", user_id)
        if not u: return

        unlocked = {r['ach_id'] async for r in conn.cursor("SELECT ach_id FROM achievements WHERE user_id = $1", user_id)}

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
                await conn.execute("INSERT INTO achievements (user_id, ach_id) VALUES ($1, $2)", user_id, ach_id)
                await conn.execute("UPDATE users SET coins = coins + $1, stars = stars + $2 WHERE user_id = $3", r_coins, r_stars, user_id)
                try:
                    await bot.send_message(user_id, f"🏆 АЧИВКА! {title}\n+{r_coins}💰, +{r_stars}⭐")
                except Exception:
                    pass

async def get_vip_days(user: dict) -> str:
    if not user['is_vip']:
        return "0"
    if user['vip_until']:
        try:
            until = datetime.date.fromisoformat(user['vip_until'])
            days = (until - datetime.date.today()).days
            return str(max(0, days))
        except:
            return "∞"
    return "∞"

# ---------------------------------------------------------
# ПОМОЩЬ
# ---------------------------------------------------------
HELP_TEXT = """🎮 <b>ПОЛНЫЙ СПИСОК ВОЗМОЖНОСТЕЙ БОТА</b>

⭐ Звёзды — ИГРОВАЯ ВАЛЮТА!

━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🎲 <b>ИГРЫ</b>
🎰 Рулетка — р / рулетка (красное, черное, чет, нечет, число)
🤠 Дуэль — дуэль (сумма) — пошагово
🐱 Котики — котики (сумма) — вызови соперника! Кто первый угадает — забирает банк
🎰 Казино — казино — за 25⭐

━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💰 <b>ЭКОНОМИКА</b>
🎁 Приз — приз (1 раз/день, ТОЛЬКО в ЛС)
🏪 Магазин — магазин
⭐ Перевод — перевод @username (сумма) (макс 25⭐/день)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━
👤 <b>ПРОФИЛЬ И ТОПЫ</b>
👤 Профиль — профиль / профиль @username
📊 Статистика — статистика / стата
🏆 Топы — топ, топ_семей

━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💍 <b>СЕМЬЯ</b>
💍 обручиться @username
👶 ребёнок @username
💔 развод
👨‍👩‍👧‍👦 семья
🎂 годовщина

━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🎯 <b>КВЕСТЫ И ТУРНИРЫ</b>
🎯 квесты
🏆 турнир
"""

@router.message(Command("start"))
@router.message(F.text.lower().in_(["помощь", "help", "❓ помощь"]))
async def cmd_help(message: Message):
    await get_or_create_user(message.from_user.id, message.from_user.username)
    if message.chat.type == ChatType.PRIVATE:
        await message.answer(HELP_TEXT, parse_mode="HTML", reply_markup=get_main_keyboard())
    else:
        await message.answer(HELP_TEXT, parse_mode="HTML")

# ============================================================
# 🎰 РУЛЕТКА
# ============================================================
@router.message(F.text.lower().startswith(("рулетка", "р")))
async def cmd_roulette(message: Message):
    chat_id = message.chat.id
    args = message.text.split()

    if len(args) < 2:
        await message.answer(
            "🎰 Ставка:\nр красное 100\nр 15 100\nр 0 100\nМин. 50💰, таймер 60 сек, макс 10 ставок/чат",
            parse_mode="HTML"
        )
        return

    bet_type = args[1].lower()
    
    if len(args) < 3:
        await message.answer("❌ Укажи сумму ставки!", parse_mode="HTML")
        return
        
    try:
        bet_amount = int(args[2])
    except ValueError:
        await message.answer("❌ Ставка должна быть числом!", parse_mode="HTML")
        return

    if bet_amount < 50:
        await message.answer("❌ Мин. ставка: 50 монет!", parse_mode="HTML")
        return

    valid_types = ["красное", "red", "черное", "black", "чет", "even", "нечет", "odd"]
    if bet_type not in valid_types and not bet_type.isdigit():
        await message.answer("❌ Ставь: красное, черное, чет, нечет или число (0-30)", parse_mode="HTML")
        return

    user = await get_or_create_user(message.from_user.id, message.from_user.username)
    if user['coins'] < bet_amount:
        await message.answer("❌ Недостаточно монет!", parse_mode="HTML")
        return

    async with db_pool.acquire() as conn:
        await conn.execute("UPDATE users SET coins = coins - $1 WHERE user_id = $2", bet_amount, user['user_id'])

    if chat_id not in active_roulettes:
        active_roulettes[chat_id] = {"bets": [], "timer_task": asyncio.create_task(run_roulette_timer(chat_id))}

    roul = active_roulettes[chat_id]
    if len(roul["bets"]) >= 10:
        await message.answer("❌ Лимит 10 ставок!", parse_mode="HTML")
        return

    uname = user['custom_nick'] or user['username'] or f"Игрок_{user['user_id']}"
    roul["bets"].append({
        "user_id": user['user_id'],
        "username": uname,
        "type": bet_type,
        "amount": bet_amount,
        "insurance": user['insurance']
    })

    await message.answer(f"✅ Ставка {uname} — {bet_amount}💰 на {bet_type}! ({len(roul['bets'])}/10)", parse_mode="HTML")

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

    async with db_pool.acquire() as conn:
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
                await conn.execute("""UPDATE users SET coins = coins + $1, wins = wins + 1, total_games = total_games + 1,
                    roulette_games = roulette_games + 1, roulette_wins = roulette_wins + 1, total_coins_won = total_coins_won + $1
                    WHERE user_id = $2""", win_coins, uid)
                res_text += f"✅ {uname}: +{win_coins}💰\n"
            else:
                refund = (amount // 2) if b["insurance"] == 1 else 0
                if refund > 0:
                    await conn.execute("UPDATE users SET coins = coins + $1, insurance = 0 WHERE user_id = $2", refund, uid)

                await conn.execute("UPDATE users SET losses = losses + 1, total_games = total_games + 1, roulette_games = roulette_games + 1 WHERE user_id = $1", uid)
                res_text += f"❌ {uname}: -{amount}💰 {'(страховка)' if refund else ''}\n"

    await bot.send_message(chat_id, res_text, parse_mode="HTML")

# ============================================================
# 🤠 ДУЭЛЬ (ПОШАГОВАЯ)
# ============================================================
@router.message(F.text.lower().startswith(("дуэль", "🤠 дуэль")))
async def cmd_duel(message: Message):
    args = message.text.split()
    if len(args) < 2:
        await message.answer("🤠 Формат: дуэль 100", parse_mode="HTML")
        return
    try:
        amount = int(args[1])
    except ValueError:
        await message.answer("❌ Ставка должна быть числом!", parse_mode="HTML")
        return

    if amount < 1:
        await message.answer("❌ Ставка должна быть больше 0!", parse_mode="HTML")
        return

    user = await get_or_create_user(message.from_user.id, message.from_user.username)
    if user['coins'] < amount:
        await message.answer("❌ Недостаточно монет!", parse_mode="HTML")
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
    await message.answer(f"🤠 {uname} вызывает на дуэль! Ставка: {amount}💰", reply_markup=kb, parse_mode="HTML")

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

    async with db_pool.acquire() as conn:
        await conn.execute("UPDATE users SET coins = coins - $1 WHERE user_id = $2", duel["amount"], p1_id)
        await conn.execute("UPDATE users SET coins = coins - $1 WHERE user_id = $2", duel["amount"], p2_id)

    p2_name = p2_user['custom_nick'] or p2_user['username'] or callback.from_user.first_name
    duel["p2"] = p2_id
    duel["p2_name"] = p2_name
    duel["turn"] = p1_id
    duel["p1_hp"] = 100
    duel["p2_hp"] = 100
    duel["pot"] = duel["amount"] * 2

    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="💥 ВЫСТРЕЛ!", callback_data=f"shoot_{duel_id}")
    ]])
    await callback.message.edit_text(
        f"🤠 ДУЭЛЬ!\n{duel['p1_name']} vs {p2_name}\n💰 Банк: {duel['pot']}💰\n\n👉 Ход: {duel['p1_name']}",
        reply_markup=kb, parse_mode="HTML"
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
        
        async with db_pool.acquire() as conn:
            winner = await conn.fetchrow("SELECT is_vip FROM users WHERE user_id = $1", winner_id)
            commission = 0 if winner['is_vip'] == 1 else 0.1
            win_amt = int(duel["pot"] * (1 - commission))
            
            await conn.execute("UPDATE users SET coins = coins + $1, wins = wins + 1, duel_games = duel_games + 1, duel_wins = duel_wins + 1 WHERE user_id = $2", win_amt, winner_id)
            await conn.execute("UPDATE users SET losses = losses + 1, duel_games = duel_games + 1 WHERE user_id = $1", loser_id)

        del active_duels[duel_id]
        await callback.message.edit_text(f"💀 {shooter_name} убивает {target_name}!\n🏆 {shooter_name} +{win_amt}💰", parse_mode="HTML")
    else:
        duel["turn"] = target_id
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="💥 ВЫСТРЕЛ!", callback_data=f"shoot_{duel_id}")
        ]])
        await callback.message.edit_text(
            f"🔫 {shooter_name} наносит {damage} урона!\n"
            f"{duel['p1_name']}: {duel['p1_hp']} HP\n"
            f"{duel['p2_name']}: {duel['p2_hp']} HP\n💰 Банк: {duel['pot']}💰\n\n👉 Ход: {target_name}",
            reply_markup=kb, parse_mode="HTML"
        )

# ============================================================
# 🐱 КОТИКИ (как дуэль)
# ============================================================
@router.message(F.text.lower().startswith(("котики", "🐱 котики")))
async def cmd_cats(message: Message):
    args = message.text.split()
    if len(args) < 2:
        await message.answer("🐱 Формат: котики 100", parse_mode="HTML")
        return
    try:
        bet = int(args[1])
    except ValueError:
        await message.answer("❌ Ставка должна быть числом!", parse_mode="HTML")
        return

    if bet < 1:
        await message.answer("❌ Ставка должна быть больше 0!", parse_mode="HTML")
        return

    user = await get_or_create_user(message.from_user.id, message.from_user.username)
    if user['coins'] < bet:
        await message.answer("❌ Недостаточно монет!", parse_mode="HTML")
        return

    cat_id = f"{message.chat.id}_{message.from_user.id}_{random.randint(100,999)}"

    active_cats[cat_id] = {
        "p1": user['user_id'],
        "p1_name": user['custom_nick'] or user['username'] or message.from_user.first_name,
        "p2": None,
        "p2_name": None,
        "bet": bet,
        "chat_id": message.chat.id,
        "active": False,
        "winner": None
    }

    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=f"🐱 Принять вызов ({bet}💰)", callback_data=f"accept_cats_{cat_id}")
    ]])
    await message.answer(f"🐱 {active_cats[cat_id]['p1_name']} вызывает на котиков! Ставка: {bet}💰\nКто первый угадает — забирает банк!", reply_markup=kb, parse_mode="HTML")

@router.callback_query(F.data.startswith("accept_cats_"))
async def process_accept_cats(callback: CallbackQuery):
    cat_id = callback.data.replace("accept_cats_", "")
    if cat_id not in active_cats: return

    game = active_cats[cat_id]
    p1_id, p2_id = game["p1"], callback.from_user.id
    if p1_id == p2_id:
        await callback.answer("Нельзя с собой!", show_alert=True)
        return

    p2_user = await get_or_create_user(p2_id, callback.from_user.username)
    if p2_user['coins'] < game["bet"]:
        await callback.answer("Недостаточно монет!", show_alert=True)
        return

    async with db_pool.acquire() as conn:
        await conn.execute("UPDATE users SET coins = coins - $1 WHERE user_id = $2", game["bet"], p1_id)
        await conn.execute("UPDATE users SET coins = coins - $1 WHERE user_id = $2", game["bet"], p2_id)

    game["p2"] = p2_id
    game["p2_name"] = p2_user['custom_nick'] or p2_user['username'] or callback.from_user.first_name
    game["active"] = True
    game["pot"] = game["bet"] * 2
    game["attempts"] = {}
    game["winner"] = None

    yellow_cats = random.randint(1, 40)
    black_cats = random.randint(0, 15)
    cats_list = ["🐈"] * yellow_cats + ["🐈‍⬛"] * black_cats
    random.shuffle(cats_list)
    game["yellow_count"] = yellow_cats
    game["cats_text"] = "".join(cats_list)

    text = f"🐱 Считай жёлтых 🐈 (чёрные 🐈‍⬛ не считаем!)\n💰 Банк: {game['pot']}💰\n\n{game['cats_text']}\n\n✏️ Пиши число в чат!"
    
    await callback.message.edit_text(text, parse_mode="HTML")

@router.message(F.text.isdigit())
async def process_cats_answer(message: Message):
    for cat_id, game in list(active_cats.items()):
        if not game["active"]: continue
        if game["winner"]: continue
        
        user_id = message.from_user.id
        if user_id not in [game["p1"], game["p2"]]: continue
        
        if game["attempts"].get(user_id, 0) >= 3: 
            await message.answer("⏳ У тебя уже 3 попытки!", parse_mode="HTML")
            return

        game["attempts"][user_id] = game["attempts"].get(user_id, 0) + 1
        val = int(message.text)

        if val == game["yellow_count"]:
            game["active"] = False
            game["winner"] = user_id
            
            async with db_pool.acquire() as conn:
                winner = await conn.fetchrow("SELECT is_vip FROM users WHERE user_id = $1", user_id)
                commission = 0 if winner['is_vip'] == 1 else 0.1
                win_pot = int(game["pot"] * (1 - commission))
                
                await conn.execute("""UPDATE users SET coins = coins + $1, wins = wins + 1, total_games = total_games + 1,
                    cat_games = cat_games + 1, cat_wins = cat_wins + 1 WHERE user_id = $2""", win_pot, user_id)
                loser_id = game["p2"] if user_id == game["p1"] else game["p1"]
                await conn.execute("UPDATE users SET losses = losses + 1, cat_games = cat_games + 1 WHERE user_id = $1", loser_id)
            
            uname = game["p1_name"] if user_id == game["p1"] else game["p2_name"]
            await message.answer(f"🎉 {uname} угадал! Жёлтых котиков было {val}.\n+{win_pot}💰", parse_mode="HTML")
            del active_cats[cat_id]
            return

# ============================================================
# 🎰 КАЗИНО
# ============================================================
@router.message(F.text.lower().in_(["казино", "🎰 казино"]))
async def cmd_casino(message: Message):
    user = await get_or_create_user(message.from_user.id, message.from_user.username)
    if user['stars'] < 25:
        await message.answer("❌ Нужно 25⭐!", parse_mode="HTML")
        return

    rand = random.random()
    async with db_pool.acquire() as conn:
        await conn.execute("UPDATE users SET stars = stars - 25, casino_games = casino_games + 1 WHERE user_id = $1", user['user_id'])

        if rand < 0.40:
            win_c = random.randint(50, 3000)
            await conn.execute("UPDATE users SET coins = coins + $1, wins = wins + 1 WHERE user_id = $2", win_c, user['user_id'])
            result = "💰 Монеты"
            reward = f"+{win_c}💰"
            chance = "40%"
        elif rand < 0.70:
            days = random.randint(4, 10)
            until = (datetime.date.today() + datetime.timedelta(days=days)).isoformat()
            await conn.execute("UPDATE users SET is_vip = 1, vip_until = $1, wins = wins + 1 WHERE user_id = $2", until, user['user_id'])
            result = "👑 VIP"
            reward = f"на {days} дней!"
            chance = "30%"
        elif rand < 0.99:
            win_s = random.randint(10, 75)
            await conn.execute("UPDATE users SET stars = stars + $1, wins = wins + 1 WHERE user_id = $2", win_s, user['user_id'])
            result = "⭐ Звёзды"
            reward = f"+{win_s}⭐"
            chance = "29%"
        else:
            until = (datetime.date.today() + datetime.timedelta(days=30)).isoformat

async def main():
    await init_db()
    # Запускаем веб-сервер в фоне
    asyncio.create_task(start_web_server())
    print("🤖 БОТ ЗАПУЩЕН! ВСЁ РАБОТАЕТ!")
    await dp.start_polling(bot)



