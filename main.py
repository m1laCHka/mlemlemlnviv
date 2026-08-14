import os
import sys
import random
import asyncio
import datetime
import logging
import traceback
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
# НАСТРОЙКИ
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

# Глобальные состояния
active_roulettes: Dict[int, dict] = {}
active_duels: Dict[str, dict] = {}
active_cats: Dict[str, dict] = {}
marry_requests: Dict[int, dict] = {}

# ---------------------------------------------------------
# ВЕБ-СЕРВЕР
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
# КЛАВИАТУРА
# ---------------------------------------------------------
def get_main_keyboard() -> ReplyKeyboardMarkup:
    kb = [
        [KeyboardButton(text="🎰 Казино"), KeyboardButton(text="🎁 Приз"), KeyboardButton(text="🏪 Магазин")],
        [KeyboardButton(text="🏆 Топ"), KeyboardButton(text="💍 Семья"), KeyboardButton(text="🎯 Квесты")],
        [KeyboardButton(text="🏆 Турнир"), KeyboardButton(text="❓ Помощь")]
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

# ---------------------------------------------------------
# БАЗА ДАННЫХ (БЕЗ УДАЛЕНИЯ!)
# ---------------------------------------------------------
async def init_db():
    global db_pool
    if not DATABASE_URL:
        raise ValueError("DATABASE_URL is not set")
    
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
            stars BIGINT DEFAULT 10,
            is_vip BIGINT DEFAULT 0,
            vip_until TEXT DEFAULT NULL,
            vip_start_date TEXT DEFAULT NULL,
            is_hidden BIGINT DEFAULT 0,
            insurance BIGINT DEFAULT 0,
            family_id BIGINT DEFAULT NULL,
            spouse_id BIGINT DEFAULT NULL,
            divorce_until TEXT DEFAULT NULL,
            daily_stars_transferred BIGINT DEFAULT 0,
            last_transfer_date TEXT DEFAULT NULL,
            last_prize_date TEXT DEFAULT NULL,
            wins BIGINT DEFAULT 0,
            losses BIGINT DEFAULT 0,
            total_games BIGINT DEFAULT 0,
            total_coins_won BIGINT DEFAULT 0,
            daily_net_win BIGINT DEFAULT 0,
            streak_days BIGINT DEFAULT 0,
            last_active_date TEXT DEFAULT NULL,
            last_game_result TEXT DEFAULT 'Нет',
            top3_family_count BIGINT DEFAULT 0,
            roulette_games BIGINT DEFAULT 0,
            roulette_wins BIGINT DEFAULT 0,
            duel_games BIGINT DEFAULT 0,
            duel_wins BIGINT DEFAULT 0,
            cat_games BIGINT DEFAULT 0,
            cat_wins BIGINT DEFAULT 0,
            casino_games BIGINT DEFAULT 0,
            casino_wins BIGINT DEFAULT 0,
            quests_completed BIGINT DEFAULT 0
        );
        """)
        
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS families (
            id SERIAL PRIMARY KEY,
            user1_id BIGINT,
            user2_id BIGINT,
            score BIGINT DEFAULT 0,
            created_at TEXT,
            top1_count BIGINT DEFAULT 0,
            last_anniversary_month BIGINT DEFAULT 0
        );
        """)
        
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS children (
            id SERIAL PRIMARY KEY,
            family_id BIGINT,
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
            quest_id BIGINT,
            progress BIGINT DEFAULT 0,
            completed BIGINT DEFAULT 0,
            PRIMARY KEY (user_id, quest_id)
        );
        """)
        
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS tournament (
            user_id BIGINT PRIMARY KEY,
            wins BIGINT DEFAULT 0,
            fee_paid BIGINT DEFAULT 0
        );
        """)
        
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS promos (
            code TEXT PRIMARY KEY,
            stars BIGINT DEFAULT 0,
            coins BIGINT DEFAULT 0,
            vip_days BIGINT DEFAULT 0,
            max_uses BIGINT DEFAULT 100,
            uses BIGINT DEFAULT 0
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
# РАНГИ
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

# ============================================================
# 💍 БРАК (С КНОПКАМИ!)
# ============================================================

def marriage_keyboard(target_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="💚 Да, согласен(на)",
                callback_data=f"marry_yes:{target_id}",
            ),
            InlineKeyboardButton(
                text="💔 Нет",
                callback_data=f"marry_no:{target_id}",
            ),
        ]
    ])

async def finish_marriage(target_id: int) -> tuple[bool, str, Optional[int]]:
    request = marry_requests.get(target_id)
    if not request:
        return False, "❌ Предложение уже недействительно.", None

    u1_id = request["from"]
    u2_id = target_id
    today = datetime.date.today()

    async with db_pool.acquire() as conn:
        async with conn.transaction():
            rows = await conn.fetch(
                """
                SELECT user_id, family_id, divorce_until
                FROM users
                WHERE user_id IN ($1, $2)
                FOR UPDATE
                """,
                u1_id,
                u2_id,
            )
            users = {row["user_id"]: row for row in rows}

            if u1_id not in users or u2_id not in users:
                marry_requests.pop(target_id, None)
                return False, "❌ Один из пользователей не найден в базе.", u1_id

            if users[u1_id]["family_id"] or users[u2_id]["family_id"]:
                marry_requests.pop(target_id, None)
                return False, "❌ Кто-то из вас уже состоит в браке.", u1_id

            for row in users.values():
                divorce_until = row["divorce_until"]
                if divorce_until:
                    try:
                        if datetime.date.fromisoformat(divorce_until) > today:
                            marry_requests.pop(target_id, None)
                            return False, "❌ Пока действует запрет на новый брак после развода.", u1_id
                    except ValueError:
                        pass

            fam_id = await conn.fetchval(
                """
                INSERT INTO families (user1_id, user2_id, created_at)
                VALUES ($1, $2, $3)
                RETURNING id
                """,
                u1_id,
                u2_id,
                today.isoformat(),
            )

            await conn.execute(
                """
                UPDATE users
                SET family_id = $1, spouse_id = $2, stars = stars + 5
                WHERE user_id = $3
                """,
                fam_id,
                u2_id,
                u1_id,
            )
            await conn.execute(
                """
                UPDATE users
                SET family_id = $1, spouse_id = $2, stars = stars + 5
                WHERE user_id = $3
                """,
                fam_id,
                u1_id,
                u2_id,
            )

    marry_requests.pop(target_id, None)
    return True, "🎉 <b>СЕМЬЯ СОЗДАНА! +5⭐ каждому!</b>", u1_id

