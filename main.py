import os
import random
import asyncio
import datetime
import logging
from typing import Optional, Dict, List

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
CHAT_ID = os.getenv("CHAT_ID", "")
PORT = int(os.getenv("PORT", 8080))

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
router = Router()
dp.include_router(router)

db_pool: Optional[asyncpg.Pool] = None

# Активные сессии игр в ОЗУ
active_roulettes: Dict[int, dict] = {}
active_duels: Dict[str, dict] = {}
active_cats: Dict[int, dict] = {}
pending_marriages: Dict[str, dict] = {}
pending_renames: Dict[str, dict] = {}
admin_mailing_state: Dict[int, bool] = {}

# ---------------------------------------------------------
# ВЕБ-СЕРВЕР ДЛЯ RENDER
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
    logging.info(f"🌐 Веб-сервер запущен на порту {PORT}")

# ---------------------------------------------------------
# ГЛАВНАЯ КЛАВИАТУРА
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
            username VARCHAR(255),
            custom_nick VARCHAR(255) DEFAULT NULL,
            custom_emoji VARCHAR(50) DEFAULT '',
            coins BIGINT DEFAULT 1000,
            stars INT DEFAULT 10,
            is_vip BOOLEAN DEFAULT FALSE,
            vip_until TIMESTAMP DEFAULT NULL,
            is_hidden BOOLEAN DEFAULT FALSE,
            insurance BOOLEAN DEFAULT FALSE,
            family_id INT DEFAULT NULL,
            spouse_id BIGINT DEFAULT NULL,
            divorce_until DATE DEFAULT NULL,
            daily_stars_transferred INT DEFAULT 0,
            last_transfer_date DATE DEFAULT NULL,
            last_prize_date DATE DEFAULT NULL,
            wins INT DEFAULT 0,
            losses INT DEFAULT 0,
            total_games INT DEFAULT 0,
            total_coins_won BIGINT DEFAULT 0,
            daily_net_win INT DEFAULT 0,
            streak_days INT DEFAULT 0,
            last_active_date DATE DEFAULT NULL,
            last_game_result VARCHAR(50) DEFAULT 'Нет',
            top3_family_count INT DEFAULT 0,
            roulette_games INT DEFAULT 0,
            roulette_wins INT DEFAULT 0,
            duel_games INT DEFAULT 0,
            duel_wins INT DEFAULT 0,
            cat_games INT DEFAULT 0,
            cat_wins INT DEFAULT 0,
            casino_games INT DEFAULT 0,
            casino_wins INT DEFAULT 0,
            quests_completed INT DEFAULT 0,
            quest1 INT DEFAULT 0,
            quest2 INT DEFAULT 0,
            quest3 INT DEFAULT 0,
            quest4 INT DEFAULT 0,
            quest5 INT DEFAULT 0,
            quest1_done BOOLEAN DEFAULT FALSE,
            quest2_done BOOLEAN DEFAULT FALSE,
            quest3_done BOOLEAN DEFAULT FALSE,
            quest4_done BOOLEAN DEFAULT FALSE,
            quest5_done BOOLEAN DEFAULT FALSE,
            quest_bonus_claimed BOOLEAN DEFAULT FALSE,
            tournament_wins INT DEFAULT 0,
            tournament_fee_paid BOOLEAN DEFAULT FALSE,
            tournament_score INT DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS families (
            id SERIAL PRIMARY KEY,
            user1_id BIGINT NOT NULL,
            user2_id BIGINT NOT NULL,
            name VARCHAR(255) DEFAULT NULL,
            score INT DEFAULT 0,
            created_at DATE NOT NULL,
            top1_count INT DEFAULT 0,
            last_anniversary_month INT DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS children (
            id SERIAL PRIMARY KEY,
            family_id INT NOT NULL,
            child_id BIGINT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS achievements (
            user_id BIGINT NOT NULL,
            ach_id VARCHAR(50) NOT NULL,
            PRIMARY KEY (user_id, ach_id)
        );

        CREATE TABLE IF NOT EXISTS promos (
            code VARCHAR(50) PRIMARY KEY,
            stars INT DEFAULT 0,
            coins BIGINT DEFAULT 0,
            vip_days INT DEFAULT 0,
            max_uses INT DEFAULT 100,
            uses INT DEFAULT 0,
            created_at TIMESTAMP DEFAULT NOW()
        );

        CREATE TABLE IF NOT EXISTS user_promos (
            user_id BIGINT NOT NULL,
            code VARCHAR(50) NOT NULL,
            PRIMARY KEY (user_id, code)
        );
        """)
        
        # Конвертация типов
        boolean_fields = [
            'is_vip', 'is_hidden', 'insurance',
            'quest1_done', 'quest2_done', 'quest3_done',
            'quest4_done', 'quest5_done', 'quest_bonus_claimed',
            'tournament_fee_paid'
        ]
        
        for field in boolean_fields:
            try:
                col_info = await conn.fetchrow("""
                    SELECT data_type 
                    FROM information_schema.columns 
                    WHERE table_name = 'users' AND column_name = $1
                """, field)
                
                if col_info and col_info['data_type'] in ['integer', 'bigint']:
                    await conn.execute(f"""
                        ALTER TABLE users 
                        ALTER COLUMN {field} TYPE BOOLEAN 
                        USING CASE WHEN {field} = 0 THEN FALSE ELSE TRUE END
                    """)
                    logging.info(f"✅ Колонка {field} сконвертирована в BOOLEAN")
            except Exception as e:
                logging.info(f"Конвертация {field} не требуется: {e}")
        
        logging.info("🗄️ База данных PostgreSQL успешно инициализирована!")

async def get_or_create_user(user_id: int, username: Optional[str]) -> dict:
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM users WHERE user_id = $1", user_id)
        today = datetime.date.today()
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
# РАНГИ, ТИТУЛЫ, АЧИВКИ, КВЕСТЫ
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
            if fam and fam['created_at']:
                try:
                    if isinstance(fam['created_at'], datetime.date):
                        if (datetime.date.today() - fam['created_at']).days >= 365:
                            titles.append("👑 Золотая семья")
                    elif isinstance(fam['created_at'], str):
                        created = datetime.datetime.strptime(fam['created_at'], '%Y-%m-%d').date()
                        if (datetime.date.today() - created).days >= 365:
                            titles.append("👑 Золотая семья")
                except Exception:
                    pass
    return titles

async def check_achievements(user_id: int):
    async with db_pool.acquire() as conn:
        u = await conn.fetchrow("SELECT * FROM users WHERE user_id = $1", user_id)
        if not u: return
        rows = await conn.fetch("SELECT ach_id FROM achievements WHERE user_id = $1", user_id)
        unlocked = {r['ach_id'] for r in rows}

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
                    await bot.send_message(user_id, f"🏆 <b>АЧИВКА!</b> {title}\n+{r_coins}💰, +{r_stars}⭐", parse_mode="HTML")
                except Exception:
                    pass

async def check_quests(user_id: int):
    async with db_pool.acquire() as conn:
        u = await conn.fetchrow("SELECT * FROM users WHERE user_id = $1", user_id)
        if not u: return

        if u['total_games'] >= 10 and not u['quest1_done']:
            await conn.execute("UPDATE users SET coins = coins + 30, quest1_done = TRUE, quests_completed = quests_completed + 1 WHERE user_id = $1", user_id)
            await bot.send_message(user_id, "✅ Квест 1 выполнен (10 игр)! +30💰")

        if u['duel_wins'] >= 5 and not u['quest2_done']:
            await conn.execute("UPDATE users SET stars = stars + 2, quest2_done = TRUE, quests_completed = quests_completed + 1 WHERE user_id = $1", user_id)
            await bot.send_message(user_id, "✅ Квест 2 выполнен (5 побед в дуэли)! +2⭐")

        if u['roulette_wins'] >= 1 and not u['quest3_done']:
            await conn.execute("UPDATE users SET stars = stars + 5, quest3_done = TRUE, quests_completed = quests_completed + 1 WHERE user_id = $1", user_id)
            await bot.send_message(user_id, "✅ Квест 3 выполнен (Угадали в рулетке)! +5⭐")

        if u['cat_games'] >= 3 and not u['quest4_done']:
            await conn.execute("UPDATE users SET coins = coins + 20, quest4_done = TRUE, quests_completed = quests_completed + 1 WHERE user_id = $1", user_id)
            await bot.send_message(user_id, "✅ Квест 4 выполнен (3 игры в котиков)! +20💰")

        if u['daily_net_win'] >= 500 and not u['quest5_done']:
            await conn.execute("UPDATE users SET stars = stars + 3, quest5_done = TRUE, quests_completed = quests_completed + 1 WHERE user_id = $1", user_id)
            await bot.send_message(user_id, "✅ Квест 5 выполнен (+500 монет за день)! +3⭐")

        u_updated = await conn.fetchrow("SELECT quests_completed, quest_bonus_claimed FROM users WHERE user_id = $1", user_id)
        if u_updated['quests_completed'] >= 5 and not u_updated['quest_bonus_claimed']:
            await conn.execute("UPDATE users SET stars = stars + 10, quest_bonus_claimed = TRUE WHERE user_id = $1", user_id)
            await bot.send_message(user_id, "🎉 <b>БОНУС!</b> Все 5 еженедельных квестов выполнены! +10⭐", parse_mode="HTML")

async def get_tournament_time_left() -> str:
    now = datetime.datetime.utcnow()
    days_until_sunday = (6 - now.weekday()) % 7
    if days_until_sunday == 0 and now.hour >= 23 and now.minute >= 59:
        return "⏳ Турнир завершается СЕЙЧАС!"
    target = (now + datetime.timedelta(days=days_until_sunday)).replace(hour=23, minute=59, second=0, microsecond=0)
    diff = target - now
    hours, remainder = divmod(diff.seconds, 3600)
    minutes, _ = divmod(remainder, 60)
    return f"⏳ Осталось до конца турнира: {diff.days}д {hours}ч {minutes}м"

# ---------------------------------------------------------
# АВТОМАТИЧЕСКИЕ ЗАДАЧИ (КРОН)
# ---------------------------------------------------------
async def finish_tournament():
    async with db_pool.acquire() as conn:
        participants = await conn.fetch(
            "SELECT user_id, tournament_score FROM users WHERE tournament_fee_paid = TRUE AND tournament_score > 0 ORDER BY tournament_score DESC LIMIT 3"
        )
        if not participants:
            await conn.execute("UPDATE users SET tournament_fee_paid = FALSE, tournament_score = 0 WHERE tournament_fee_paid = TRUE")
            return

        total_fee = await conn.fetchval("SELECT COUNT(*) * 10 FROM users WHERE tournament_fee_paid = TRUE") or 0
        prize_pool = 500 + total_fee
        prizes = [int(prize_pool * 0.6), int(prize_pool * 0.3), int(prize_pool * 0.1)]

        for idx, p in enumerate(participants):
            stars_earned = prizes[idx]
            await conn.execute("UPDATE users SET stars = stars + $1 WHERE user_id = $2", stars_earned, p['user_id'])
            try:
                await bot.send_message(p['user_id'], f"🏆 <b>ТУРНИР ЗАВЕРШЕН!</b>\nВаше место: #{idx+1}\nНаграда: +{stars_earned}⭐", parse_mode="HTML")
            except Exception:
                pass

        await conn.execute("UPDATE users SET tournament_fee_paid = FALSE, tournament_score = 0 WHERE tournament_fee_paid = TRUE")

async def daily_cron_loop():
    while True:
        now = datetime.datetime.utcnow()
        target = now.replace(hour=21, minute=0, second=0, microsecond=0)
        if now >= target:
            target += datetime.timedelta(days=1)
        await asyncio.sleep((target - now).total_seconds())

        try:
            async with db_pool.acquire() as conn:
                top_users = await conn.fetch("SELECT user_id FROM users WHERE COALESCE(is_hidden, 0) = 0 ORDER BY wins DESC LIMIT 3")
                rewards_u = [(25, 100), (15, 50), (10, 25)]
                for idx, u in enumerate(top_users):
                    s, c = rewards_u[idx]
                    await conn.execute("UPDATE users SET stars = stars + $1, coins = coins + $2 WHERE user_id = $3", s, c, u['user_id'])
                    try:
                        await bot.send_message(u['user_id'], f"🏆 <b>НАГРАДА ЗА ТОП!</b>\nМесто #{idx+1}: +{s}⭐ +{c}💰", parse_mode="HTML")
                    except Exception:
                        pass

                top_fams = await conn.fetch("SELECT user1_id, user2_id, id FROM families ORDER BY score DESC LIMIT 3")
                rewards_f = [15, 10, 5]
                for idx, f in enumerate(top_fams):
                    s = rewards_f[idx]
                    await conn.execute("UPDATE users SET stars = stars + $1 WHERE user_id IN ($2, $3)", s, f['user1_id'], f['user2_id'])
                    if idx == 0:
                        await conn.execute("UPDATE families SET top1_count = top1_count + 1 WHERE id = $1", f['id'])
                    for uid in [f['user1_id'], f['user2_id']]:
                        try:
                            await bot.send_message(uid, f"💍 <b>НАГРАДА СЕМЬЕ!</b>\nМесто #{idx+1}: +{s}⭐ каждому!", parse_mode="HTML")
                        except Exception:
                            pass

                await conn.execute("UPDATE users SET daily_stars_transferred = 0, daily_net_win = 0")
                await conn.execute("UPDATE users SET is_vip = FALSE WHERE vip_until < NOW() AND vip_until IS NOT NULL")

                if datetime.datetime.utcnow().weekday() == 6:
                    await finish_tournament()
                    await conn.execute("""
                        UPDATE users SET quest1_done = FALSE, quest2_done = FALSE, quest3_done = FALSE,
                        quest4_done = FALSE, quest5_done = FALSE, quest_bonus_claimed = FALSE, quests_completed = 0
                    """)
        except Exception as e:
            logging.error(f"❌ Ошибка в кроне: {e}")

# ---------------------------------------------------------
# ТЕКСТ ПОМОЩИ
# ---------------------------------------------------------
HELP_TEXT = """🎮 <b>ПОЛНЫЙ СПИСОК ВОЗМОЖНОСТЕЙ БОТА</b>

⭐ Звёзды — ИГРОВАЯ ВАЛЮТА!

━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🎲 <b>ИГРЫ</b>
🎰 Рулетка — р / рулетка [ставка] [сумма]
🤠 Дуэль — дуэль [сумма] (Только в чатах)
🐱 Котики — котики [ставка] (Только в чатах)
🎰 Казино — казино (За 25⭐)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💰 <b>ЭКОНОМИКА</b>
🎁 Приз — приз (Раз в день, ТОЛЬКО в ЛС)
🏪 Магазин — магазин
⭐ Перевод — перевод @username [сумма] (макс 25⭐/день)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━
👤 <b>ПРОФИЛЬ И ТОПЫ</b>
👤 Профиль — профиль / п
📊 Статистика — статистика / стата
🏆 Топы — топ, топ_семей

━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💍 <b>СЕМЬЯ</b>
💍 обручиться [имя_семьи]
👶 ребёнок @username
💔 развод
👨‍👩‍👧‍👦 семья
🎂 годовщина
✏️ сменить_имя_семьи @партнёр НовоеИмя

━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🎯 <b>КВЕСТЫ И ТУРНИРЫ</b>
🎯 квесты
🏆 турнир
"""

# ============================================================
# 🚀 ОСНОВНЫЕ КОМАНДЫ
# ============================================================
@router.message(Command("start"))
async def cmd_start(message: Message):
    user = await get_or_create_user(message.from_user.id, message.from_user.username)
    await message.answer(HELP_TEXT, parse_mode="HTML", reply_markup=get_main_keyboard())

@router.message(F.text.lower().in_(["помощь", "help", "❓ помощь"]))
async def cmd_help(message: Message):
    await message.answer(HELP_TEXT, parse_mode="HTML", reply_markup=get_main_keyboard())

# ============================================================
# 📌 ДИАГНОСТИКА (ЛОВИТ ВСЕ СООБЩЕНИЯ)
# ============================================================
@router.message()
async def catch_all(message: Message):
    """Ловит все сообщения, которые не обработаны другими хендлерами"""
    logging.info(f"📩 Получено сообщение: {message.text} от {message.from_user.id}")
    
    # Проверяем, не является ли сообщение командой с кнопки
    text = message.text.lower()
    
    # Список известных команд
    known_commands = [
        "рулетка", "дуэль", "котики", "казино", "приз", "магазин",
        "профиль", "статистика", "перевод", "топ", "семья", "квесты",
        "турнир", "помощь", "start", "help"
    ]
    
    # Если сообщение содержит что-то из списка - игнорируем
    for cmd in known_commands:
        if cmd in text:
            return
    
    await message.answer(
        "❓ Я не понял вашу команду.\n\n"
        "Введите /help для списка команд или /start для главного меню.",
        parse_mode="HTML",
        reply_markup=get_main_keyboard()
    )

# ============================================================
# 📱 ОБРАБОТКА КНОПОК ГЛАВНОГО МЕНЮ
# ============================================================
@router.message(F.text.contains("Рулетка") | F.text.contains("🎰"))
async def btn_roulette(m: Message): 
    await cmd_roulette(m)

@router.message(F.text.contains("Дуэль") | F.text.contains("🤠"))
async def btn_duel(m: Message): 
    await m.answer("🤠 Напишите в группе: <code>дуэль 100</code>", parse_mode="HTML")

@router.message(F.text.contains("Котики") | F.text.contains("🐱"))
async def btn_cats(m: Message): 
    await cmd_cats(m)

@router.message(F.text.contains("Казино") | F.text.contains("🎰"))
async def btn_casino(m: Message): 
    await cmd_casino(m)

@router.message(F.text.contains("Приз") | F.text.contains("🎁"))
async def btn_prize(m: Message): 
    await cmd_prize(m)

@router.message(F.text.contains("Магазин") | F.text.contains("🏪"))
async def btn_shop(m: Message): 
    await cmd_shop(m)

@router.message(F.text.contains("Профиль") | F.text.contains("👤"))
async def btn_profile(m: Message): 
    await cmd_profile(m)

@router.message(F.text.contains("Статистика") | F.text.contains("📊"))
async def btn_stats(m: Message): 
    await cmd_stats(m)

@router.message(F.text.contains("Перевод") | F.text.contains("⭐"))
async def btn_transfer(m: Message): 
    await m.answer("❌ Формат: <code>перевод @username 10</code>", parse_mode="HTML")

@router.message(F.text.contains("Топ") | F.text.contains("🏆"))
async def btn_top(m: Message): 
    await cmd_top(m)

@router.message(F.text.contains("Семья") | F.text.contains("💍"))
async def btn_fam(m: Message): 
    await cmd_family(m)

@router.message(F.text.contains("Квесты") | F.text.contains("🎯"))
async def btn_q(m: Message): 
    await cmd_quests(m)

@router.message(F.text.contains("Турнир") | F.text.contains("🏆"))
async def btn_t(m: Message): 
    await cmd_tournament(m)

@router.message(F.text.contains("Помощь") | F.text.contains("❓"))
async def btn_help(m: Message): 
    await cmd_help(m)

# ============================================================
# 📱 ДОПОЛНИТЕЛЬНЫЕ ОБРАБОТЧИКИ (ПРЯМОЕ СОВПАДЕНИЕ)
# ============================================================
@router.message(F.text == "🎰 Рулетка")
async def btn_roulette_direct(m: Message): 
    await cmd_roulette(m)

@router.message(F.text == "🤠 Дуэль")
async def btn_duel_direct(m: Message): 
    await m.answer("🤠 Напишите в группе: <code>дуэль 100</code>", parse_mode="HTML")

@router.message(F.text == "🐱 Котики")
async def btn_cats_direct(m: Message): 
    await cmd_cats(m)

@router.message(F.text == "🎰 Казино")
async def btn_casino_direct(m: Message): 
    await cmd_casino(m)

@router.message(F.text == "🎁 Приз")
async def btn_prize_direct(m: Message): 
    await cmd_prize(m)

@router.message(F.text == "🏪 Магазин")
async def btn_shop_direct(m: Message): 
    await cmd_shop(m)

@router.message(F.text == "👤 Профиль")
async def btn_profile_direct(m: Message): 
    await cmd_profile(m)

@router.message(F.text == "📊 Статистика")
async def btn_stats_direct(m: Message): 
    await cmd_stats(m)

@router.message(F.text == "⭐ Перевод")
async def btn_transfer_direct(m: Message): 
    await m.answer("❌ Формат: <code>перевод @username 10</code>", parse_mode="HTML")

@router.message(F.text == "🏆 Топ")
async def btn_top_direct(m: Message): 
    await cmd_top(m)

@router.message(F.text == "💍 Семья")
async def btn_fam_direct(m: Message): 
    await cmd_family(m)

@router.message(F.text == "🎯 Квесты")
async def btn_q_direct(m: Message): 
    await cmd_quests(m)

@router.message(F.text == "🏆 Турнир")
async def btn_t_direct(m: Message): 
    await cmd_tournament(m)

@router.message(F.text == "❓ Помощь")
async def btn_help_direct(m: Message): 
    await cmd_help(m)

# ---------------------------------------------------------
# 🎰 РУЛЕТКА
# ---------------------------------------------------------
@router.message(F.text.lower().startswith(("рулетка", "р ", "🎰 рулетка")))
async def cmd_roulette(message: Message):
    chat_id = message.chat.id
    args = message.text.split()
    if len(args) < 3:
        await message.answer("🎰 Формат: <code>р красное 100</code>, <code>р 0 50</code>, <code>р чет 200</code>", parse_mode="HTML")
        return

    bet_type = args[1].lower()
    try: bet_amount = int(args[2])
    except ValueError: 
        await message.answer("❌ Ставка должна быть числом!", parse_mode="HTML")
        return

    if bet_amount < 50:
        await message.answer("❌ Мин. ставка: 50💰!", parse_mode="HTML")
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
        await message.answer("❌ Лимит 10 ставок в раунде!", parse_mode="HTML")
        return

    uname = user['custom_nick'] or user['username'] or f"Игрок_{user['user_id']}"
    bet_idx = len(roul["bets"])
    roul["bets"].append({"user_id": user['user_id'], "username": uname, "type": bet_type, "amount": bet_amount, "insurance": user['insurance']})
    
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="❌ Отменить ставку", callback_data=f"cancel_roulette_{chat_id}_{bet_idx}")
    ]])
    
    await message.answer(
        f"✅ Ставка {uname} — {bet_amount}💰 на <b>{bet_type}</b>! ({len(roul['bets'])}/10)\n⏳ Таймер: 60с",
        parse_mode="HTML",
        reply_markup=kb
    )

@router.callback_query(F.data.startswith("cancel_roulette_"))
async def cancel_roulette_bet(callback: CallbackQuery):
    await callback.answer()
    parts = callback.data.split("_")
    if len(parts) < 4:
        await callback.answer("Ошибка!", show_alert=True)
        return
    
    chat_id = int(parts[2])
    bet_idx = int(parts[3])
    
    if chat_id not in active_roulettes:
        await callback.answer("Ставка уже обработана!", show_alert=True)
        return
    
    roul = active_roulettes[chat_id]
    if bet_idx >= len(roul["bets"]):
        await callback.answer("Ставка уже обработана!", show_alert=True)
        return
    
    bet = roul["bets"][bet_idx]
    if bet["user_id"] != callback.from_user.id:
        await callback.answer("Это не ваша ставка!", show_alert=True)
        return
    
    async with db_pool.acquire() as conn:
        await conn.execute("UPDATE users SET coins = coins + $1 WHERE user_id = $2", bet["amount"], callback.from_user.id)
    
    roul["bets"].pop(bet_idx)
    await callback.message.edit_text(f"❌ Ставка отменена! Возвращено {bet['amount']}💰", parse_mode="HTML")

async def run_roulette_timer(chat_id: int):
    await asyncio.sleep(60)
    if chat_id not in active_roulettes: return
    roul = active_roulettes.pop(chat_id)
    bets = roul["bets"]
    if not bets: return

    winning_number = random.randint(0, 30)
    is_zero = (winning_number == 0)
    is_even = (winning_number % 2 == 0) if not is_zero else None
    is_red = (winning_number % 2 != 0) if not is_zero else None

    color_str = "🟢 0" if is_zero else ("🔴 " + str(winning_number) if is_red else "⚫ " + str(winning_number))
    res_text = f"🎰 <b>РУЛЕТКА! Выпало: {color_str}</b>\n\n"

    async with db_pool.acquire() as conn:
        for b in bets:
            uid, uname, b_type, amount = b["user_id"], b["username"], b["type"], b["amount"]
            won, mult = False, 0
            if b_type in ["красное", "red"] and is_red: won, mult = True, 2
            elif b_type in ["черное", "чёрное", "black"] and (not is_red and not is_zero): won, mult = True, 2
            elif b_type in ["чет", "чёт", "even"] and is_even: won, mult = True, 2
            elif b_type in ["нечет", "нечёт", "odd"] and (not is_even and not is_zero): won, mult = True, 2
            elif b_type == "0" and is_zero: won, mult = True, 100
            elif b_type.isdigit() and int(b_type) == winning_number: won, mult = True, 50

            if won:
                win_coins = amount * mult
                net_win = win_coins - amount
                await conn.execute("""UPDATE users SET coins = coins + $1, wins = wins + 1, total_games = total_games + 1,
                    roulette_games = roulette_games + 1, roulette_wins = roulette_wins + 1, total_coins_won = total_coins_won + $1,
                    daily_net_win = daily_net_win + $3 WHERE user_id = $2""", win_coins, uid, net_win)
                res_text += f"✅ {uname}: +{win_coins}💰\n"
            else:
                refund = (amount // 2) if b["insurance"] else 0
                if refund > 0:
                    await conn.execute("UPDATE users SET coins = coins + $1, insurance = FALSE WHERE user_id = $2", refund, uid)
                await conn.execute("UPDATE users SET losses = losses + 1, total_games = total_games + 1, roulette_games = roulette_games + 1 WHERE user_id = $1", uid)
                res_text += f"❌ {uname}: -{amount}💰 {'(страховка)' if refund else ''}\n"
            
            await check_achievements(uid)
            await check_quests(uid)

    await bot.send_message(chat_id, res_text, parse_mode="HTML")

# ---------------------------------------------------------
# 🤠 ДУЭЛЬ
# ---------------------------------------------------------
@router.message(F.text.lower().startswith(("дуэль", "🤠 дуэль")))
async def cmd_duel(message: Message):
    if message.chat.type == ChatType.PRIVATE:
        await message.answer("⚠️ Дуэли только в группах!", parse_mode="HTML")
        return
    args = message.text.split()
    if len(args) < 2: 
        await message.answer("🤠 Формат: <code>дуэль 100</code>", parse_mode="HTML")
        return
    try: amount = int(args[1])
    except ValueError: 
        await message.answer("❌ Ставка должна быть числом!", parse_mode="HTML")
        return
    if amount < 10:
        await message.answer("❌ Мин. ставка: 10💰!", parse_mode="HTML")
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
        "pending": True,
        "created_by": message.from_user.id,
        "chat_id": message.chat.id
    }

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=f"🎯 Принять дуэль ({amount}💰)", callback_data=f"accept_duel_{duel_id}"),
            InlineKeyboardButton(text="❌ Отмена", callback_data=f"cancel_duel_{duel_id}")
        ]
    ])
    await message.answer(f"🤠 <b>{uname} вызывает на дуэль!</b>\nСтавка: {amount}💰\n⏳ Таймер: 60с", reply_markup=kb, parse_mode="HTML")

@router.callback_query(F.data.startswith("cancel_duel_"))
async def cancel_duel(callback: CallbackQuery):
    await callback.answer()
    duel_id = callback.data.replace("cancel_duel_", "")
    if duel_id not in active_duels:
        await callback.answer("Дуэль уже завершена!", show_alert=True)
        return
    
    duel = active_duels[duel_id]
    if callback.from_user.id != duel["created_by"]:
        await callback.answer("Отменить может только создатель!", show_alert=True)
        return
    
    async with db_pool.acquire() as conn:
        await conn.execute("UPDATE users SET coins = coins + $1 WHERE user_id = $2", duel["amount"], duel["p1"])
    
    del active_duels[duel_id]
    await callback.message.edit_text("❌ Дуэль отменена! Ставка возвращена.", parse_mode="HTML")

@router.callback_query(F.data.startswith("accept_duel_"))
async def process_accept_duel(callback: CallbackQuery):
    await callback.answer()
    duel_id = callback.data.replace("accept_duel_", "")
    if duel_id not in active_duels: 
        await callback.answer("Дуэль уже завершена!", show_alert=True)
        return
    duel = active_duels[duel_id]
    if not duel["pending"]: 
        await callback.answer("Дуэль уже началась!", show_alert=True)
        return
    if duel["p1"] == callback.from_user.id:
        await callback.answer("Нельзя с самим собой!", show_alert=True)
        return

    p2_user = await get_or_create_user(callback.from_user.id, callback.from_user.username)
    if p2_user['coins'] < duel["amount"]:
        await callback.answer("Недостаточно монет!", show_alert=True)
        return

    async with db_pool.acquire() as conn:
        await conn.execute("UPDATE users SET coins = coins - $1 WHERE user_id = $2", duel["amount"], p2_user['user_id'])

    p2_name = p2_user['custom_nick'] or p2_user['username'] or callback.from_user.first_name
    duel["pending"] = False
    duel["p2"] = p2_user['user_id']
    duel["p2_name"] = p2_name
    duel["turn"] = duel["p1"]
    duel["p1_hp"] = 100
    duel["p2_hp"] = 100

    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="💥 ВЫСТРЕЛ!", callback_data=f"shoot_{duel_id}")]])
    await callback.message.edit_text(f"🤠 <b>ДУЭЛЬ НАЧАЛАСЬ!</b>\n{duel['p1_name']} (100 HP) vs {p2_name} (100 HP)\n\n👉 Ход: {duel['p1_name']}", reply_markup=kb, parse_mode="HTML")

@router.callback_query(F.data.startswith("shoot_"))
async def process_shoot(callback: CallbackQuery):
    await callback.answer()
    duel_id = callback.data.replace("shoot_", "")
    if duel_id not in active_duels: 
        await callback.answer("Дуэль уже завершена!", show_alert=True)
        return
    duel = active_duels[duel_id]
    if callback.from_user.id != duel["turn"]:
        await callback.answer("Сейчас не ваш ход!", show_alert=True)
        return

    is_p1 = (callback.from_user.id == duel["p1"])
    target_hp_key = "p2_hp" if is_p1 else "p1_hp"
    shooter_name = duel["p1_name"] if is_p1 else duel["p2_name"]
    target_name = duel["p2_name"] if is_p1 else duel["p1_name"]
    target_id = duel["p2"] if is_p1 else duel["p1"]

    dmg = random.randint(25, 55)
    duel[target_hp_key] = max(0, duel[target_hp_key] - dmg)

    if duel[target_hp_key] <= 0:
        winner_id = callback.from_user.id
        loser_id = target_id
        tot_pot = duel["amount"] * 2
        win_amt = int(tot_pot * 0.9)

        async with db_pool.acquire() as conn:
            net_win = win_amt - duel["amount"]
            await conn.execute("UPDATE users SET coins = coins - $1, losses = losses + 1, duel_games = duel_games + 1 WHERE user_id = $2", duel["amount"], loser_id)
            await conn.execute("UPDATE users SET coins = coins + $1, wins = wins + 1, duel_games = duel_games + 1, duel_wins = duel_wins + 1, tournament_score = tournament_score + 1, daily_net_win = daily_net_win + $3 WHERE user_id = $2", win_amt, winner_id, net_win)

        await check_achievements(winner_id)
        await check_quests(winner_id)
        del active_duels[duel_id]
        await callback.message.edit_text(f"💀 <b>{shooter_name} побеждает {target_name}!</b>\n🏆 Приз: +{win_amt}💰\n+1 очко в турнир!", parse_mode="HTML")

# ---------------------------------------------------------
# 🐱 КОТИКИ
# ---------------------------------------------------------
@router.message(F.text.lower().startswith(("котики", "🐱 котики")))
async def cmd_cats(message: Message):
    if message.chat.type == ChatType.PRIVATE:
        await message.answer("⚠️ Игра в котиков только в группах!", parse_mode="HTML")
        return
    
    args = message.text.split()
    if len(args) < 2 or not args[1].isdigit():
        await message.answer("🐱 Формат: <code>котики 100</code>", parse_mode="HTML")
        return
    bet = int(args[1])
    if bet < 10:
        await message.answer("❌ Мин. ставка: 10💰!", parse_mode="HTML")
        return

    user = await get_or_create_user(message.from_user.id, message.from_user.username)
    if user['coins'] < bet:
        await message.answer("❌ Недостаточно монет!", parse_mode="HTML")
        return

    chat_id = message.chat.id
    if chat_id in active_cats and active_cats[chat_id]["active"]:
        await message.answer("⚠️ В этом чате уже идёт игра в котиков! Дождитесь окончания.", parse_mode="HTML")
        return

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🟢 Играть одному (15 сек)", callback_data=f"cats_mode_solo_{chat_id}_{bet}")],
        [InlineKeyboardButton(text="🟡 Играть с пользователем", callback_data=f"cats_mode_multi_{chat_id}_{bet}")]
    ])
    
    await message.answer(
        f"🐱 <b>ВЫБЕРИТЕ РЕЖИМ ИГРЫ</b>\n\n"
        f"💰 Ставка: {bet}💰\n\n"
        f"🟢 <b>Играть одному</b> — 15 секунд, 3 попытки\n"
        f"🟡 <b>Играть с пользователем</b> — кто первый напишет правильный ответ",
        parse_mode="HTML",
        reply_markup=kb
    )

@router.callback_query(F.data.startswith("cats_mode_solo_"))
async def cats_mode_solo(callback: CallbackQuery):
    await callback.answer()
    parts = callback.data.split("_")
    chat_id = int(parts[3])
    bet = int(parts[4])
    
    if chat_id in active_cats and active_cats[chat_id]["active"]:
        await callback.answer("Игра уже началась!", show_alert=True)
        return
    
    user = await get_or_create_user(callback.from_user.id, callback.from_user.username)
    if user['coins'] < bet:
        await callback.answer("❌ Недостаточно монет!", show_alert=True)
        await callback.message.edit_text("❌ Недостаточно монет для игры!", parse_mode="HTML")
        return
    
    await callback.message.delete()
    await start_cats_game(callback.message, chat_id, bet, user, "solo")

@router.callback_query(F.data.startswith("cats_mode_multi_"))
async def cats_mode_multi(callback: CallbackQuery):
    await callback.answer()
    parts = callback.data.split("_")
    chat_id = int(parts[3])
    bet = int(parts[4])
    
    if chat_id in active_cats and active_cats[chat_id]["active"]:
        await callback.answer("Игра уже началась!", show_alert=True)
        return
    
    user = await get_or_create_user(callback.from_user.id, callback.from_user.username)
    if user['coins'] < bet:
        await callback.answer("❌ Недостаточно монет!", show_alert=True)
        await callback.message.edit_text("❌ Недостаточно монет для игры!", parse_mode="HTML")
        return
    
    await callback.message.delete()
    await start_cats_game(callback.message, chat_id, bet, user, "multi")

async def start_cats_game(message: Message, chat_id: int, bet: int, user: dict, mode: str):
    yellow = random.randint(1, 40)
    blacks = random.randint(0, 15)
    cats = ["🐈"] * yellow + ["🐈‍⬛"] * blacks
    random.shuffle(cats)

    async with db_pool.acquire() as conn:
        await conn.execute("UPDATE users SET coins = coins - $1 WHERE user_id = $2", bet, user['user_id'])

    active_cats[chat_id] = {
        "count": yellow,
        "pot": bet,
        "host_id": user['user_id'],
        "host_name": user['custom_nick'] or user['username'] or message.from_user.first_name,
        "attempts": {},
        "active": True,
        "timer_task": None,
        "start_time": datetime.datetime.utcnow(),
        "mode": mode,
        "winner_id": None,
        "answered": set()
    }

    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="❌ Отменить игру (возврат)", callback_data=f"cancel_cats_{chat_id}")
    ]])

    cats_display = "".join(cats[:40])
    
    if mode == "solo":
        text = f"""🐱 <b>Считай жёлтых 🐈</b>
(чёрные 🐈‍⬛ НЕ считаем!)

Ставка: {bet}💰
Режим: 🟢 Один игрок

{cats_display}

⏳ У вас есть 15 секунд!
📝 Введите число (количество жёлтых котиков)
🎯 У вас 3 попытки!"""
    else:
        text = f"""🐱 <b>Считай жёлтых 🐈</b>
(чёрные 🐈‍⬛ НЕ считаем!)

Ставка: {bet}💰
Режим: 🟡 Игра с пользователем

{cats_display}

👥 Кто первый напишет правильный ответ — тот и выиграл!
📝 Введите число (количество жёлтых котиков)"""

    await message.answer(text, parse_mode="HTML", reply_markup=kb)

    if mode == "solo":
        game = active_cats[chat_id]
        game["timer_task"] = asyncio.create_task(cats_timer(chat_id))

async def cats_timer(chat_id: int):
    await asyncio.sleep(15)
    if chat_id not in active_cats: return
    game = active_cats[chat_id]
    if not game["active"]: return
    if game.get("winner_id"): return
    
    game["active"] = False
    async with db_pool.acquire() as conn:
        await conn.execute("UPDATE users SET coins = coins + $1 WHERE user_id = $2", game["pot"], game["host_id"])
    
    await bot.send_message(
        chat_id,
        f"⏰ <b>ВРЕМЯ ВЫШЛО!</b>\n\n"
        f"Правильный ответ: {game['count']} жёлтых котиков 🐈\n"
        f"Ставка возвращена создателю игры.",
        parse_mode="HTML"
    )
    if chat_id in active_cats:
        del active_cats[chat_id]

@router.callback_query(F.data.startswith("cancel_cats_"))
async def cancel_cats(callback: CallbackQuery):
    await callback.answer()
    chat_id = int(callback.data.replace("cancel_cats_", ""))
    if chat_id not in active_cats:
        await callback.answer("Игра уже завершена!", show_alert=True)
        return
    game = active_cats[chat_id]
    if not game["active"]:
        await callback.answer("Игра уже завершена!", show_alert=True)
        return
    if callback.from_user.id != game["host_id"]:
        await callback.answer("Отменить может только хост!", show_alert=True)
        return
    if game["timer_task"]:
        game["timer_task"].cancel()
    game["active"] = False
    async with db_pool.acquire() as conn:
        await conn.execute("UPDATE users SET coins = coins + $1 WHERE user_id = $2", game["pot"], game["host_id"])
    await callback.message.edit_text("❌ Игра отменена! Ставка возвращена.", parse_mode="HTML")
    del active_cats[chat_id]

@router.message(F.text.isdigit())
async def process_cats_answer(message: Message):
    chat_id = message.chat.id
    if chat_id not in active_cats: return
    game = active_cats[chat_id]
    if not game["active"]: return
    uid = message.from_user.id
    if game.get("winner_id"):
        await message.answer("⏳ Игра уже завершена!", parse_mode="HTML")
        return
    
    if game["mode"] == "solo":
        if game["attempts"].get(uid, 0) >= 3:
            await message.answer("❌ У вас больше нет попыток!", parse_mode="HTML")
            return
        
        game["attempts"][uid] = game["attempts"].get(uid, 0) + 1
        attempts_left = 3 - game["attempts"][uid]
        val = int(message.text)
        
        if val == game["count"]:
            game["active"] = False
            game["winner_id"] = uid
            if game["timer_task"]:
                game["timer_task"].cancel()
            win_pot = int(game["pot"] * 1.8)
            net_win = win_pot - game["pot"]
            async with db_pool.acquire() as conn:
                await conn.execute("""UPDATE users SET 
                    coins = coins + $1, wins = wins + 1, total_games = total_games + 1, 
                    cat_games = cat_games + 1, cat_wins = cat_wins + 1, 
                    tournament_score = tournament_score + 1, daily_net_win = daily_net_win + $3 
                    WHERE user_id = $2""", win_pot, uid, net_win)
            await check_achievements(uid)
            await check_quests(uid)
            await message.answer(
                f"🎉 <b>ПРАВИЛЬНО, {message.from_user.first_name}!</b>\n\n"
                f"Жёлтых котиков было: {game['count']} 🐈\n"
                f"🏆 Выигрыш: +{win_pot}💰\n"
                f"+1 очко в турнир!\n"
                f"⏱️ Время: {(datetime.datetime.utcnow() - game['start_time']).seconds} сек",
                parse_mode="HTML"
            )
            del active_cats[chat_id]
        else:
            if attempts_left == 0:
                await message.answer(
                    f"❌ <b>НЕПРАВИЛЬНО!</b>\n\n"
                    f"У вас закончились попытки!\n"
                    f"Правильный ответ: {game['count']} жёлтых котиков 🐈",
                    parse_mode="HTML"
                )
                game["active"] = False
                if game["timer_task"]:
                    game["timer_task"].cancel()
                async with db_pool.acquire() as conn:
                    await conn.execute("UPDATE users SET coins = coins + $1 WHERE user_id = $2", game["pot"], game["host_id"])
                del active_cats[chat_id]
            else:
                await message.answer(
                    f"❌ <b>НЕПРАВИЛЬНО!</b>\n\n"
                    f"Осталось попыток: {attempts_left}\n"
                    f"Попробуйте ещё раз!",
                    parse_mode="HTML"
                )
    
    else:
        if uid in game["answered"]:
            await message.answer("⏳ Вы уже отвечали! Ждите других игроков.", parse_mode="HTML")
            return
        val = int(message.text)
        game["answered"].add(uid)
        if val == game["count"]:
            game["active"] = False
            game["winner_id"] = uid
            win_pot = int(game["pot"] * 1.8)
            net_win = win_pot - game["pot"]
            async with db_pool.acquire() as conn:
                await conn.execute("""UPDATE users SET 
                    coins = coins + $1, wins = wins + 1, total_games = total_games + 1, 
                    cat_games = cat_games + 1, cat_wins = cat_wins + 1, 
                    tournament_score = tournament_score + 1, daily_net_win = daily_net_win + $3 
                    WHERE user_id = $2""", win_pot, uid, net_win)
            await check_achievements(uid)
            await check_quests(uid)
            await message.answer(
                f"🎉 <b>ПРАВИЛЬНО, {message.from_user.first_name}!</b>\n\n"
                f"Жёлтых котиков было: {game['count']} 🐈\n"
                f"🏆 Выигрыш: +{win_pot}💰\n"
                f"+1 очко в турнир!\n"
                f"⏱️ Время: {(datetime.datetime.utcnow() - game['start_time']).seconds} сек",
                parse_mode="HTML"
            )
            del active_cats[chat_id]
        else:
            await message.answer(
                f"❌ <b>НЕПРАВИЛЬНО, {message.from_user.first_name}!</b>\n\n"
                f"Попробуйте ещё раз!",
                parse_mode="HTML"
            )

# ---------------------------------------------------------
# 🎰 КАЗИНО
# ---------------------------------------------------------
@router.message(F.text.lower().in_(["казино", "🎰 казино"]))
async def cmd_casino(message: Message):
    user = await get_or_create_user(message.from_user.id, message.from_user.username)
    if user['stars'] < 25:
        await message.answer("❌ Требуется 25⭐!", parse_mode="HTML")
        return
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, крутить!", callback_data="casino_yes")],
        [InlineKeyboardButton(text="❌ Нет, отмена", callback_data="casino_no")]
    ])
    await message.answer("🎰 <b>Платная рулетка казино!</b>\nСтоимость: 25⭐\nВы уверены, что хотите сыграть?", parse_mode="HTML", reply_markup=kb)

@router.callback_query(F.data.startswith("casino_"))
async def process_casino(callback: CallbackQuery):
    await callback.answer()
    if callback.data == "casino_no":
        await callback.message.edit_text("❌ Отменено!", parse_mode="HTML")
        return
    
    user = await get_or_create_user(callback.from_user.id, callback.from_user.username)
    if user['stars'] < 25:
        await callback.message.edit_text("❌ Недостаточно звёзд!", parse_mode="HTML")
        return
    
    rand = random.random()
    async with db_pool.acquire() as conn:
        await conn.execute("UPDATE users SET stars = stars - 25, casino_games = casino_games + 1 WHERE user_id = $1", user['user_id'])
        if rand < 0.40:
            c = random.randint(50, 3000)
            await conn.execute("UPDATE users SET coins = coins + $1, wins = wins + 1, casino_wins = casino_wins + 1 WHERE user_id = $2", c, user['user_id'])
            res = f"💰 Выиграно <b>+{c} монет</b>!"
        elif rand < 0.70:
            d = random.randint(4, 10)
            vip_until = datetime.datetime.utcnow() + datetime.timedelta(days=d)
            await conn.execute("UPDATE users SET is_vip = TRUE, vip_until = $1, wins = wins + 1, casino_wins = casino_wins + 1 WHERE user_id = $2", vip_until, user['user_id'])
            res = f"👑 Выигран <b>VIP статус на {d} дней</b>!"
        elif rand < 0.99:
            s = random.randint(10, 75)
            await conn.execute("UPDATE users SET stars = stars + $1, wins = wins + 1, casino_wins = casino_wins + 1 WHERE user_id = $2", s, user['user_id'])
            res = f"⭐ Выиграно <b>+{s} звёзд</b>!"
        else:
            vip_until = datetime.datetime.utcnow() + datetime.timedelta(days=30)
            await conn.execute("UPDATE users SET coins = coins + 5000, stars = stars + 50, is_vip = TRUE, vip_until = $1, wins = wins + 1, casino_wins = casino_wins + 1 WHERE user_id = $2", vip_until, user['user_id'])
            res = "🔥 <b>ДЖЕКПОТ (1%)!</b> 5000💰 + 50⭐ + VIP на 30 дней!"
        await check_achievements(user['user_id'])
    await callback.message.edit_text(f"🎰 <b>КАЗИНО (-25⭐):</b>\n\n{res}", parse_mode="HTML")

# ---------------------------------------------------------
# 🎁 ПРИЗ
# ---------------------------------------------------------
@router.message(F.text.lower().in_(["приз", "🎁 приз"]))
async def cmd_prize(message: Message):
    if message.chat.type != ChatType.PRIVATE:
        await message.answer("⚠️ Приз доступен только в ЛС с ботом!", parse_mode="HTML")
        return
    user = await get_or_create_user(message.from_user.id, message.from_user.username)
    today = str(datetime.date.today())
    
    if user.get('last_prize_date') and str(user['last_prize_date']) == today:
        await message.answer("⏳ Вы уже получали приз сегодня!", parse_mode="HTML")
        return
    
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=f"📦 Сундук #{i}", callback_data=f"chest_{i}")] for i in range(1, 6)])
    await message.answer("🎁 <b>Выберите сундук:</b>", reply_markup=kb, parse_mode="HTML")

@router.callback_query(F.data.startswith("chest_"))
async def process_chest(callback: CallbackQuery):
    await callback.answer()
    user = await get_or_create_user(callback.from_user.id, callback.from_user.username)
    today = str(datetime.date.today())
    
    if user.get('last_prize_date') and str(user['last_prize_date']) == today:
        await callback.answer("⏳ Вы уже получали приз сегодня!", show_alert=True)
        return
    
    r = random.random()
    if r < 0.30: c, s = 200, 0
    elif r < 0.55: c, s = 500, 0
    elif r < 0.75: c, s = 800, 0
    elif r < 0.90: c, s = 1000, 1
    else: c, s = 1500, 5

    async with db_pool.acquire() as conn:
        await conn.execute(
            "UPDATE users SET coins = coins + $1, stars = stars + $2, last_prize_date = $3 WHERE user_id = $4", 
            c, s, today, user['user_id']
        )
    await callback.message.edit_text(f"🎉 <b>Награда получена:</b> +{c}💰 +{s}⭐", parse_mode="HTML")

# ---------------------------------------------------------
# 🏪 МАГАЗИН
# ---------------------------------------------------------
@router.message(F.text.lower().in_(["магазин", "🏪 магазин"]))
async def cmd_shop(message: Message):
    text = """🏪 <b>МАГАЗИН ТОВАРОВ</b>

👑 VIP 1 день — 5⭐
👑 VIP 7 дней — 30⭐
🛡️ Страховка — 5⭐ (возврат 50% при проигрыше)
💱 Обмен — 1⭐ → 50💰
✏️ Сменить ник — <code>сменить_ник @ник</code> (10⭐)
🔒 Скрыть профиль — <code>скрыть_профиль</code> (1000⭐)
🔓 Открыть профиль — <code>открыть_профиль</code>"""
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👑 VIP 1д (5⭐)", callback_data="buy_vip_1"), 
         InlineKeyboardButton(text="👑 VIP 7д (30⭐)", callback_data="buy_vip_7")],
        [InlineKeyboardButton(text="🛡️ Страховка (5⭐)", callback_data="buy_ins"), 
         InlineKeyboardButton(text="💱 1⭐→50💰", callback_data="buy_ex")]
    ])
    await message.answer(text, parse_mode="HTML", reply_markup=kb)

@router.callback_query(F.data.startswith("buy_"))
async def process_shop(callback: CallbackQuery):
    await callback.answer()
    user = await get_or_create_user(callback.from_user.id, callback.from_user.username)
    async with db_pool.acquire() as conn:
        if callback.data == "buy_vip_1" and user['stars'] >= 5:
            vip_until = datetime.datetime.utcnow() + datetime.timedelta(days=1)
            await conn.execute("UPDATE users SET stars = stars - 5, is_vip = TRUE, vip_until = $1 WHERE user_id = $2", vip_until, user['user_id'])
            await callback.message.answer("✅ Куплен VIP на 1 день!", parse_mode="HTML")
        elif callback.data == "buy_vip_7" and user['stars'] >= 30:
            vip_until = datetime.datetime.utcnow() + datetime.timedelta(days=7)
            await conn.execute("UPDATE users SET stars = stars - 30, is_vip = TRUE, vip_until = $1 WHERE user_id = $2", vip_until, user['user_id'])
            await callback.message.answer("✅ Куплен VIP на 7 дней!", parse_mode="HTML")
        elif callback.data == "buy_ins" and user['stars'] >= 5:
            await conn.execute("UPDATE users SET stars = stars - 5, insurance = TRUE WHERE user_id = $1", user['user_id'])
            await callback.message.answer("✅ Страховка куплена!", parse_mode="HTML")
        elif callback.data == "buy_ex" and user['stars'] >= 1:
            await conn.execute("UPDATE users SET stars = stars - 1, coins = coins + 50 WHERE user_id = $1", user['user_id'])
            await callback.message.answer("✅ Обменено 1⭐ на 50💰!", parse_mode="HTML")
        else:
            await callback.message.answer("❌ Недостаточно звёзд!", parse_mode="HTML")

# ---------------------------------------------------------
# ⭐ ПЕРЕВОДЫ
# ---------------------------------------------------------
@router.message(F.text.lower().startswith("перевод"))
async def cmd_transfer(message: Message):
    args = message.text.split()
    if len(args) < 3:
        await message.answer("❌ Формат: <code>перевод @username 10</code>", parse_mode="HTML")
        return
    target_uname = args[1].replace("@", "")
    try: amount = int(args[2])
    except ValueError: 
        await message.answer("❌ Сумма должна быть числом!", parse_mode="HTML")
        return
    if amount <= 0:
        await message.answer("❌ Мин. сумма 1⭐!", parse_mode="HTML")
        return

    sender = await get_or_create_user(message.from_user.id, message.from_user.username)
    today = datetime.date.today()
    transferred = sender['daily_stars_transferred'] if sender['last_transfer_date'] == today else 0

    if transferred + amount > 25:
        await message.answer(f"❌ Лимит переводов 25⭐ в день! Переведено сегодня: {transferred}⭐", parse_mode="HTML")
        return
    if sender['stars'] < amount:
        await message.answer("❌ Недостаточно звёзд!", parse_mode="HTML")
        return

    async with db_pool.acquire() as conn:
        target = await conn.fetchrow("SELECT user_id FROM users WHERE username = $1", target_uname)
        if not target:
            await message.answer("❌ Пользователь не найден!", parse_mode="HTML")
            return
        await conn.execute("UPDATE users SET stars = stars - $1, daily_stars_transferred = $2, last_transfer_date = $3 WHERE user_id = $4", amount, transferred + amount, today, sender['user_id'])
        await conn.execute("UPDATE users SET stars = stars + $1 WHERE user_id = $2", amount, target['user_id'])
    await message.answer(f"✅ Переведено +{amount}⭐ для @{target_uname}!", parse_mode="HTML")

# ---------------------------------------------------------
# ✏️ СМЕНА НИКА
# ---------------------------------------------------------
@router.message(F.text.lower().startswith("сменить_ник"))
async def cmd_change_nick(message: Message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer("❌ Формат: <code>сменить_ник @новый_ник</code>", parse_mode="HTML")
        return

    new_nick = args[1].strip()
    if new_nick.startswith("@"):
        new_nick = new_nick[1:]

    user = await get_or_create_user(message.from_user.id, message.from_user.username)
    if user['stars'] < 10:
        await message.answer("❌ Нужна 10⭐!", parse_mode="HTML")
        return

    async with db_pool.acquire() as conn:
        await conn.execute("UPDATE users SET stars = stars - 10, custom_nick = $1 WHERE user_id = $2", new_nick, message.from_user.id)

    await message.answer(f"✅ Ваш кастомный ник изменён на: <b>{new_nick}</b>", parse_mode="HTML")

# ---------------------------------------------------------
# 🔒 СКРЫТИЕ/ОТКРЫТИЕ ПРОФИЛЯ
# ---------------------------------------------------------
@router.message(F.text.lower() == "скрыть_профиль")
async def cmd_hide_profile(message: Message):
    user = await get_or_create_user(message.from_user.id, message.from_user.username)
    if user['stars'] < 1000:
        await message.answer("❌ Нужно 1000⭐!", parse_mode="HTML")
        return

    async with db_pool.acquire() as conn:
        await conn.execute("UPDATE users SET stars = stars - 1000, is_hidden = TRUE WHERE user_id = $1", message.from_user.id)

    await message.answer("🔒 Ваш профиль скрыт от других игроков!", parse_mode="HTML")

@router.message(F.text.lower() == "открыть_профиль")
async def cmd_unhide_profile(message: Message):
    async with db_pool.acquire() as conn:
        await conn.execute("UPDATE users SET is_hidden = FALSE WHERE user_id = $1", message.from_user.id)
    await message.answer("🔓 Ваш профиль открыт!", parse_mode="HTML")

# ---------------------------------------------------------
# 👤 ПРОФИЛЬ
# ---------------------------------------------------------
@router.message(F.text.lower().startswith(("профиль", "п ")))
async def cmd_profile(message: Message):
    user = await get_or_create_user(message.from_user.id, message.from_user.username)
    rank = calculate_rank(user['wins'], user['total_games'])
    dname = user['custom_nick'] or user['username'] or f"Игрок_{user['user_id']}"

    family_info = ""
    if user['family_id']:
        async with db_pool.acquire() as conn:
            fam = await conn.fetchrow("SELECT name, score FROM families WHERE id = $1", user['family_id'])
            if fam: 
                fname = fam['name'] if fam['name'] else f"Семья #{user['family_id']}"
                family_info = f"\n💍 Семья: {fname} (Очки: {fam['score']})"

    titles = await get_user_titles(user)
    titles_str = ", ".join(titles) if titles else "Нет"
    
    vip_status = "👑 VIP" if user['is_vip'] and (not user['vip_until'] or user['vip_until'] > datetime.datetime.utcnow()) else "Обычный"
    if user['vip_until'] and user['vip_until'] > datetime.datetime.utcnow():
        days_left = (user['vip_until'] - datetime.datetime.utcnow()).days
        vip_status += f" (осталось {days_left}д)"

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

    text = f"""👤 <b>Профиль:</b> {dname}
🎖️ Ранг: {rank}
💳 Статус: {vip_status}
💰 Монеты: {user['coins']:,}
⭐ Звёзды: {user['stars']}
🎖️ Титулы: {titles_str}{family_info}
━━━━━━━━━━━━━━━━━
📈 Статистика по играм:
{stats_text}
🏆 Лучшая игра: {best_game} ({best_ratio}%)
🏟️ Турнирные очки: {user['tournament_score']}"""

    await message.answer(text, parse_mode="HTML")

# ---------------------------------------------------------
# 📊 СТАТИСТИКА
# ---------------------------------------------------------
@router.message(F.text.lower().in_(["статистика", "стата"]))
async def cmd_stats(message: Message):
    u = await get_or_create_user(message.from_user.id, message.from_user.username)
    text = f"""📊 <b>СТАТИСТИКА</b>
🎰 Рулетка: {u['roulette_wins']}/{u['roulette_games']}
🤠 Дуэль: {u['duel_wins']}/{u['duel_games']}
🐱 Котики: {u['cat_wins']}/{u['cat_games']}
🎰 Казино: {u['casino_wins']}/{u['casino_games']}
🏆 Побед/Всего: {u['wins']}/{u['total_games']}"""
    await message.answer(text, parse_mode="HTML")

# ---------------------------------------------------------
# 🏆 ТОПЫ
# ---------------------------------------------------------
@router.message(F.text.lower().in_(["топ", "🏆 топ"]))
async def cmd_top(message: Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💍 Топ Семей", callback_data="top_families")],
        [InlineKeyboardButton(text="🏟️ Топ Турнира", callback_data="top_tournament")],
        [InlineKeyboardButton(text="📊 Награды за топ", callback_data="top_rewards")]
    ])
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("SELECT custom_nick, username, wins, coins FROM users WHERE COALESCE(is_hidden, 0) = 0 ORDER BY wins DESC LIMIT 10")
    
    if not rows:
        text = "🏆 <b>ТОП ИГРОКОВ:</b>\n\nНет игроков для отображения"
    else:
        text = "🏆 <b>ТОП ИГРОКОВ:</b>\n\n"
        for idx, r in enumerate(rows, 1):
            name = r['custom_nick'] or r['username'] or "Игрок"
            text += f"{idx}. {name} — {r['wins']} побед ({r['coins']}💰)\n"
    await message.answer(text, parse_mode="HTML", reply_markup=kb)

@router.callback_query(F.data == "top_rewards")
async def top_rewards(callback: CallbackQuery):
    await callback.answer()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Назад к топу", callback_data="top_back")]
    ])
    
    text = """🏆 <b>ЕЖЕДНЕВНЫЕ НАГРАДЫ ЗА ТОП</b>

🥇 1 место: +25⭐ +100💰
🥈 2 место: +15⭐ +50💰
🥉 3 место: +10⭐ +25💰

📅 Награды выдаются каждый день в 00:00 по МСК

💍 <b>ТОП СЕМЕЙ:</b>
🥇 1 место: +15⭐ каждому
🥈 2 место: +10⭐ каждому
🥉 3 место: +5⭐ каждому"""
    
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)

@router.callback_query(F.data == "top_back")
async def top_back(callback: CallbackQuery):
    await callback.answer()
    await cmd_top(callback.message)

@router.callback_query(F.data == "top_families")
async def cmd_top_families(callback: CallbackQuery):
    await callback.answer()
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("SELECT id, name, score, created_at FROM families ORDER BY score DESC LIMIT 10")
    
    if not rows:
        text = "💍 <b>ТОП СЕМЕЙ:</b>\n\nНет семей для отображения"
    else:
        text = "💍 <b>ТОП СЕМЕЙ:</b>\n\n"
        for idx, r in enumerate(rows, 1):
            name = r['name'] or f"Семья #{r['id']}"
            text += f"{idx}. {name} — {r['score']} очков (с {r['created_at']})\n"
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Назад к топу", callback_data="top_back")]
    ])
    
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)

@router.callback_query(F.data == "top_tournament")
async def cmd_top_tournament(callback: CallbackQuery):
    await callback.answer()
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT user_id, username, custom_nick, tournament_score 
            FROM users 
            WHERE tournament_fee_paid = TRUE AND tournament_score > 0 
            ORDER BY tournament_score DESC LIMIT 10
        """)
    
    if not rows:
        text = "🏟️ <b>ТОП ТУРНИРА:</b>\n\nНет участников!"
    else:
        text = "🏟️ <b>ТОП ТУРНИРА:</b>\n\n"
        for idx, r in enumerate(rows, 1):
            name = r['custom_nick'] or r['username'] or f"Игрок_{r['user_id']}"
            text += f"{idx}. {name} — {r['tournament_score']} очков\n"
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Назад к топу", callback_data="top_back")]
    ])
    
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)