@router.message(F.text.lower().startswith("обручиться"))
async def cmd_marry(message: Message):
    if not message.reply_to_message or not message.reply_to_message.from_user:
        await message.answer(
            "❌ Ответь командой <b>обручиться</b> на сообщение пользователя.",
            parse_mode="HTML",
        )
        return

    if message.reply_to_message.from_user.is_bot:
        await message.answer("❌ С ботом обручиться нельзя!", parse_mode="HTML")
        return

    u1 = await get_or_create_user(message.from_user.id, message.from_user.username)
    target = message.reply_to_message.from_user
    u2 = await get_or_create_user(target.id, target.username)

    if u1["user_id"] == u2["user_id"]:
        await message.answer("❌ Нельзя обручиться с самим собой!", parse_mode="HTML")
        return
    if u1["family_id"]:
        await message.answer("❌ Ты уже состоишь в браке!", parse_mode="HTML")
        return
    if u2["family_id"]:
        await message.answer("❌ Этот человек уже состоит в браке!", parse_mode="HTML")
        return

    today = datetime.date.today()
    for user in (u1, u2):
        if user.get("divorce_until"):
            try:
                if datetime.date.fromisoformat(user["divorce_until"]) > today:
                    await message.answer(
                        "❌ У одного из вас ещё действует запрет на брак после развода.",
                        parse_mode="HTML",
                    )
                    return
            except ValueError:
                pass

    from_name = (
        u1.get("custom_nick")
        or u1.get("username")
        or message.from_user.first_name
        or f"Игрок_{u1['user_id']}"
    )
    target_name = target.first_name or target.username or f"Игрок_{target.id}"

    marry_requests[u2["user_id"]] = {
        "from": u1["user_id"],
        "from_name": from_name,
        "chat_id": message.chat.id,
    }

    await message.answer(
        f"💍 <b>{target_name}</b>, пользователь <b>{from_name}</b> "
        "предлагает тебе обручиться!\n\nВыбери ответ:",
        parse_mode="HTML",
        reply_markup=marriage_keyboard(u2["user_id"]),
    )

@router.callback_query(F.data.startswith("marry_yes:"))
async def process_marry_yes(callback: CallbackQuery):
    target_id = int(callback.data.split(":", 1)[1])

    if callback.from_user.id != target_id:
        await callback.answer("Эта кнопка предназначена другому человеку!", show_alert=True)
        return

    request = marry_requests.get(target_id)
    if not request:
        await callback.answer("Предложение уже недействительно.", show_alert=True)
        return

    await callback.answer("Обрабатываю ответ…")
    success, text, proposer_id = await finish_marriage(target_id)

    if callback.message:
        await callback.message.edit_text(text, parse_mode="HTML")

    if success and proposer_id:
        try:
            await bot.send_message(
                proposer_id,
                f"🎉 {callback.from_user.first_name} согласился(ась)! +5⭐!",
                parse_mode="HTML",
            )
        except Exception:
            pass

@router.callback_query(F.data.startswith("marry_no:"))
async def process_marry_no(callback: CallbackQuery):
    target_id = int(callback.data.split(":", 1)[1])

    if callback.from_user.id != target_id:
        await callback.answer("Эта кнопка предназначена другому человеку!", show_alert=True)
        return

    request = marry_requests.pop(target_id, None)
    if not request:
        await callback.answer("Предложение уже недействительно.", show_alert=True)
        return

    await callback.answer("Предложение отклонено")
    text = f"💔 {callback.from_user.first_name} отказался(ась) от предложения."
    if callback.message:
        await callback.message.edit_text(text, parse_mode="HTML")

    try:
        await bot.send_message(request["from"], text, parse_mode="HTML")
    except Exception:
        pass

@router.message(F.text.lower().in_(["да", "нет"]), F.chat.type == ChatType.PRIVATE)
async def process_marry_text_answer(message: Message):
    target_id = message.from_user.id
    request = marry_requests.get(target_id)
    if not request:
        return

    if message.text.lower() == "нет":
        marry_requests.pop(target_id, None)
        await message.answer("💔 Ты отказался(ась) от предложения!", parse_mode="HTML")
        try:
            await bot.send_message(
                request["from"],
                f"💔 {message.from_user.first_name} отказался(ась) от предложения.",
                parse_mode="HTML",
            )
        except Exception:
            pass
        return

    success, text, proposer_id = await finish_marriage(target_id)
    await message.answer(text, parse_mode="HTML")
    if success and proposer_id:
        try:
            await bot.send_message(
                proposer_id,
                f"🎉 {message.from_user.first_name} согласился(ась)! +5⭐!",
                parse_mode="HTML",
            )
        except Exception:
            pass

@router.message(F.text.lower() == "развод")
async def cmd_divorce(message: Message):
    user = await get_or_create_user(message.from_user.id, message.from_user.username)
    if not user["family_id"]:
        await message.answer("❌ Ты не состоишь в браке!", parse_mode="HTML")
        return

    family_id = user["family_id"]
    ban_date = (datetime.date.today() + datetime.timedelta(days=7)).isoformat()

    async with db_pool.acquire() as conn:
        async with conn.transaction():
            family = await conn.fetchrow(
                "SELECT user1_id, user2_id FROM families WHERE id = $1 FOR UPDATE",
                family_id,
            )
            if not family:
                await conn.execute(
                    "UPDATE users SET family_id = NULL, spouse_id = NULL WHERE user_id = $1",
                    user["user_id"],
                )
                await message.answer("⚠️ Запись семьи не найдена, профиль исправлен.")
                return

            await conn.execute("DELETE FROM families WHERE id = $1", family_id)
            await conn.execute(
                """
                UPDATE users
                SET family_id = NULL, spouse_id = NULL, divorce_until = $1
                WHERE user_id IN ($2, $3)
                """,
                ban_date,
                family["user1_id"],
                family["user2_id"],
            )

    await message.answer("💔 Развод оформлен. Новый брак будет доступен через 7 дней.", parse_mode="HTML")

@router.message(F.text.lower().startswith("ребёнок"))
async def cmd_adopt(message: Message):
    if not message.reply_to_message:
        await message.answer("❌ Ответь на сообщение!", parse_mode="HTML")
        return
    parent = await get_or_create_user(message.from_user.id, message.from_user.username)
    child = await get_or_create_user(message.reply_to_message.from_user.id, message.reply_to_message.from_user.username)
    if not parent['family_id']:
        await message.answer("❌ Сначала семья!", parse_mode="HTML")
        return
    async with db_pool.acquire() as conn:
        await conn.execute("INSERT INTO children (family_id, child_id) VALUES ($1, $2)", parent['family_id'], child['user_id'])
    await message.answer(f"👶 {message.reply_to_message.from_user.first_name} усыновлён!", parse_mode="HTML")

@router.message(F.text.lower() == "семья")
async def cmd_family(message: Message):
    user = await get_or_create_user(message.from_user.id, message.from_user.username)
    if not user['family_id']:
        await message.answer("❌ Нет семьи!", parse_mode="HTML")
        return
    async with db_pool.acquire() as conn:
        fam = await conn.fetchrow("SELECT * FROM families WHERE id = $1", user['family_id'])
    await message.answer(f"💍 <b>СЕМЬЯ</b>\n📅 Создана: {fam['created_at']}\n⭐ Очки: {fam['score']}", parse_mode="HTML")

# ============================================================
# 🎂 ГОДОВЩИНА (ИСПРАВЛЕННАЯ!)
# ============================================================
@router.message(F.text.lower() == "годовщина")
async def cmd_anniversary(message: Message):
    user = await get_or_create_user(message.from_user.id, message.from_user.username)
    if not user["family_id"]:
        await message.answer("❌ Нет семьи!", parse_mode="HTML")
        return

    async with db_pool.acquire() as conn:
        fam = await conn.fetchrow(
            """
            SELECT created_at, last_anniversary_month
            FROM families
            WHERE id = $1
            """,
            user["family_id"],
        )

    if not fam:
        await message.answer("❌ Семья не найдена в базе.", parse_mode="HTML")
        return

    created = datetime.date.fromisoformat(fam["created_at"])
    today = datetime.date.today()
    months = (today.year - created.year) * 12 + today.month - created.month

    if today.day < created.day:
        months -= 1
    months = max(0, months)

    if months <= 0:
        await message.answer("📅 Первый месяц вместе ещё не прошёл.", parse_mode="HTML")
        return

    if months > fam["last_anniversary_month"]:
        reward = min(5 * months, 50)
        async with db_pool.acquire() as conn:
            async with conn.transaction():
                updated = await conn.execute(
                    """
                    UPDATE families
                    SET last_anniversary_month = $1
                    WHERE id = $2 AND last_anniversary_month < $1
                    """,
                    months,
                    user["family_id"],
                )
                if updated == "UPDATE 1":
                    await conn.execute(
                        """
                        UPDATE users
                        SET stars = stars + $1
                        WHERE family_id = $2
                        """,
                        reward,
                        user["family_id"],
                    )

        if updated == "UPDATE 1":
            await message.answer(
                f"🎂 <b>ГОДОВЩИНА! {months} месяцев! +{reward}⭐ каждому!</b>",
                parse_mode="HTML",
            )
        else:
            await message.answer(
                f"📅 Вместе {months} месяцев. Награда уже получена.",
                parse_mode="HTML",
            )
    else:
        await message.answer(f"📅 Вместе {months} месяцев.", parse_mode="HTML")

# ============================================================
# 🎰 РУЛЕТКА (ИСПРАВЛЕННАЯ!)
# ============================================================
@router.message(F.text.lower().startswith(("рулетка", "р ")))
async def cmd_roulette(message: Message):
    chat_id = message.chat.id
    args = message.text.split()

    if len(args) < 3:
        await message.answer(
            "🎰 Ставка: р красное 100 | р 15 100 | р 0 100\nМин. 50💰",
            parse_mode="HTML",
        )
        return

    bet_type = args[1].lower().replace("ё", "е")
    valid_words = {"красное", "red", "черное", "black", "чет", "even", "нечет", "odd"}

    try:
        bet_amount = int(args[2])
    except ValueError:
        await message.answer("❌ Ставка должна быть числом!", parse_mode="HTML")
        return

    if bet_amount < 50:
        await message.answer("❌ Мин. ставка: 50 монет!", parse_mode="HTML")
        return

    if bet_type not in valid_words:
        if not bet_type.isdigit() or not 0 <= int(bet_type) <= 30:
            await message.answer(
                "❌ Ставь на красное, черное, чет, нечет или число от 0 до 30.",
                parse_mode="HTML",
            )
            return

    roul = active_roulettes.get(chat_id)
    if roul and len(roul["bets"]) >= 10:
        await message.answer("❌ Лимит 10 ставок!", parse_mode="HTML")
        return

    user = await get_or_create_user(message.from_user.id, message.from_user.username)

    async with db_pool.acquire() as conn:
        charged = await conn.fetchval(
            """
            UPDATE users
            SET coins = coins - $1
            WHERE user_id = $2 AND coins >= $1
            RETURNING coins
            """,
            bet_amount,
            user["user_id"],
        )

    if charged is None:
        await message.answer("❌ Недостаточно монет!", parse_mode="HTML")
        return

    if chat_id not in active_roulettes:
        active_roulettes[chat_id] = {
            "bets": [],
            "timer_task": asyncio.create_task(run_roulette_timer(chat_id)),
        }

    roul = active_roulettes[chat_id]
    uname = user["custom_nick"] or user["username"] or f"Игрок_{user['user_id']}"
    roul["bets"].append(
        {
            "user_id": user["user_id"],
            "username": uname,
            "type": bet_type,
            "amount": bet_amount,
            "insurance": user["insurance"],
        }
    )

    await message.answer(
        f"✅ Ставка {uname} — {bet_amount}💰 на {bet_type}! ({len(roul['bets'])}/10)",
        parse_mode="HTML",
    )

async def run_roulette_timer(chat_id: int):
    await asyncio.sleep(60)
    roul = active_roulettes.pop(chat_id, None)
    if not roul:
        return

    bets = roul["bets"]
    winning_number = random.randint(0, 30)
    is_zero = winning_number == 0
    is_even = winning_number % 2 == 0 if not is_zero else False
    is_red = winning_number % 2 != 0 if not is_zero else False

    color_str = (
        "🟢 0"
        if is_zero
        else f"🔴 {winning_number}" if is_red else f"⚫ {winning_number}"
    )
    res_text = f"🎰 РУЛЕТКА! Выпало: {color_str}\n\n"

    async with db_pool.acquire() as conn:
        async with conn.transaction():
            for bet in bets:
                uid = bet["user_id"]
                uname = bet["username"]
                bet_type = bet["type"]
                amount = bet["amount"]
                won = False
                multiplier = 0

                if bet_type in ("красное", "red") and is_red:
                    won, multiplier = True, 2
                elif bet_type in ("черное", "black") and not is_red and not is_zero:
                    won, multiplier = True, 2
                elif bet_type in ("чет", "even") and is_even:
                    won, multiplier = True, 2
                elif bet_type in ("нечет", "odd") and not is_even and not is_zero:
                    won, multiplier = True, 2
                elif bet_type == "0" and is_zero:
                    won, multiplier = True, 100
                elif bet_type.isdigit() and int(bet_type) == winning_number:
                    won, multiplier = True, 50

                if won:
                    win_coins = amount * multiplier
                    await conn.execute(
                        """
                        UPDATE users
                        SET coins = coins + $1,
                            wins = wins + 1,
                            total_games = total_games + 1,
                            roulette_games = roulette_games + 1,
                            roulette_wins = roulette_wins + 1,
                            total_coins_won = total_coins_won + $1
                        WHERE user_id = $2
                        """,
                        win_coins,
                        uid,
                    )
                    res_text += f"✅ {uname}: +{win_coins}💰\n"
                else:
                    refund = amount // 2 if bet["insurance"] == 1 else 0
                    if refund:
                        await conn.execute(
                            """
                            UPDATE users
                            SET coins = coins + $1, insurance = 0
                            WHERE user_id = $2
                            """,
                            refund,
                            uid,
                        )

                    await conn.execute(
                        """
                        UPDATE users
                        SET losses = losses + 1,
                            total_games = total_games + 1,
                            roulette_games = roulette_games + 1
                        WHERE user_id = $1
                        """,
                        uid,
                    )
                    insurance_text = "(страховка: возврат 50%)" if refund else ""
                    res_text += f"❌ {uname}: -{amount - refund}💰 {insurance_text}\n"

    await bot.send_message(chat_id, res_text, parse_mode="HTML")