# ---------------------------------------------------------
# 💍 СЕМЕЙНАЯ СИСТЕМА
# ---------------------------------------------------------
@router.message(F.text.lower().startswith("обручиться"))
async def cmd_marry(message: Message):
    if not message.reply_to_message:
        await message.answer("❌ Ответьте на сообщение партнера!", parse_mode="HTML")
        return
    
    args = message.text.split(maxsplit=1)
    fam_name = args[1].strip() if len(args) > 1 else None
    
    u1 = await get_or_create_user(message.from_user.id, message.from_user.username)
    u2 = await get_or_create_user(message.reply_to_message.from_user.id, message.reply_to_message.from_user.username)
    
    if u1['family_id'] or u2['family_id']:
        await message.answer("❌ Кто-то уже в браке!", parse_mode="HTML")
        return
    
    if u1['divorce_until'] and u1['divorce_until'] > datetime.date.today():
        await message.answer(f"❌ Вы не можете вступать в брак до {u1['divorce_until']}!", parse_mode="HTML")
        return
    
    if u2['divorce_until'] and u2['divorce_until'] > datetime.date.today():
        await message.answer(f"❌ Ваш партнер не может вступать в брак до {u2['divorce_until']}!", parse_mode="HTML")
        return

    marriage_id = f"{u1['user_id']}_{u2['user_id']}"
    pending_marriages[marriage_id] = {
        "u1": u1['user_id'], 
        "u2": u2['user_id'], 
        "fam_name": fam_name or f"Семья {u1['username']} и {u2['username']}"
    }
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="❤️ Принять", callback_data=f"marry_accept_{marriage_id}"),
            InlineKeyboardButton(text="💔 Отклонить", callback_data=f"marry_reject_{marriage_id}")
        ]
    ])
    await message.answer(f"💍 {message.reply_to_message.from_user.first_name}, вам предлагают руку и сердце!\nНазвание: {pending_marriages[marriage_id]['fam_name']}\n⏳ Таймер: 120с", reply_markup=kb, parse_mode="HTML")