# ============================================================
# 🤠 ДУЭЛЬ
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
    await callback.message.edit_text(f"🤠 ДУЭЛЬ!\n{duel['p1_name']} vs {p2_name}\n💰 Банк: {duel['pot']}💰\n\n👉 Ход: {duel['p1_name']}", reply_markup=kb, parse_mode="HTML")

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
        await callback.message.edit_text(f"🔫 {shooter_name} наносит {damage} урона!\n{duel['p1_name']}: {duel['p1_hp']} HP\n{duel['p2_name']}: {duel['p2_hp']} HP\n💰 Банк: {duel['pot']}💰\n\n👉 Ход: {target_name}", reply_markup=kb, parse_mode="HTML")

# ============================================================
# 🐱 КОТИКИ
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
    await message.answer(f"🐱 {active_cats[cat_id]['p1_name']} вызывает на котиков! Ставка: {bet}💰", reply_markup=kb, parse_mode="HTML")

@router.callback_query(F.data.startswith("accept_cats_"))
async def process_accept_cats(callback: CallbackQuery):
    cat_id = callback.data.replace("accept_cats_", "", 1)
    game = active_cats.get(cat_id)

    if not game:
        await callback.answer("Игра уже недействительна!", show_alert=True)
        return
    if game["active"] or game["p2"] is not None:
        await callback.answer("Вызов уже принял другой игрок!", show_alert=True)
        return

    p1_id = game["p1"]
    p2_id = callback.from_user.id
    if p1_id == p2_id:
        await callback.answer("Нельзя играть с собой!", show_alert=True)
        return

    p2_user = await get_or_create_user(p2_id, callback.from_user.username)

    async with db_pool.acquire() as conn:
        async with conn.transaction():
            players = await conn.fetch(
                "SELECT user_id, coins FROM users WHERE user_id IN ($1, $2) FOR UPDATE",
                p1_id,
                p2_id,
            )
            balances = {row["user_id"]: row["coins"] for row in players}

            if balances.get(p1_id, 0) < game["bet"]:
                active_cats.pop(cat_id, None)
                await callback.answer("У создателя уже недостаточно монет!", show_alert=True)
                return
            if balances.get(p2_id, 0) < game["bet"]:
                await callback.answer("Недостаточно монет!", show_alert=True)
                return

            await conn.execute(
                "UPDATE users SET coins = coins - $1 WHERE user_id IN ($2, $3)",
                game["bet"],
                p1_id,
                p2_id,
            )

    game["p2"] = p2_id
    game["p2_name"] = (
        p2_user["custom_nick"]
        or p2_user["username"]
        or callback.from_user.first_name
    )
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

    text = (
        "🐱 Считай жёлтых 🐈 (чёрные 🐈‍⬛ не считаем!)\n"
        f"💰 Банк: {game['pot']}💰\n\n"
        f"{game['cats_text']}\n\n"
        "✏️ Пиши число!"
    )
    await callback.answer()
    await callback.message.edit_text(text, parse_mode="HTML")

@router.message(F.text.isdigit())
async def process_cats_answer(message: Message):
    for cat_id, game in list(active_cats.items()):
        if not game["active"] or game["winner"]:
            continue
        user_id = message.from_user.id
        if user_id not in [game["p1"], game["p2"]]:
            continue
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
                await conn.execute("UPDATE users SET coins = coins + $1, wins = wins + 1, total_games = total_games + 1, cat_games = cat_games + 1, cat_wins = cat_wins + 1 WHERE user_id = $2", win_pot, user_id)
                loser_id = game["p2"] if user_id == game["p1"] else game["p1"]
                await conn.execute("UPDATE users SET losses = losses + 1, cat_games = cat_games + 1 WHERE user_id = $1", loser_id)
            uname = game["p1_name"] if user_id == game["p1"] else game["p2_name"]
            await message.answer(f"🎉 {uname} угадал! Жёлтых котиков было {val}.\n+{win_pot}💰", parse_mode="HTML")
            del active_cats[cat_id]
            return

# ============================================================
# 🎰 КАЗИНО (ИСПРАВЛЕННОЕ!)
# ============================================================
@router.message(F.text.lower().in_(["казино", "🎰 казино"]))
async def cmd_casino(message: Message):
    user = await get_or_create_user(message.from_user.id, message.from_user.username)

    async with db_pool.acquire() as conn:
        async with conn.transaction():
            charged = await conn.fetchval(
                """
                UPDATE users
                SET stars = stars - 25, casino_games = casino_games + 1,
                    total_games = total_games + 1
                WHERE user_id = $1 AND stars >= 25
                RETURNING stars
                """,
                user["user_id"],
            )
            if charged is None:
                await message.answer("❌ Нужно 25⭐!", parse_mode="HTML")
                return

            rand = random.random()
            if rand < 0.40:
                win_c = random.randint(50, 3000)
                await conn.execute(
                    """
                    UPDATE users
                    SET coins = coins + $1, wins = wins + 1,
                        casino_wins = casino_wins + 1
                    WHERE user_id = $2
                    """,
                    win_c,
                    user["user_id"],
                )
                result = "💰 Монеты"
                reward = f"+{win_c}💰"
                chance = "40%"
            elif rand < 0.70:
                days = random.randint(4, 10)
                until = (datetime.date.today() + datetime.timedelta(days=days)).isoformat()
                await conn.execute(
                    """
                    UPDATE users
                    SET is_vip = 1, vip_until = $1, wins = wins + 1,
                        casino_wins = casino_wins + 1
                    WHERE user_id = $2
                    """,
                    until,
                    user["user_id"],
                )
                result = "👑 VIP"
                reward = f"на {days} дней!"
                chance = "30%"
            elif rand < 0.99:
                win_s = random.randint(10, 75)
                await conn.execute(
                    """
                    UPDATE users
                    SET stars = stars + $1, wins = wins + 1,
                        casino_wins = casino_wins + 1
                    WHERE user_id = $2
                    """,
                    win_s,
                    user["user_id"],
                )
                result = "⭐ Звёзды"
                reward = f"+{win_s}⭐"
                chance = "29%"
            else:
                until = (datetime.date.today() + datetime.timedelta(days=30)).isoformat()
                await conn.execute(
                    """
                    UPDATE users
                    SET coins = coins + 5000, stars = stars + 50,
                        is_vip = 1, vip_until = $1, wins = wins + 1,
                        casino_wins = casino_wins + 1
                    WHERE user_id = $2
                    """,
                    until,
                    user["user_id"],
                )
                result = "🔥 ДЖЕКПОТ!"
                reward = "5000💰 + 50⭐ + VIP 30 дней!"
                chance = "1%"

    await message.answer(
        "🎰 <b>КАЗИНО</b> (-25⭐)\n\n"
        f"🎯 <b>Выпало:</b> {result}\n"
        f"📊 <b>Вероятность:</b> {chance}\n"
        f"💰 <b>Награда:</b> {reward}",
        parse_mode="HTML",
    )

# ============================================================
# 🎁 ПРИЗ
# ============================================================
@router.message(F.text.lower().in_(["приз", "🎁 приз"]))
async def cmd_prize(message: Message):
    if message.chat.type != ChatType.PRIVATE:
        await message.answer("⚠️ Зайди в ЛС к боту!", parse_mode="HTML")
        return
    user = await get_or_create_user(message.from_user.id, message.from_user.username)
    today = datetime.date.today().isoformat()
    if user['last_prize_date'] == today:
        await message.answer("⏳ Уже сегодня!", parse_mode="HTML")
        return
    buttons = [[InlineKeyboardButton(text=f"📦 Сундук #{i}", callback_data=f"chest_{i}")] for i in range(1, 6)]
    await message.answer("🎁 Выбери сундук:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode="HTML")

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
    async with db_pool.acquire() as conn:
        await conn.execute("UPDATE users SET coins = coins + $1, stars = stars + $2, last_prize_date = $3 WHERE user_id = $4",
                         win_coins, win_stars, today, callback.from_user.id)
    text = f"🎉 Сундук #{chosen}\n+{win_coins}💰 +{win_stars}⭐\n\nОстальные:\n"
    for idx, (c, s) in enumerate(rewards, 1):
        text += f"📦 #{idx}: {c}💰 {s}⭐ {'👈' if idx == chosen else ''}\n"
    await callback.message.edit_text(text, parse_mode="HTML")

# ============================================================
# ⭐ ПЕРЕВОД (ИСПРАВЛЕННЫЙ!)
# ============================================================
@router.message(F.text.lower().startswith("перевод"))
async def cmd_transfer(message: Message):
    args = message.text.split()
    if len(args) < 3:
        await message.answer("❌ Формат: перевод @username 10", parse_mode="HTML")
        return

    target_uname = args[1].replace("@", "").strip()
    try:
        amount = int(args[2])
    except ValueError:
        await message.answer("❌ Сумма должна быть числом!", parse_mode="HTML")
        return

    if amount < 1:
        await message.answer("❌ Сумма должна быть больше 0!", parse_mode="HTML")
        return

    sender = await get_or_create_user(message.from_user.id, message.from_user.username)
    today = datetime.date.today().isoformat()

    async with db_pool.acquire() as conn:
        async with conn.transaction():
            sender_row = await conn.fetchrow(
                "SELECT * FROM users WHERE user_id = $1 FOR UPDATE",
                sender["user_id"],
            )
            target = await conn.fetchrow(
                "SELECT * FROM users WHERE LOWER(username) = LOWER($1) FOR UPDATE",
                target_uname,
            )

            if not target:
                await message.answer("❌ Пользователь не найден!", parse_mode="HTML")
                return
            if target["user_id"] == sender["user_id"]:
                await message.answer("❌ Нельзя переводить звёзды самому себе!", parse_mode="HTML")
                return

            transferred = (
                sender_row["daily_stars_transferred"]
                if sender_row["last_transfer_date"] == today
                else 0
            )
            if transferred + amount > 25:
                await message.answer(
                    f"❌ Лимит 25⭐/день! Уже переведено {transferred}⭐",
                    parse_mode="HTML",
                )
                return
            if sender_row["stars"] < amount:
                await message.answer("❌ Недостаточно звёзд!", parse_mode="HTML")
                return

            await conn.execute(
                """
                UPDATE users
                SET stars = stars - $1,
                    daily_stars_transferred = $2,
                    last_transfer_date = $3
                WHERE user_id = $4
                """,
                amount,
                transferred + amount,
                today,
                sender["user_id"],
            )
            await conn.execute(
                "UPDATE users SET stars = stars + $1 WHERE user_id = $2",
                amount,
                target["user_id"],
            )

    await message.answer(f"✅ +{amount}⭐ для @{target_uname}!", parse_mode="HTML")

# ============================================================
# 🏪 МАГАЗИН
# ============================================================
@router.message(F.text.lower().in_(["магазин", "🏪 магазин"]))
async def cmd_shop(message: Message):
    text = """🏪 <b>МАГАЗИН</b>\n👑 VIP 1д — 5⭐\n👑 VIP 7д — 30⭐\n🛡️ Страховка — 5⭐\n💱 1⭐ → 50💰\n✏️ Сменить ник — сменить_ник @ник (10⭐)\n🔒 Скрыть профиль — скрыть_профиль (1000⭐)\n🔓 Открыть профиль — открыть_профиль"""
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👑 VIP 1д (5⭐)", callback_data="buy_vip_1"),
         InlineKeyboardButton(text="👑 VIP 7д (30⭐)", callback_data="buy_vip_7")],
        [InlineKeyboardButton(text="🛡️ Страховка (5⭐)", callback_data="buy_ins"),
         InlineKeyboardButton(text="💱 1⭐→50💰", callback_data="buy_ex")]
    ])
    await message.answer(text, parse_mode="HTML", reply_markup=kb)