@router.callback_query(F.data.startswith("marry_"))
async def process_marry(callback: CallbackQuery):
    await callback.answer()
    parts = callback.data.split("_")
    if len(parts) < 3:
        await callback.answer("Ошибка!", show_alert=True)
        return
    
    action = parts[1]
    m_id = parts[2]
    
    if m_id not in pending_marriages:
        await callback.answer("Предложение устарело!", show_alert=True)
        return
    
    data = pending_marriages[m_id]
    
    if action == "reject":
        del pending_marriages[m_id]
        await callback.message.edit_text("💔 Предложение отклонено!", parse_mode="HTML")
        return
    
    if callback.from_user.id != data["u2"]:
        await callback.answer("Это предложение не вам!", show_alert=True)
        return

    async with db_pool.acquire() as conn:
        fam_id = await conn.fetchval(
            "INSERT INTO families (user1_id, user2_id, name, created_at) VALUES ($1, $2, $3, $4) RETURNING id",
            data["u1"], data["u2"], data["fam_name"], datetime.date.today()
        )
        await conn.execute("UPDATE users SET family_id = $1, spouse_id = $2, stars = stars + 5 WHERE user_id IN ($3, $4)", 
                         fam_id, data["u2"], data["u1"], data["u2"])
    
    del pending_marriages[m_id]
    await callback.message.edit_text("🎉 <b>Семья создана! +5⭐ каждому!</b>", parse_mode="HTML")
    await check_achievements(data["u1"])
    await check_achievements(data["u2"])