@router.callback_query(F.data.startswith("buy_"))
async def process_shop(callback: CallbackQuery):
    user = await get_or_create_user(callback.from_user.id, callback.from_user.username)
    async with db_pool.acquire() as conn:
        if callback.data == "buy_vip_1" and user['stars'] >= 5:
            until = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
            await conn.execute("UPDATE users SET stars = stars - 5, is_vip = 1, vip_until = $1 WHERE user_id = $2", until, user['user_id'])
            await callback.answer("✅ VIP 1 день!", show_alert=True)
        elif callback.data == "buy_vip_7" and user['stars'] >= 30:
            until = (datetime.date.today() + datetime.timedelta(days=7)).isoformat()
            await conn.execute("UPDATE users SET stars = stars - 30, is_vip = 1, vip_until = $1 WHERE user_id = $2", until, user['user_id'])
            await callback.answer("✅ VIP 7 дней!", show_alert=True)
        elif callback.data == "buy_ins" and user['stars'] >= 5:
            await conn.execute("UPDATE users SET stars = stars - 5, insurance = 1 WHERE user_id = $1", user['user_id'])
            await callback.answer("✅ Страховка!", show_alert=True)
        elif callback.data == "buy_ex" and user['stars'] >= 1:
            await conn.execute("UPDATE users SET stars = stars - 1, coins = coins + 50 WHERE user_id = $1", user['user_id'])
            await callback.answer("✅ +50💰!", show_alert=True)
        else:
            await callback.answer("❌ Недостаточно звёзд!", show_alert=True)

# ============================================================
# СМЕНА НИКА
# ============================================================
@router.message(F.text.lower().startswith("сменить_ник"))
async def cmd_change_nick(message: Message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer("❌ Формат: сменить_ник @новый_ник", parse_mode="HTML")
        return
    new_nick = args[1].strip()
    if new_nick.startswith("@"):
        new_nick = new_nick[1:]
    user = await get_or_create_user(message.from_user.id, message.from_user.username)
    if user['stars'] < 10:
        await message.answer("❌ Нужно 10⭐!", parse_mode="HTML")
        return
    async with db_pool.acquire() as conn:
        await conn.execute("UPDATE users SET stars = stars - 10, custom_nick = $1 WHERE user_id = $2", new_nick, message.from_user.id)
    await message.answer(f"✅ Ник изменён на: {new_nick}", parse_mode="HTML")

# ============================================================
# СКРЫТИЕ ПРОФИЛЯ
# ============================================================
@router.message(F.text.lower() == "скрыть_профиль")
async def cmd_hide_profile(message: Message):
    user = await get_or_create_user(message.from_user.id, message.from_user.username)
    if user['stars'] < 1000:
        await message.answer("❌ Нужно 1000⭐!", parse_mode="HTML")
        return
    async with db_pool.acquire() as conn:
        await conn.execute("UPDATE users SET stars = stars - 1000, is_hidden = 1 WHERE user_id = $1", message.from_user.id)
    await message.answer("🔒 Профиль скрыт за 1000⭐!", parse_mode="HTML")

@router.message(F.text.lower() == "открыть_профиль")
async def cmd_unhide_profile(message: Message):
    async with db_pool.acquire() as conn:
        await conn.execute("UPDATE users SET is_hidden = 0 WHERE user_id = $1", message.from_user.id)
    await message.answer("🔓 Профиль открыт!", parse_mode="HTML")

# ============================================================
# ПРОФИЛЬ
# ============================================================
@router.message(F.text.lower().startswith(("профиль", "п")))
async def cmd_profile(message: Message):
    args = message.text.split()
    if len(args) > 1:
        target_uname = args[1].replace("@", "")
        async with db_pool.acquire() as conn:
            target_row = await conn.fetchrow("SELECT * FROM users WHERE username = $1", target_uname)
            if not target_row:
                await message.answer("❌ Пользователь не найден!", parse_mode="HTML")
                return
            user = dict(target_row)
            if user.get('is_hidden', 0) == 1:
                await message.answer("🔒 Профиль скрыт!", parse_mode="HTML")
                return
    else:
        user = await get_or_create_user(message.from_user.id, message.from_user.username)
    rank = calculate_rank(user['wins'], user['total_games'])
    display_name = user['custom_nick'] or user['username'] or f"Игрок_{user['user_id']}"
    vip_days = await get_vip_days(user)
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
    text = (f"👤 <b>Профиль:</b> {display_name}\n🎖️ <b>Ранг:</b> {rank}\n💳 <b>Статус:</b> {'👑 VIP' if user['is_vip'] else 'Обычный'}\n👑 <b>VIP дней:</b> {vip_days}\n💰 <b>Монеты:</b> {user['coins']:,}\n⭐ <b>Звёзды:</b> {user['stars']}\n🎖️ <b>Титулы:</b> {titles_str}\n📊 <b>Всего игр:</b> {user['total_games']}\n━━━━━━━━━━━━━━━━━\n📈 <b>Статистика:</b>\n{stats_text}\n🏆 <b>Лучшая игра:</b> {best_game} ({best_ratio}%)")
    reply_markup = get_main_keyboard() if message.chat.type == ChatType.PRIVATE else None
    await message.answer(text, parse_mode="HTML", reply_markup=reply_markup)

# ============================================================
# СТАТИСТИКА
# ============================================================
@router.message(F.text.lower().in_(["статистика", "стата"]))
async def cmd_stats(message: Message):
    u = await get_or_create_user(message.from_user.id, message.from_user.username)
    text = f"""📊 <b>СТАТИСТИКА</b>\n🎰 Рулетка: {u['roulette_wins']}/{u['roulette_games']} побед\n🤠 Дуэль: {u['duel_wins']}/{u['duel_games']} побед\n🐱 Котики: {u['cat_wins']}/{u['cat_games']} побед\n🎰 Казино: {u['casino_wins']}/{u['casino_games']} побед\n\n🔥 Всего игр: {u['total_games']}\n🏆 Побед: {u['wins']}\n💀 Поражений: {u['losses']}"""
    await message.answer(text, parse_mode="HTML")

# ============================================================
# ТОПЫ
# ============================================================
@router.message(F.text.lower().in_(["топ", "🏆 топ"]))
async def cmd_top(message: Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="💍 Топ Семей", callback_data="top_families")
    ]])
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("SELECT user_id, username, custom_nick, wins, coins, is_hidden FROM users WHERE user_id != $1 ORDER BY wins DESC LIMIT 10", ADMIN_ID)
    text = "🏆 <b>ТОП ИГРОКОВ:</b>\n\n"
    for idx, r in enumerate(rows, start=1):
        name = r['custom_nick'] or r['username'] or f"Игрок_{r['user_id']}"
        if r['is_hidden'] == 1:
            text += f"{idx}. {name} — {r['wins']} побед ({r['coins']}💰) 🔒\n"
        else:
            text += f"{idx}. <a href='tg://user?id={r['user_id']}'>{name}</a> — {r['wins']} побед ({r['coins']}💰)\n"
    await message.answer(text, parse_mode="HTML", reply_markup=kb)

@router.callback_query(F.data == "top_families")
@router.message(F.text.lower() == "топ_семей")
async def cmd_top_families(event):
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("SELECT id, score, created_at FROM families ORDER BY score DESC LIMIT 10")
    text = "💍 <b>ТОП СЕМЕЙ:</b>\n\n"
    for idx, r in enumerate(rows, start=1):
        text += f"{idx}. Семья #{r['id']} — {r['score']} очков ({r['created_at']})\n"
    if isinstance(event, CallbackQuery):
        await event.message.edit_text(text, parse_mode="HTML")
    else:
        await event.answer(text, parse_mode="HTML")

# ============================================================
# КВЕСТЫ
# ============================================================
@router.message(F.text.lower().in_(["квесты", "🎯 квесты"]))
async def cmd_quests(message: Message):
    text = """🎯 <b>ЕЖЕНЕДЕЛЬНЫЕ КВЕСТЫ</b>\n1. Сыграть 10 игр — 30💰\n2. Выиграть 5 дуэлей — 2⭐\n3. Угадать число в рулетке — 5⭐\n4. Сыграть в котиков 3 раза — 20💰\n5. Выиграть 500 монет за день — 3⭐\n🎁 Бонус за все 5: +10⭐!"""
    await message.answer(text, parse_mode="HTML")

# ============================================================
# ТУРНИРЫ
# ============================================================
@router.message(F.text.lower().in_(["турнир", "🏆 турнир"]))
async def cmd_tournament(message: Message):
    text = """🏆 <b>ЕЖЕНЕДЕЛЬНЫЙ ТУРНИР</b>\n🗓️ Пн - Вс\n💰 Призовой фонд: 500⭐ + взносы\n🥇 1 место — 60%\n🥈 2 место — 30%\n🥉 3 место — 10%\n🎟️ Участие: 10⭐"""
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🎟️ Участвовать (10⭐)", callback_data="join_tournament")
    ]])
    await message.answer(text, parse_mode="HTML", reply_markup=kb)

@router.callback_query(F.data == "join_tournament")
async def process_join_tournament(callback: CallbackQuery):
    user = await get_or_create_user(callback.from_user.id, callback.from_user.username)
    if user['stars'] < 10:
        await callback.answer("❌ Нужно 10⭐!", show_alert=True)
        return
    async with db_pool.acquire() as conn:
        await conn.execute("UPDATE users SET stars = stars - 10 WHERE user_id = $1", user['user_id'])
        await conn.execute("INSERT INTO tournament (user_id, fee_paid) VALUES ($1, 1) ON CONFLICT (user_id) DO UPDATE SET fee_paid = 1", user['user_id'])
    await callback.answer("✅ Ты в турнире!", show_alert=True)

# ============================================================
# ПРОМОКОДЫ
# ============================================================
@router.message(Command("create_promo"))
async def cmd_create_promo(message: Message, command: CommandObject):
    if message.from_user.id != ADMIN_ID:
        return
    args = command.args.split() if command.args else []
    if len(args) < 3:
        await message.answer("Формат: /create_promo КОД ЗВЕЗДЫ МОНЕТЫ VIP_ДНЕЙ", parse_mode="HTML")
        return
    code, stars, coins = args[0], int(args[1]), int(args[2])
    vip_days = int(args[3]) if len(args) > 3 else 0
    async with db_pool.acquire() as conn:
        await conn.execute("INSERT INTO promos (code, stars, coins, vip_days, max_uses) VALUES ($1, $2, $3, $4, 100) ON CONFLICT (code) DO UPDATE SET stars = $2, coins = $3, vip_days = $4", code, stars, coins, vip_days)
    await message.answer(f"✅ Промокод <b>{code}</b> создан!", parse_mode="HTML")

@router.message(Command("promo"))
async def cmd_use_promo(message: Message, command: CommandObject):
    if not command.args:
        return
    code = command.args.strip()
    uid = message.from_user.id
    async with db_pool.acquire() as conn:
        p = await conn.fetchrow("SELECT * FROM promos WHERE code = $1", code)
        if not p:
            await message.answer("❌ Нет такого промокода!", parse_mode="HTML")
            return
        if p['uses'] >= p['max_uses']:
            await message.answer("❌ Промокод уже использован!", parse_mode="HTML")
            return
        await conn.execute("UPDATE promos SET uses = uses + 1 WHERE code = $1", code)
        await conn.execute("UPDATE users SET stars = stars + $1, coins = coins + $2 WHERE user_id = $3", p['stars'], p['coins'], uid)
        if p['vip_days'] > 0:
            until = (datetime.date.today() + datetime.timedelta(days=p['vip_days'])).isoformat()
            await conn.execute("UPDATE users SET is_vip = 1, vip_until = $1 WHERE user_id = $2", until, uid)
    await message.answer(f"🎉 <b>Промокод активирован!</b>\n💰 +{p['coins']}💰\n⭐ +{p['stars']}⭐\n👑 VIP +{p['vip_days']} дн.", parse_mode="HTML")

# ============================================================
# АДМИН-ПАНЕЛЬ
# ============================================================
@router.message(Command("admin"))
async def cmd_admin(message: Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("❌ У тебя нет доступа!", parse_mode="HTML")
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎫 Создать промокод", callback_data="admin_promo")],
        [InlineKeyboardButton(text="📊 Статистика бота", callback_data="admin_stats")],
        [InlineKeyboardButton(text="👑 Выдать VIP", callback_data="admin_vip")],
        [InlineKeyboardButton(text="📋 Список промокодов", callback_data="admin_list_promos")],
        [InlineKeyboardButton(text="🗑️ Удалить промокод", callback_data="admin_delete_promo")]
    ])
    await message.answer("🛠️ <b>АДМИН-ПАНЕЛЬ</b>", reply_markup=kb, parse_mode="HTML")