# ---------------------------------------------------------
# ✏️ СМЕНА НАЗВАНИЯ СЕМЬИ
# ---------------------------------------------------------
@router.message(F.text.lower().startswith("сменить_имя_семьи"))
async def cmd_rename_family(message: Message):
    if not message.reply_to_message:
        await message.answer("❌ Ответьте на сообщение партнера!", parse_mode="HTML")
        return
    
    args = message.text.split(maxsplit=2)
    if len(args) < 3:
        await message.answer("❌ Формат: <code>сменить_имя_семьи @партнёр НовоеИмя</code>", parse_mode="HTML")
        return

    new_name = args[2].strip()
    user = await get_or_create_user(message.from_user.id, message.from_user.username)
    if not user['family_id']:
        await message.answer("❌ Вы не состоите в семье!", parse_mode="HTML")
        return

    if user['stars'] < 5:
        await message.answer("❌ Смена имени стоит 5⭐!", parse_mode="HTML")
        return

    rename_id = f"{user['user_id']}_{user['spouse_id']}_{int(datetime.datetime.utcnow().timestamp())}"
    pending_renames[rename_id] = {
        "family_id": user['family_id'],
        "new_name": new_name,
        "requester": user['user_id'],
        "partner": user['spouse_id']
    }
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Принять", callback_data=f"rename_accept_{rename_id}"),
            InlineKeyboardButton(text="❌ Отклонить", callback_data=f"rename_reject_{rename_id}")
        ]
    ])
    await message.answer(f"✏️ Партнер предлагает сменить название семьи на: <b>{new_name}</b>\nСтоимость: 5⭐", reply_markup=kb, parse_mode="HTML")

@router.callback_query(F.data.startswith("rename_"))
async def process_rename(callback: CallbackQuery):
    await callback.answer()
    parts = callback.data.split("_")
    if len(parts) < 3:
        await callback.answer("Ошибка!", show_alert=True)
        return
    
    action = parts[1]
    rename_id = parts[2]
    
    if rename_id not in pending_renames:
        await callback.answer("Предложение устарело!", show_alert=True)
        return
    
    rename = pending_renames[rename_id]
    
    if action == "reject":
        del pending_renames[rename_id]
        await callback.message.edit_text("❌ Смена имени отклонена!", parse_mode="HTML")
        return
    
    if callback.from_user.id != rename["partner"]:
        await callback.answer("Это предложение не вам!", show_alert=True)
        return
    
    async with db_pool.acquire() as conn:
        user = await conn.fetchrow("SELECT stars FROM users WHERE user_id = $1", rename["requester"])
        if user['stars'] < 5:
            await callback.answer("❌ У инициатора недостаточно звёзд!", show_alert=True)
            return
        
        await conn.execute("UPDATE users SET stars = stars - 5 WHERE user_id = $1", rename["requester"])
        await conn.execute("UPDATE families SET name = $1 WHERE id = $2", rename["new_name"], rename["family_id"])
    
    del pending_renames[rename_id]
    await callback.message.edit_text(f"✅ Название семьи успешно изменено на: <b>{rename['new_name']}</b>", parse_mode="HTML")