@router.callback_query(F.data == "admin_promo")
async def admin_promo(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("❌ Нет доступа!", show_alert=True)
        return
    await callback.message.edit_text("🎫 <b>Создание промокода</b>\n\nОтправь команду:\n<code>/create_promo КОД ЗВЕЗДЫ МОНЕТЫ VIP_ДНЕЙ</code>", parse_mode="HTML")

@router.callback_query(F.data == "admin_stats")
async def admin_stats(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("❌ Нет доступа!", show_alert=True)
        return
    async with db_pool.acquire() as conn:
        users = await conn.fetchval("SELECT COUNT(*) FROM users")
        total_coins = await conn.fetchval("SELECT COALESCE(SUM(coins), 0) FROM users")
        total_stars = await conn.fetchval("SELECT COALESCE(SUM(stars), 0) FROM users")
    await callback.message.edit_text(f"📊 <b>СТАТИСТИКА БОТА</b>\n\n👤 <b>Всего игроков:</b> {users}\n💰 <b>Всего монет:</b> {total_coins:,}\n⭐ <b>Всего звёзд:</b> {total_stars:,}", parse_mode="HTML")

@router.callback_query(F.data == "admin_vip")
async def admin_vip(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("❌ Нет доступа!", show_alert=True)
        return
    await callback.message.edit_text("👑 <b>Выдача VIP</b>\n\nОтправь команду:\n<code>/vip @username ДНИ</code>", parse_mode="HTML")

@router.message(Command("vip"))
async def cmd_give_vip(message: Message, command: CommandObject):
    if message.from_user.id != ADMIN_ID:
        await message.answer("❌ У тебя нет доступа!", parse_mode="HTML")
        return
    args = command.args.split() if command.args else []
    if len(args) < 2:
        await message.answer("❌ Формат: /vip @username ДНИ", parse_mode="HTML")
        return
    target_uname = args[0].replace("@", "")
    try:
        days = int(args[1])
    except ValueError:
        await message.answer("❌ Дни должны быть числом!", parse_mode="HTML")
        return
    async with db_pool.acquire() as conn:
        target = await conn.fetchrow("SELECT * FROM users WHERE username = $1", target_uname)
        if not target:
            await message.answer("❌ Пользователь не найден!", parse_mode="HTML")
            return
        until = (datetime.date.today() + datetime.timedelta(days=days)).isoformat()
        await conn.execute("UPDATE users SET is_vip = 1, vip_until = $1 WHERE user_id = $2", until, target['user_id'])
    await message.answer(f"✅ @{target_uname} получил VIP на {days} дней!", parse_mode="HTML")

@router.callback_query(F.data == "admin_list_promos")
async def admin_list_promos(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("❌ Нет доступа!", show_alert=True)
        return
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("SELECT code, stars, coins, vip_days, max_uses, uses FROM promos ORDER BY code")
    if not rows:
        await callback.message.edit_text("📋 <b>Список промокодов</b>\n\n❌ Нет активных промокодов!", parse_mode="HTML")
        return
    text = "📋 <b>СПИСОК ПРОМОКОДОВ</b>\n\n"
    for r in rows:
        status = "✅ Активен" if r['uses'] < r['max_uses'] else "❌ Использован"
        text += f"🔹 <b>{r['code']}</b>\n   ⭐ +{r['stars']}⭐ | 💰 +{r['coins']}💰 | 👑 VIP {r['vip_days']}дн.\n   Использовано: {r['uses']}/{r['max_uses']} — {status}\n\n"
    await callback.message.edit_text(text, parse_mode="HTML")

@router.callback_query(F.data == "admin_delete_promo")
async def admin_delete_promo(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("❌ Нет доступа!", show_alert=True)
        return
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("SELECT code FROM promos ORDER BY code")
    if not rows:
        await callback.message.edit_text("❌ Нет промокодов для удаления!", parse_mode="HTML")
        return
    buttons = []
    for r in rows:
        buttons.append([InlineKeyboardButton(text=f"🗑️ {r['code']}", callback_data=f"delete_promo_{r['code']}")])
    buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data="admin_back")])
    await callback.message.edit_text("🗑️ <b>УДАЛЕНИЕ ПРОМОКОДА</b>\n\nВыбери промокод для удаления:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode="HTML")

@router.callback_query(F.data.startswith("delete_promo_"))
async def process_delete_promo(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("❌ Нет доступа!", show_alert=True)
        return
    code = callback.data.replace("delete_promo_", "")
    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM promos WHERE code = $1", code)
    await callback.answer(f"✅ Промокод {code} удалён!", show_alert=True)
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("SELECT code FROM promos ORDER BY code")
    if not rows:
        await callback.message.edit_text("✅ Промокод удалён! Больше нет активных промокодов.", parse_mode="HTML")
        return
    buttons = []
    for r in rows:
        buttons.append([InlineKeyboardButton(text=f"🗑️ {r['code']}", callback_data=f"delete_promo_{r['code']}")])
    buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data="admin_back")])
    await callback.message.edit_text("🗑️ <b>УДАЛЕНИЕ ПРОМОКОДА</b>\n\nВыбери промокод для удаления:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode="HTML")

@router.callback_query(F.data == "admin_back")
async def admin_back(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("❌ Нет доступа!", show_alert=True)
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎫 Создать промокод", callback_data="admin_promo")],
        [InlineKeyboardButton(text="📊 Статистика бота", callback_data="admin_stats")],
        [InlineKeyboardButton(text="👑 Выдать VIP", callback_data="admin_vip")],
        [InlineKeyboardButton(text="📋 Список промокодов", callback_data="admin_list_promos")],
        [InlineKeyboardButton(text="🗑️ Удалить промокод", callback_data="admin_delete_promo")]
    ])
    await callback.message.edit_text("🛠️ <b>АДМИН-ПАНЕЛЬ</b>", reply_markup=kb, parse_mode="HTML")

# ============================================================
# ПОМОЩЬ (САМЫЙ ПОСЛЕДНИЙ!)
# ============================================================
HELP_TEXT = """🎮 <b>ПОЛНЫЙ СПИСОК ВОЗМОЖНОСТЕЙ БОТА</b>\n\n⭐ Звёзды — ИГРОВАЯ ВАЛЮТА!\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n🎲 <b>ИГРЫ</b>\n🎰 Рулетка — р / рулетка (красное, черное, чет, нечет, число)\n🤠 Дуэль — дуэль (сумма) — пошагово\n🐱 Котики — котики (сумма) — вызови соперника!\n🎰 Казино — казино — за 25⭐\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n💰 <b>ЭКОНОМИКА</b>\n🎁 Приз — приз (1 раз/день, ТОЛЬКО в ЛС)\n🏪 Магазин — магазин\n⭐ Перевод — перевод @username (сумма) (макс 25⭐/день)\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n👤 <b>ПРОФИЛЬ И ТОПЫ</b>\n👤 Профиль — профиль / профиль @username\n📊 Статистика — статистика / стата\n🏆 Топы — топ, топ_семей\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n💍 <b>СЕМЬЯ</b>\n💍 обручиться @username\n👶 ребёнок @username\n💔 развод\n👨‍👩‍👧‍👦 семья\n🎂 годовщина\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n🎯 <b>КВЕСТЫ И ТУРНИРЫ</b>\n🎯 квесты\n🏆 турнир"""

@router.message(Command("start"))
@router.message(F.text.lower().in_(["помощь", "help", "❓ помощь"]))
async def cmd_help(message: Message):
    await get_or_create_user(message.from_user.id, message.from_user.username)
    if message.chat.type == ChatType.PRIVATE:
        await message.answer(HELP_TEXT, parse_mode="HTML", reply_markup=get_main_keyboard())
    else:
        await message.answer(HELP_TEXT, parse_mode="HTML")

# ============================================================
# ЗАПУСК
# ============================================================
async def main():
    await init_db()
    await start_web_server()
    print("🤖 БОТ ЗАПУЩЕН!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