# ---------------------------------------------------------
# 💔 РАЗВОД
# ---------------------------------------------------------
@router.message(F.text.lower() == "развод")
async def cmd_divorce(message: Message):
    user = await get_or_create_user(message.from_user.id, message.from_user.username)
    if not user['family_id']:
        await message.answer("❌ Вы не состоите в браке!", parse_mode="HTML")
        return
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💔 Да, развестись", callback_data="divorce_confirm")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="divorce_cancel")]
    ])
    await message.answer("💔 <b>Вы уверены, что хотите развестись?</b>\nПосле развода будет бан на брак 7 дней.", parse_mode="HTML", reply_markup=kb)

@router.callback_query(F.data.startswith("divorce_"))
async def process_divorce(callback: CallbackQuery):
    await callback.answer()
    if callback.data == "divorce_cancel":
        await callback.message.edit_text("❌ Развод отменен!", parse_mode="HTML")
        return
    
    user = await get_or_create_user(callback.from_user.id, callback.from_user.username)
    if not user['family_id']:
        await callback.answer("Вы не состоите в браке!", show_alert=True)
        return
    
    ban_date = datetime.date.today() + datetime.timedelta(days=7)
    async with db_pool.acquire() as conn:
        family_id = user['family_id']
        spouse_id = user['spouse_id']
        await conn.execute("DELETE FROM families WHERE id = $1", family_id)
        await conn.execute("UPDATE users SET family_id = NULL, spouse_id = NULL, divorce_until = $1 WHERE user_id IN ($2, $3)",
                         ban_date, user['user_id'], spouse_id)

    await callback.message.edit_text("💔 Бракоразводный процесс завершен. Установлен бан на брак на 7 дней.", parse_mode="HTML")

# ---------------------------------------------------------
# 👶 УСЫНОВЛЕНИЕ
# ---------------------------------------------------------
@router.message(F.text.lower().startswith("ребёнок"))
async def cmd_adopt(message: Message):
    if not message.reply_to_message:
        await message.answer("❌ Ответьте на сообщение игрока!", parse_mode="HTML")
        return

    parent = await get_or_create_user(message.from_user.id, message.from_user.username)
    child = await get_or_create_user(message.reply_to_message.from_user.id, message.reply_to_message.from_user.username)

    if not parent['family_id']:
        await message.answer("❌ Вы должны состоять в семье!", parse_mode="HTML")
        return

    async with db_pool.acquire() as conn:
        existing = await conn.fetchval("SELECT child_id FROM children WHERE child_id = $1", child['user_id'])
        if existing:
            await message.answer("❌ Этот игрок уже усыновлен!", parse_mode="HTML")
            return
        
        family = await conn.fetchrow("SELECT top1_count FROM families WHERE id = $1", parent['family_id'])
        if family['top1_count'] < 5:
            await message.answer("❌ Семья должна быть в топ-1 минимум 5 раз!", parse_mode="HTML")
            return
        
        await conn.execute("INSERT INTO children (family_id, child_id) VALUES ($1, $2)", parent['family_id'], child['user_id'])

    await message.answer(f"👶 Игрок {message.reply_to_message.from_user.first_name} успешно усыновлён!", parse_mode="HTML")

# ---------------------------------------------------------
# 👨‍👩‍👧‍👦 ИНФОРМАЦИЯ О СЕМЬЕ
# ---------------------------------------------------------
@router(message, F.text.lower() == "семья")
async def cmd_family(message: Message):
    user = await get_or_create_user(message.from_user.id, message.from_user.username)
    if not user['family_id']:
        await message.answer("❌ Вы не в браке!", parse_mode="HTML")
        return
    
    async with db_pool.acquire() as conn:
        fam = await conn.fetchrow("SELECT * FROM families WHERE id = $1", user['family_id'])
        children = await conn.fetch("SELECT child_id FROM children WHERE family_id = $1", user['family_id'])
    
    fam_name = fam['name'] or f"Семья #{fam['id']}"
    spouse = await get_or_create_user(user['spouse_id'], None)
    spouse_name = spouse['custom_nick'] or spouse['username'] or f"Игрок_{spouse['user_id']}"
    
    children_list = []
    for c in children:
        child_user = await get_or_create_user(c['child_id'], None)
        children_list.append(child_user['custom_nick'] or child_user['username'] or f"Игрок_{c['child_id']}")
    
    children_text = "\n".join([f"👶 {c}" for c in children_list]) if children_list else "Нет детей"
    
    text = f"""💍 <b>{fam_name}</b>
👨‍👩‍👧‍👦 Супруги: {user['custom_nick'] or user['username']} & {spouse_name}
📅 Дата создания: {fam['created_at']}
⭐ Очки семьи: {fam['score']}
🎖️ Побед в Топ-1: {fam['top1_count']}

👶 Дети:
{children_text}"""
    await message.answer(text, parse_mode="HTML")

# ---------------------------------------------------------
# 🎂 ГОДОВЩИНА
# ---------------------------------------------------------
@router.message(F.text.lower() == "годовщина")
async def cmd_anniversary(message: Message):
    user = await get_or_create_user(message.from_user.id, message.from_user.username)
    if not user['family_id']:
        await message.answer("❌ Вы не в браке!", parse_mode="HTML")
        return
    
    async with db_pool.acquire() as conn:
        fam = await conn.fetchrow("SELECT created_at, last_anniversary_month FROM families WHERE id = $1", user['family_id'])
    
    created = fam['created_at']
    months = (datetime.date.today().year - created.year) * 12 + datetime.date.today().month - created.month
    
    if months > fam['last_anniversary_month']:
        reward = min(5 * months, 50)
        async with db_pool.acquire() as conn:
            await conn.execute("UPDATE families SET last_anniversary_month = $1 WHERE id = $2", months, user['family_id'])
            await conn.execute("UPDATE users SET stars = stars + $1 WHERE user_id IN ($2, $3)", reward, user['user_id'], user['spouse_id'])
        await message.answer(f"🎂 <b>ГОДОВЩИНА! Вместе {months} мес.! +{reward}⭐ каждому!</b>", parse_mode="HTML")
    else:
        await message.answer(f"📅 Вы вместе {months} мес. Следующий бонус через {months - fam['last_anniversary_month']} мес.", parse_mode="HTML")

# ---------------------------------------------------------
# 🎯 КВЕСТЫ
# ---------------------------------------------------------
@router.message(F.text.lower().in_(["квесты", "🎯 квесты"]))
async def cmd_quests(message: Message):
    user = await get_or_create_user(message.from_user.id, message.from_user.username)
    
    quests = [
        ("1️⃣ Сыграть 10 игр", user['total_games'] >= 10, user['quest1_done']),
        ("2️⃣ Выиграть 5 дуэлей", user['duel_wins'] >= 5, user['quest2_done']),
        ("3️⃣ Угадать число в рулетке", user['roulette_wins'] >= 1, user['quest3_done']),
        ("4️⃣ Сыграть в котиков 3 раза", user['cat_games'] >= 3, user['quest4_done']),
        ("5️⃣ Выиграть 500 монет за день", user['daily_net_win'] >= 500, user['quest5_done']),
    ]
    
    text = "🎯 <b>ЕЖЕНЕДЕЛЬНЫЕ КВЕСТЫ</b>\n\n"
    for q_name, completed, done in quests:
        status = "✅" if done else "⏳" if completed else "❌"
        text += f"{status} {q_name}\n"
    
    text += f"\n📊 Выполнено квестов: {user['quests_completed']}/5"
    if user['quests_completed'] >= 5 and not user['quest_bonus_claimed']:
        text += "\n🎁 БОНУС ЗА ВСЕ КВЕСТЫ ДОСТУПЕН!"
    elif user['quest_bonus_claimed']:
        text += "\n✅ БОНУС ЗА ВСЕ КВЕСТЫ ПОЛУЧЕН!"
    
    await message.answer(text, parse_mode="HTML")

# ---------------------------------------------------------
# 🏆 ТУРНИР
# ---------------------------------------------------------
@router.message(F.text.lower().in_(["турнир", "🏆 турнир"]))
async def cmd_tournament(message: Message):
    time_left = await get_tournament_time_left()
    
    async with db_pool.acquire() as conn:
        participants = await conn.fetchval("SELECT COUNT(*) FROM users WHERE tournament_fee_paid = TRUE")
        total_fee = participants * 10
        prize_pool = 500 + total_fee
        
        top_participants = await conn.fetch(
            "SELECT user_id, username, custom_nick, tournament_score FROM users WHERE tournament_fee_paid = TRUE ORDER BY tournament_score DESC LIMIT 3"
        )
    
    text = f"""🏆 <b>ЕЖЕНЕДЕЛЬНЫЙ ТУРНИР</b>

{time_left}

👥 Участников: {participants}
💰 Призовой фонд: {prize_pool}⭐ (500⭐ + взносы)

🏅 Текущий топ-3:
"""
    if top_participants:
        for idx, p in enumerate(top_participants, 1):
            name = p['custom_nick'] or p['username'] or f"Игрок_{p['user_id']}"
            text += f"{idx}. {name} — {p['tournament_score']} побед\n"
    else:
        text += "Нет участников\n"
    
    text += """
🎖️ Награды:
🥇 1 место — 60% фонда
🥈 2 место — 30% фонда
🥉 3 место — 10% фонда
"""
    
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🎟️ Участвовать (10⭐)", callback_data="join_tournament")
    ]])
    await message.answer(text, parse_mode="HTML", reply_markup=kb)

@router.callback_query(F.data == "join_tournament")
async def join_tournament(callback: CallbackQuery):
    await callback.answer()
    user = await get_or_create_user(callback.from_user.id, callback.from_user.username)
    if user['tournament_fee_paid']:
        await callback.answer("Вы уже участвуете!", show_alert=True)
        return
    if user['stars'] < 10:
        await callback.answer("❌ Недостаточно звёзд!", show_alert=True)
        return
    
    async with db_pool.acquire() as conn:
        await conn.execute("UPDATE users SET stars = stars - 10, tournament_fee_paid = TRUE WHERE user_id = $1", user['user_id'])
    
    await callback.message.edit_text("✅ Вы успешно зарегистрированы в турнире!\nУдачи! 🍀", parse_mode="HTML")

# ============================================================
# 👑 АДМИН-ПАНЕЛЬ
# ============================================================
@router.message(Command("admin"))
async def cmd_admin(message: Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("❌ У вас нет доступа к админ-панели!", parse_mode="HTML")
        return
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Статистика бота", callback_data="admin_stats")],
        [InlineKeyboardButton(text="🎫 Создать промокод", callback_data="admin_create_promo")],
        [InlineKeyboardButton(text="📋 Список промокодов", callback_data="admin_list_promos")],
        [InlineKeyboardButton(text="🗑️ Удалить промокод", callback_data="admin_delete_promo")],
        [InlineKeyboardButton(text="📋 Список пользователей", callback_data="admin_users")],
        [InlineKeyboardButton(text="🗑️ Удалить профиль", callback_data="admin_delete_user")],
        [InlineKeyboardButton(text="🔒 Блокировка/Разблокировка", callback_data="admin_ban_user")],
        [InlineKeyboardButton(text="📨 Рассылка сообщения", callback_data="admin_mailing")],
        [InlineKeyboardButton(text="🔍 Проверка БД", callback_data="admin_check_db")]
    ])
    
    await message.answer(
        "👑 <b>АДМИН-ПАНЕЛЬ</b>\n\n"
        "Выберите действие:",
        parse_mode="HTML",
        reply_markup=kb
    )

# ---------------------------------------------------------
# 📊 СТАТИСТИКА БОТА (АДМИН)
# ---------------------------------------------------------
@router.callback_query(F.data == "admin_stats")
async def admin_stats(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("❌ У вас нет прав!", show_alert=True)
        return
    
    await callback.answer()
    
    try:
        async with db_pool.acquire() as conn:
            users_count = await conn.fetchval("SELECT COUNT(*) FROM users")
            families_count = await conn.fetchval("SELECT COUNT(*) FROM families")
            achievements_count = await conn.fetchval("SELECT COUNT(*) FROM achievements")
            promos_count = await conn.fetchval("SELECT COUNT(*) FROM promos")
            
            total_coins = await conn.fetchval("SELECT COALESCE(SUM(coins), 0) FROM users")
            total_stars = await conn.fetchval("SELECT COALESCE(SUM(stars), 0) FROM users")
            avg_coins = await conn.fetchval("SELECT COALESCE(AVG(coins), 0) FROM users")
            avg_stars = await conn.fetchval("SELECT COALESCE(AVG(stars), 0) FROM users")
            
            vip_count = await conn.fetchval("SELECT COUNT(*) FROM users WHERE is_vip = TRUE")
            
            week_ago = datetime.date.today() - datetime.timedelta(days=7)
            active_users = await conn.fetchval("SELECT COUNT(*) FROM users WHERE last_active_date >= $1", week_ago)
            
            total_games = await conn.fetchval("SELECT COALESCE(SUM(total_games), 0) FROM users")
            total_wins = await conn.fetchval("SELECT COALESCE(SUM(wins), 0) FROM users")
            
            text = f"""📊 <b>СТАТИСТИКА БОТА</b>

━━━━━━━━━━━━━━━━━━━
👥 <b>ПОЛЬЗОВАТЕЛИ</b>
👤 Всего: {users_count}
👑 VIP: {vip_count}
📅 Активных за 7 дней: {active_users}

━━━━━━━━━━━━━━━━━━━
💰 <b>ЭКОНОМИКА</b>
💎 Всего монет: {total_coins:,}
⭐ Всего звёзд: {total_stars:,}
📊 Среднее монет: {avg_coins:.0f}
📊 Среднее звёзд: {avg_stars:.0f}

━━━━━━━━━━━━━━━━━━━
🎮 <b>ИГРОВАЯ СТАТИСТИКА</b>
🎯 Всего игр: {total_games:,}
🏆 Всего побед: {total_wins:,}
📈 Процент побед: {round(total_wins/total_games*100, 1) if total_games > 0 else 0}%

━━━━━━━━━━━━━━━━━━━
🏠 <b>СОЦИАЛКА</b>
💍 Семей: {families_count}
🏆 Ачивок: {achievements_count}
🎫 Промокодов: {promos_count}
"""
        
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад в админ-панель", callback_data="admin_back")]
        ])
        
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
        
    except Exception as e:
        await callback.message.edit_text(f"❌ Ошибка: {e}", parse_mode="HTML")

# ---------------------------------------------------------
# 🎫 СОЗДАНИЕ ПРОМОКОДА
# ---------------------------------------------------------
@router.callback_query(F.data == "admin_create_promo")
async def admin_create_promo_menu(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("❌ У вас нет прав!", show_alert=True)
        return
    
    await callback.answer()
    await callback.message.edit_text(
        "🎫 <b>СОЗДАНИЕ ПРОМОКОДА</b>\n\n"
        "Отправьте сообщение в формате:\n"
        "<code>КОД ЗВЕЗДЫ МОНЕТЫ</code>\n\n"
        "Пример:\n"
        "<code>HAPPY2026 50 1000</code>\n\n"
        "⏳ Ожидаю ввода...",
        parse_mode="HTML"
    )

@router.message(Command("create_promo"))
async def cmd_create_promo(message: Message, command: CommandObject):
    if message.from_user.id != ADMIN_ID:
        await message.answer("❌ У вас нет прав!", parse_mode="HTML")
        return
    
    if not command.args:
        await message.answer("Формат: /create_promo КОД ЗВЕЗДЫ МОНЕТЫ", parse_mode="HTML")
        return
    
    args = command.args.split()
    if len(args) < 3:
        await message.answer("Формат: /create_promo КОД ЗВЕЗДЫ МОНЕТЫ", parse_mode="HTML")
        return
    
    code, stars, coins = args[0], int(args[1]), int(args[2])
    async with db_pool.acquire() as conn:
        existing = await conn.fetchval("SELECT code FROM promos WHERE code = $1", code)
        if existing:
            await message.answer(f"❌ Промокод <b>{code}</b> уже существует!", parse_mode="HTML")
            return
        
        await conn.execute("INSERT INTO promos (code, stars, coins, max_uses) VALUES ($1, $2, $3, 100)", code, stars, coins)
    
    await message.answer(
        f"✅ <b>ПРОМОКОД СОЗДАН!</b>\n\n"
        f"📌 Код: <code>{code}</code>\n"
        f"⭐ Звёзд: +{stars}\n"
        f"💰 Монет: +{coins}\n"
        f"📊 Макс. использований: 100",
        parse_mode="HTML"
    )

@router.message(F.text.regexp(r'^[A-Za-z0-9_]+ \d+ \d+$'))
async def admin_create_promo_handler(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    
    try:
        code, stars, coins = message.text.split()
        stars = int(stars)
        coins = int(coins)
        
        async with db_pool.acquire() as conn:
            existing = await conn.fetchval("SELECT code FROM promos WHERE code = $1", code)
            if existing:
                await message.answer(f"❌ Промокод <b>{code}</b> уже существует!", parse_mode="HTML")
                return
            
            await conn.execute("INSERT INTO promos (code, stars, coins, max_uses) VALUES ($1, $2, $3, 100)", code, stars, coins)
        
        await message.answer(
            f"✅ <b>ПРОМОКОД СОЗДАН!</b>\n\n"
            f"📌 Код: <code>{code}</code>\n"
            f"⭐ Звёзд: +{stars}\n"
            f"💰 Монет: +{coins}\n"
            f"📊 Макс. использований: 100",
            parse_mode="HTML"
        )
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}", parse_mode="HTML")

# ---------------------------------------------------------
# 📋 СПИСОК ПРОМОКОДОВ
# ---------------------------------------------------------
@router.callback_query(F.data == "admin_list_promos")
async def admin_list_promos(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("❌ У вас нет прав!", show_alert=True)
        return
    
    await callback.answer()
    
    async with db_pool.acquire() as conn:
        promos = await conn.fetch("SELECT code, stars, coins, uses, max_uses, created_at FROM promos ORDER BY created_at DESC LIMIT 20")
    
    if not promos:
        text = "📋 <b>СПИСОК ПРОМОКОДОВ</b>\n\nНет созданных промокодов"
    else:
        text = "📋 <b>СПИСОК ПРОМОКОДОВ</b>\n\n"
        for p in promos:
            text += f"📌 <code>{p['code']}</code>\n"
            text += f"   ⭐ +{p['stars']} | 💰 +{p['coins']}\n"
            text += f"   📊 {p['uses']}/{p['max_uses']} использований\n\n"
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Назад в админ-панель", callback_data="admin_back")]
    ])
    
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)

# ---------------------------------------------------------
# 🗑️ УДАЛЕНИЕ ПРОМОКОДА
# ---------------------------------------------------------
@router.callback_query(F.data == "admin_delete_promo")
async def admin_delete_promo_menu(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("❌ У вас нет прав!", show_alert=True)
        return
    
    await callback.answer()
    
    async with db_pool.acquire() as conn:
        promos = await conn.fetch("SELECT code, stars, coins, uses FROM promos ORDER BY created_at DESC LIMIT 20")
    
    if not promos:
        await callback.message.edit_text("🗑️ <b>УДАЛЕНИЕ ПРОМОКОДА</b>\n\nНет промокодов для удаления", parse_mode="HTML")
        return
    
    buttons = []
    for p in promos:
        btn_text = f"{p['code']} (+{p['stars']}⭐ +{p['coins']}💰) [{p['uses']} использований]"
        buttons.append([InlineKeyboardButton(text=btn_text, callback_data=f"delete_promo_{p['code']}")])
    
    buttons.append([InlineKeyboardButton(text="🔙 Назад в админ-панель", callback_data="admin_back")])
    
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    
    await callback.message.edit_text(
        "🗑️ <b>УДАЛЕНИЕ ПРОМОКОДА</b>\n\nВыберите промокод для удаления:",
        parse_mode="HTML",
        reply_markup=kb
    )

@router.callback_query(F.data.startswith("delete_promo_"))
async def admin_delete_promo(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("❌ У вас нет прав!", show_alert=True)
        return
    
    await callback.answer()
    code = callback.data.replace("delete_promo_", "")
    
    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM promos WHERE code = $1", code)
        await conn.execute("DELETE FROM user_promos WHERE code = $1", code)
    
    await admin_delete_promo_menu(callback)

# ---------------------------------------------------------
# 📋 СПИСОК ПОЛЬЗОВАТЕЛЕЙ (АДМИН)
# ---------------------------------------------------------
@router.callback_query(F.data == "admin_users")
async def admin_users(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("❌ У вас нет прав!", show_alert=True)
        return
    
    await callback.answer()
    
    async with db_pool.acquire() as conn:
        users = await conn.fetch("SELECT user_id, username, custom_nick, coins, stars, wins, total_games FROM users ORDER BY coins DESC LIMIT 20")
    
    text = "📋 <b>ТОП-20 ПОЛЬЗОВАТЕЛЕЙ ПО МОНЕТАМ</b>\n\n"
    for idx, u in enumerate(users, 1):
        name = u['custom_nick'] or u['username'] or f"ID:{u['user_id']}"
        text += f"{idx}. {name}\n"
        text += f"   💰 {u['coins']:,} | ⭐ {u['stars']} | 🏆 {u['wins']} побед\n\n"
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Назад в админ-панель", callback_data="admin_back")]
    ])
    
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)

# ---------------------------------------------------------
# 🗑️ УДАЛЕНИЕ ПРОФИЛЯ ПОЛЬЗОВАТЕЛЯ
# ---------------------------------------------------------
@router.callback_query(F.data == "admin_delete_user")
async def admin_delete_user_menu(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("❌ У вас нет прав!", show_alert=True)
        return
    
    await callback.answer()
    
    async with db_pool.acquire() as conn:
        users = await conn.fetch("""
            SELECT user_id, username, custom_nick, coins, wins 
            FROM users 
            ORDER BY coins DESC 
            LIMIT 20
        """)
    
    if not users:
        await callback.message.edit_text("📋 <b>УДАЛЕНИЕ ПРОФИЛЯ</b>\n\nНет пользователей в базе!", parse_mode="HTML")
        return
    
    buttons = []
    for u in users:
        name = u['custom_nick'] or u['username'] or f"ID:{u['user_id']}"
        btn_text = f"{name} (💰{u['coins']:,} 🏆{u['wins']})"
        buttons.append([InlineKeyboardButton(text=btn_text, callback_data=f"del_user_{u['user_id']}")])
    
    buttons.append([InlineKeyboardButton(text="🔙 Назад в админ-панель", callback_data="admin_back")])
    
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    
    await callback.message.edit_text(
        "🗑️ <b>УДАЛЕНИЕ ПРОФИЛЯ</b>\n\n"
        "⚠️ <b>ВНИМАНИЕ!</b> Это действие НЕОБРАТИМО!\n"
        "Выберите пользователя для удаления:",
        parse_mode="HTML",
        reply_markup=kb
    )

@router.callback_query(F.data.startswith("del_user_"))
async def admin_delete_user(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("❌ У вас нет прав!", show_alert=True)
        return
    
    await callback.answer()
    user_id = int(callback.data.replace("del_user_", ""))
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ ДА, УДАЛИТЬ", callback_data=f"confirm_del_{user_id}")],
        [InlineKeyboardButton(text="❌ ОТМЕНА", callback_data="admin_delete_user")]
    ])
    
    await callback.message.edit_text(
        f"⚠️ <b>ВЫ УВЕРЕНЫ?</b>\n\n"
        f"Пользователь с ID {user_id} будет ПОЛНОСТЬЮ УДАЛЁН из базы данных!\n"
        f"Это действие НЕОБРАТИМО!",
        parse_mode="HTML",
        reply_markup=kb
    )

@router.callback_query(F.data.startswith("confirm_del_"))
async def admin_confirm_delete(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("❌ У вас нет прав!", show_alert=True)
        return
    
    await callback.answer()
    user_id = int(callback.data.replace("confirm_del_", ""))
    
    try:
        async with db_pool.acquire() as conn:
            await conn.execute("DELETE FROM achievements WHERE user_id = $1", user_id)
            await conn.execute("DELETE FROM user_promos WHERE user_id = $1", user_id)
            await conn.execute("DELETE FROM children WHERE child_id = $1", user_id)
            
            family = await conn.fetchrow("SELECT family_id FROM users WHERE user_id = $1", user_id)
            if family and family['family_id']:
                spouse = await conn.fetchrow("SELECT spouse_id FROM users WHERE user_id = $1", user_id)
                if spouse:
                    await conn.execute("UPDATE users SET family_id = NULL, spouse_id = NULL WHERE user_id = $1", spouse['spouse_id'])
                await conn.execute("DELETE FROM families WHERE id = $1", family['family_id'])
            
            await conn.execute("DELETE FROM users WHERE user_id = $1", user_id)
        
        await callback.message.edit_text(
            f"✅ <b>ПОЛЬЗОВАТЕЛЬ {user_id} УДАЛЁН!</b>\n\n"
            f"Все данные удалены из базы.",
            parse_mode="HTML"
        )
        
    except Exception as e:
        await callback.message.edit_text(
            f"❌ <b>ОШИБКА!</b>\n\n{str(e)}",
            parse_mode="HTML"
        )

# ---------------------------------------------------------
# 🔒 БЛОКИРОВКА/РАЗБЛОКИРОВКА ПОЛЬЗОВАТЕЛЯ
# ---------------------------------------------------------
@router.callback_query(F.data == "admin_ban_user")
async def admin_ban_user_menu(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("❌ У вас нет прав!", show_alert=True)
        return
    
    await callback.answer()
    
    async with db_pool.acquire() as conn:
        users = await conn.fetch("""
            SELECT user_id, username, custom_nick, coins, is_hidden 
            FROM users 
            ORDER BY coins DESC 
            LIMIT 20
        """)
    
    if not users:
        await callback.message.edit_text("🔒 <b>БЛОКИРОВКА ПОЛЬЗОВАТЕЛЕЙ</b>\n\nНет пользователей в базе!", parse_mode="HTML")
        return
    
    buttons = []
    for u in users:
        name = u['custom_nick'] or u['username'] or f"ID:{u['user_id']}"
        status = "🔴 ЗАБЛОКИРОВАН" if u['is_hidden'] else "🟢 АКТИВЕН"
        btn_text = f"{name} - {status}"
        buttons.append([InlineKeyboardButton(text=btn_text, callback_data=f"ban_toggle_{u['user_id']}")])
    
    buttons.append([InlineKeyboardButton(text="🔙 Назад в админ-панель", callback_data="admin_back")])
    
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    
    await callback.message.edit_text(
        "🔒 <b>БЛОКИРОВКА/РАЗБЛОКИРОВКА</b>\n\n"
        "Нажмите на пользователя, чтобы изменить статус:\n"
        "🟢 АКТИВЕН - может играть\n"
        "🔴 ЗАБЛОКИРОВАН - не может играть",
        parse_mode="HTML",
        reply_markup=kb
    )

@router.callback_query(F.data.startswith("ban_toggle_"))
async def admin_ban_toggle(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("❌ У вас нет прав!", show_alert=True)
        return
    
    await callback.answer()
    user_id = int(callback.data.replace("ban_toggle_", ""))
    
    async with db_pool.acquire() as conn:
        user = await conn.fetchrow("SELECT is_hidden FROM users WHERE user_id = $1", user_id)
        if not user:
            await callback.answer("Пользователь не найден!", show_alert=True)
            return
        
        new_status = not user['is_hidden']
        await conn.execute("UPDATE users SET is_hidden = $1 WHERE user_id = $2", new_status, user_id)
        
        status_text = "ЗАБЛОКИРОВАН 🔴" if new_status else "РАЗБЛОКИРОВАН 🟢"
        try:
            await bot.send_message(
                user_id,
                f"🔒 <b>ВАШ СТАТУС ИЗМЕНЁН!</b>\n\n"
                f"Новый статус: {status_text}\n"
                f"{'Вы не можете участвовать в играх и топах.' if new_status else 'Вы снова можете играть!'}",
                parse_mode="HTML"
            )
        except Exception:
            pass
    
    await admin_ban_user_menu(callback)

# ============================================================
# 📨 РАССЫЛКА СООБЩЕНИЙ (АДМИН)
# ============================================================
@router.callback_query(F.data == "admin_mailing")
async def admin_mailing_start(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("❌ У вас нет прав!", show_alert=True)
        return
    
    await callback.answer()
    await callback.message.edit_text(
        "📨 <b>РАССЫЛКА СООБЩЕНИЙ</b>\n\n"
        "Отправьте сообщение, которое хотите разослать всем пользователям.\n\n"
        "📌 Сообщение может содержать текст, ссылки, эмодзи.\n"
        "⏳ Бот отправит сообщение ВСЕМ пользователям.\n\n"
        "❌ Для отмены отправьте /cancel_mailing",
        parse_mode="HTML"
    )
    
    admin_mailing_state[callback.from_user.id] = True

@router.message(Command("cancel_mailing"))
async def cancel_mailing(message: Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("❌ У вас нет прав!", parse_mode="HTML")
        return
    
    if message.from_user.id in admin_mailing_state:
        del admin_mailing_state[message.from_user.id]
        await message.answer("❌ Рассылка отменена!", parse_mode="HTML")
    else:
        await message.answer("❌ Активная рассылка не найдена!", parse_mode="HTML")

# ---------------------------------------------------------
# 🚀 ЗАПУСК БОТА
# ---------------------------------------------------------
async def main():
    await init_db()
    await start_web_server()
    asyncio.create_task(daily_cron_loop())
    await bot.delete_webhook(drop_pending_updates=True)
    logging.info("🤖 Бот запущен и работает!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
