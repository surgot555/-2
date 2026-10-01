import asyncio
import logging
import math
import os
import random
import sqlite3
import time

from aiohttp import web
from aiogram import Bot, Dispatcher, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
)

logging.basicConfig(level=logging.INFO)

TOKEN = os.environ["BOT_TOKEN"]
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0"))
DB_PATH = os.environ.get("DB_PATH", "game.db")
SUPPORT = os.environ.get("SUPPORT_CONTACT", "@admin")

REGEN_SEC = 5  # 1 энергия восстанавливается за 5 секунд
BOT_USERNAME = ""

bot = Bot(TOKEN)
router = Router()

# ---------------------------------------------------------------- БАЗА

db = sqlite3.connect(DB_PATH, check_same_thread=False)
db.row_factory = sqlite3.Row
db.executescript(
    """
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY, name TEXT,
  coins INTEGER DEFAULT 0, total INTEGER DEFAULT 0,
  energy REAL DEFAULT 100, energy_ts REAL,
  last_daily INTEGER DEFAULT 0, streak INTEGER DEFAULT 0,
  boost_until REAL DEFAULT 0, vip INTEGER DEFAULT 0,
  refs INTEGER DEFAULT 0, joined REAL);
CREATE TABLE IF NOT EXISTS payments(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER, item TEXT, stars INTEGER, ts REAL);
CREATE TABLE IF NOT EXISTS settings(k TEXT PRIMARY KEY, v TEXT);
"""
)
db.commit()


def user_exists(uid):
    return db.execute("SELECT 1 FROM users WHERE id=?", (uid,)).fetchone() is not None


def get_user(uid, name="Игрок"):
    if not user_exists(uid):
        now = time.time()
        db.execute(
            "INSERT INTO users(id,name,energy_ts,joined) VALUES(?,?,?,?)",
            (uid, name[:40], now, now),
        )
        db.commit()
    return db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()


def get_setting(k):
    r = db.execute("SELECT v FROM settings WHERE k=?", (k,)).fetchone()
    return r["v"] if r else ""


def set_setting(k, v):
    db.execute("INSERT OR REPLACE INTO settings(k,v) VALUES(?,?)", (k, v))
    db.commit()


# ---------------------------------------------------------------- ЛОГИКА


def num(n):
    return f"{int(n):,}".replace(",", " ")


def max_energy(u):
    return 200 if u["vip"] else 100


def cur_energy(u):
    gained = (time.time() - u["energy_ts"]) / REGEN_SEC
    return min(max_energy(u), u["energy"] + gained)


def mult(u):
    m = 1
    if u["boost_until"] > time.time():
        m += 1
    if u["vip"]:
        m += 2
    return m


def level(total):
    return int(math.sqrt(total / 500)) + 1


TITLES = [
    (1, "🌱 Новичок"),
    (3, "🔍 Искатель"),
    (6, "🎮 Игрок"),
    (10, "⚔️ Мастер"),
    (15, "🔥 Легенда"),
    (25, "💎 Миллионер"),
]


def title(u):
    lv = level(u["total"])
    t = TITLES[0][1]
    for need, name in TITLES:
        if lv >= need:
            t = name
    return ("👑 " if u["vip"] else "") + t


def btn(text, data):
    return InlineKeyboardButton(text=text, callback_data=data)


MENU_BTN = [btn("⬅️ Меню", "menu")]


def main_kb():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [btn("👆 Тапалка", "tap"), btn("🎲 Кубик", "dice")],
            [btn("🎰 Слоты", "slots"), btn("🎁 Бонус", "daily")],
            [btn("🏆 Топ", "top"), btn("👥 Друзья", "ref")],
            [btn("⭐ Магазин", "shop"), btn("👤 Профиль", "me")],
        ]
    )


def menu_text(u):
    t = (
        f"🎮 Монетный Город\n\n{title(u)} • уровень {level(u['total'])}\n"
        f"💰 Монеты: {num(u['coins'])}\n\n"
        "🎯 Цель: стать 💎 Миллионером и попасть в ТОП-10!\n"
        "Выбирай игру 👇"
    )
    ad = get_setting("ad")
    if ad:
        t += f"\n\n📢 {ad}"
    return t


async def show(event, text, kb):
    if isinstance(event, CallbackQuery):
        try:
            await event.message.edit_text(text, reply_markup=kb)
        except TelegramAPIError:
            pass
        await event.answer()
    else:
        await event.answer(text, reply_markup=kb)


# ---------------------------------------------------------------- СТАРТ / МЕНЮ


@router.message(CommandStart())
async def start(m: Message, command: CommandObject):
    uid = m.from_user.id
    is_new = not user_exists(uid)
    u = get_user(uid, m.from_user.full_name)
    if is_new and command.args and command.args.startswith("ref_"):
        try:
            ref = int(command.args[4:])
        except ValueError:
            ref = 0
        if ref and ref != uid and user_exists(ref):
            db.execute(
                "UPDATE users SET coins=coins+500,total=total+500,refs=refs+1 WHERE id=?",
                (ref,),
            )
            db.execute(
                "UPDATE users SET coins=coins+200,total=total+200 WHERE id=?", (uid,)
            )
            db.commit()
            u = get_user(uid)
            try:
                await bot.send_message(
                    ref, f"👥 По твоей ссылке пришёл {m.from_user.full_name}! +500 монет 💰"
                )
            except TelegramAPIError:
                pass
    await m.answer(menu_text(u), reply_markup=main_kb())


@router.message(Command("menu"))
async def menu_cmd(m: Message):
    u = get_user(m.from_user.id, m.from_user.full_name)
    await m.answer(menu_text(u), reply_markup=main_kb())


@router.callback_query(F.data == "menu")
async def menu_cb(cb: CallbackQuery):
    u = get_user(cb.from_user.id, cb.from_user.full_name)
    await show(cb, menu_text(u), main_kb())


# ---------------------------------------------------------------- ТАПАЛКА


def tap_text(u):
    return (
        "👆 ТАПАЛКА\n\n"
        f"💰 Монеты: {num(u['coins'])}\n"
        f"⚡ Энергия: {int(cur_energy(u))}/{max_energy(u)}\n"
        f"✨ За тап: +{mult(u)}\n\nЖми кнопку!"
    )


def tap_kb():
    return InlineKeyboardMarkup(
        inline_keyboard=[[btn("👆 ТАП!", "hit")], [btn("⚡ Энергия", "shop"), *MENU_BTN]]
    )


@router.callback_query(F.data == "tap")
async def tap_open(cb: CallbackQuery):
    u = get_user(cb.from_user.id, cb.from_user.full_name)
    await show(cb, tap_text(u), tap_kb())


@router.callback_query(F.data == "hit")
async def tap_hit(cb: CallbackQuery):
    uid = cb.from_user.id
    u = get_user(uid, cb.from_user.full_name)
    e = cur_energy(u)
    if e < 1:
        await cb.answer("⚡ Энергия кончилась! Подожди или зарядись в магазине", show_alert=True)
        return
    m = mult(u)
    db.execute(
        "UPDATE users SET coins=coins+?,total=total+?,energy=?,energy_ts=? WHERE id=?",
        (m, m, e - 1, time.time(), uid),
    )
    db.commit()
    u = get_user(uid)
    try:
        await cb.message.edit_text(tap_text(u), reply_markup=tap_kb())
    except TelegramAPIError:
        pass
    await cb.answer()


# ---------------------------------------------------------------- КУБИК И СЛОТЫ


def bet_kb(prefix):
    row = [btn(str(b), f"{prefix}:{b}") for b in (10, 50, 100, 500)]
    return InlineKeyboardMarkup(inline_keyboard=[row, MENU_BTN])


def after_kb(prefix, bet):
    return InlineKeyboardMarkup(
        inline_keyboard=[[btn(f"🔁 Ещё раз ({bet})", f"{prefix}:{bet}")], MENU_BTN]
    )


def settle(uid, bet, payout):
    db.execute(
        "UPDATE users SET coins=coins+?,total=total+? WHERE id=?",
        (payout, max(0, payout - bet), uid),
    )
    db.commit()


@router.callback_query(F.data == "dice")
async def dice_menu(cb: CallbackQuery):
    await show(
        cb,
        "🎲 КУБИК\n\n1–3 — проигрыш\n4 — ставка возвращается\n5 — x2\n6 — x3\n\nВыбери ставку:",
        bet_kb("d"),
    )


@router.callback_query(F.data.startswith("d:"))
async def dice_play(cb: CallbackQuery):
    uid = cb.from_user.id
    bet = int(cb.data[2:])
    u = get_user(uid, cb.from_user.full_name)
    if u["coins"] < bet:
        await cb.answer("Не хватает монет 😢 Заработай в Тапалке!", show_alert=True)
        return
    db.execute("UPDATE users SET coins=coins-? WHERE id=?", (bet, uid))
    db.commit()
    await cb.answer()
    msg = await cb.message.answer_dice(emoji="🎲")
    await asyncio.sleep(4)
    k = {1: 0, 2: 0, 3: 0, 4: 1, 5: 2, 6: 3}[msg.dice.value]
    payout = bet * k
    settle(uid, bet, payout)
    if k == 0:
        res = f"😢 Выпало {msg.dice.value}. Минус {bet}."
    elif k == 1:
        res = f"😐 Выпало {msg.dice.value}. Ставка возвращена."
    else:
        res = f"🎉 Выпало {msg.dice.value}! Выигрыш {payout} (x{k})"
    u = get_user(uid)
    await cb.message.answer(f"{res}\n💰 Баланс: {num(u['coins'])}", reply_markup=after_kb("d", bet))


@router.callback_query(F.data == "slots")
async def slots_menu(cb: CallbackQuery):
    await show(
        cb,
        "🎰 СЛОТЫ\n\nТри одинаковых — x5\nДжекпот 777 — x10!\n\nВыбери ставку:",
        bet_kb("s"),
    )


@router.callback_query(F.data.startswith("s:"))
async def slots_play(cb: CallbackQuery):
    uid = cb.from_user.id
    bet = int(cb.data[2:])
    u = get_user(uid, cb.from_user.full_name)
    if u["coins"] < bet:
        await cb.answer("Не хватает монет 😢 Заработай в Тапалке!", show_alert=True)
        return
    db.execute("UPDATE users SET coins=coins-? WHERE id=?", (bet, uid))
    db.commit()
    await cb.answer()
    msg = await cb.message.answer_dice(emoji="🎰")
    await asyncio.sleep(3)
    v = msg.dice.value
    k = 10 if v == 64 else 5 if v in (1, 22, 43) else 0
    payout = bet * k
    settle(uid, bet, payout)
    if v == 64:
        res = f"💎 ДЖЕКПОТ! Выигрыш {payout} (x10)"
    elif k:
        res = f"🎉 Три в ряд! Выигрыш {payout} (x5)"
    else:
        res = f"😢 Не повезло. Минус {bet}."
    u = get_user(uid)
    await cb.message.answer(f"{res}\n💰 Баланс: {num(u['coins'])}", reply_markup=after_kb("s", bet))


# ---------------------------------------------------------------- БОНУС, ТОП, ДРУЗЬЯ, ПРОФИЛЬ


@router.callback_query(F.data == "daily")
async def daily(cb: CallbackQuery):
    uid = cb.from_user.id
    u = get_user(uid, cb.from_user.full_name)
    today = int(time.time() // 86400)
    if u["last_daily"] == today:
        await show(
            cb,
            f"🎁 Бонус на сегодня уже получен.\n🔥 Серия: {u['streak']} дн.\nПриходи завтра!",
            InlineKeyboardMarkup(inline_keyboard=[MENU_BTN]),
        )
        return
    streak = u["streak"] + 1 if u["last_daily"] == today - 1 else 1
    reward = 100 * min(streak, 7)
    db.execute(
        "UPDATE users SET coins=coins+?,total=total+?,last_daily=?,streak=? WHERE id=?",
        (reward, reward, today, streak, uid),
    )
    db.commit()
    await show(
        cb,
        f"🎁 Ежедневный бонус: +{reward} монет!\n🔥 Серия: {streak} дн. (макс. награда на 7-й день)",
        InlineKeyboardMarkup(inline_keyboard=[MENU_BTN]),
    )


@router.callback_query(F.data == "top")
async def top(cb: CallbackQuery):
    uid = cb.from_user.id
    u = get_user(uid, cb.from_user.full_name)
    rows = db.execute("SELECT name,total,vip FROM users ORDER BY total DESC LIMIT 10").fetchall()
    medals = ["🥇", "🥈", "🥉"]
    lines = []
    for i, r in enumerate(rows):
        mark = medals[i] if i < 3 else f"{i + 1}."
        lines.append(f"{mark} {'👑' if r['vip'] else ''}{r['name']} — {num(r['total'])}")
    rank = db.execute("SELECT COUNT(*)+1 FROM users WHERE total>?", (u["total"],)).fetchone()[0]
    text = "🏆 ТОП-10 ИГРОКОВ\n\n" + "\n".join(lines) + f"\n\nТвоё место: #{rank} ({num(u['total'])})"
    await show(cb, text, InlineKeyboardMarkup(inline_keyboard=[MENU_BTN]))


@router.callback_query(F.data == "ref")
async def ref(cb: CallbackQuery):
    uid = cb.from_user.id
    u = get_user(uid, cb.from_user.full_name)
    link = f"https://t.me/{BOT_USERNAME}?start=ref_{uid}"
    text = (
        "👥 ПРИГЛАШАЙ ДРУЗЕЙ\n\nЗа каждого друга: +500 монет тебе и +200 ему.\n\n"
        f"Твоя ссылка:\n{link}\n\nПриглашено: {u['refs']}"
    )
    await show(cb, text, InlineKeyboardMarkup(inline_keyboard=[MENU_BTN]))


@router.callback_query(F.data == "me")
async def me(cb: CallbackQuery):
    u = get_user(cb.from_user.id, cb.from_user.full_name)
    lv = level(u["total"])
    boost = ""
    if u["boost_until"] > time.time():
        left = int((u["boost_until"] - time.time()) // 60)
        boost = f"\n🚀 Буст x2: ещё {left // 60} ч {left % 60} мин"
    text = (
        f"👤 ПРОФИЛЬ\n\n{title(u)}\nУровень: {lv}\n"
        f"💰 Монеты: {num(u['coins'])}\n"
        f"📈 Всего заработано: {num(u['total'])} / {num(500 * lv * lv)} до след. уровня\n"
        f"✨ За тап: +{mult(u)}\n⚡ Макс. энергия: {max_energy(u)}\n"
        f"👥 Друзей: {u['refs']}{boost}"
    )
    await show(cb, text, InlineKeyboardMarkup(inline_keyboard=[MENU_BTN]))


# ---------------------------------------------------------------- МАГАЗИН (STARS)

ITEMS = {
    "energy": ("⚡ Полная энергия", 5, "Мгновенно заряжает энергию до максимума."),
    "coins": ("💰 5000 монет", 10, "Пачка из 5000 игровых монет."),
    "boost": ("🚀 Буст x2 на 24ч", 15, "+1 монета за каждый тап на 24 часа."),
    "vip": ("👑 VIP навсегда", 99, "+2 монеты за тап, энергия до 200, значок 👑 в топе."),
}


@router.callback_query(F.data == "shop")
async def shop(cb: CallbackQuery):
    rows = [[btn(f"{v[0]} — {v[1]}⭐", f"buy:{k}")] for k, v in ITEMS.items()]
    rows.append(MENU_BTN)
    await show(
        cb,
        "⭐ МАГАЗИН\n\nОплата Telegram Stars. Выбери товар:",
        InlineKeyboardMarkup(inline_keyboard=rows),
    )


@router.callback_query(F.data.startswith("buy:"))
async def buy(cb: CallbackQuery):
    key = cb.data[4:]
    if key not in ITEMS:
        await cb.answer()
        return
    u = get_user(cb.from_user.id, cb.from_user.full_name)
    if key == "vip" and u["vip"]:
        await cb.answer("У тебя уже есть VIP 👑", show_alert=True)
        return
    name, price, desc = ITEMS[key]
    await cb.answer()
    await bot.send_invoice(
        chat_id=cb.from_user.id,
        title=name,
        description=desc,
        payload=key,
        currency="XTR",
        prices=[LabeledPrice(label=name, amount=price)],
    )


@router.pre_checkout_query()
async def pre_checkout(q: PreCheckoutQuery):
    await q.answer(ok=True)


@router.message(F.successful_payment)
async def paid(m: Message):
    uid = m.from_user.id
    key = m.successful_payment.invoice_payload
    stars = m.successful_payment.total_amount
    u = get_user(uid, m.from_user.full_name)
    now = time.time()
    if key == "energy":
        db.execute("UPDATE users SET energy=?,energy_ts=? WHERE id=?", (max_energy(u), now, uid))
    elif key == "coins":
        db.execute("UPDATE users SET coins=coins+5000 WHERE id=?", (uid,))
    elif key == "boost":
        until = max(now, u["boost_until"]) + 86400
        db.execute("UPDATE users SET boost_until=? WHERE id=?", (until, uid))
    elif key == "vip":
        db.execute("UPDATE users SET vip=1,energy=200,energy_ts=? WHERE id=?", (now, uid))
    db.execute(
        "INSERT INTO payments(user_id,item,stars,ts) VALUES(?,?,?,?)", (uid, key, stars, now)
    )
    db.commit()
    await m.answer("✅ Оплата прошла, спасибо! Товар уже у тебя 🎉", reply_markup=main_kb())


@router.message(Command("paysupport"))
async def paysupport(m: Message):
    await m.answer(f"По вопросам оплаты и возвратов пиши: {SUPPORT}")


# ---------------------------------------------------------------- АДМИНКА (реклама)


def is_admin(m: Message):
    return ADMIN_ID and m.from_user.id == ADMIN_ID


@router.message(Command("stats"))
async def stats(m: Message):
    if not is_admin(m):
        return
    users = db.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    day = db.execute(
        "SELECT COUNT(*) FROM users WHERE joined>?", (time.time() - 86400,)
    ).fetchone()[0]
    stars, cnt = db.execute("SELECT COALESCE(SUM(stars),0),COUNT(*) FROM payments").fetchone()
    await m.answer(
        f"📊 Игроков: {users}\n🆕 За 24ч: {day}\n⭐ Заработано звёзд: {stars} ({cnt} покупок)"
    )


@router.message(Command("setad"))
async def setad(m: Message, command: CommandObject):
    if not is_admin(m):
        return
    text = (command.args or "").strip()
    if text.lower() in ("", "off"):
        set_setting("ad", "")
        await m.answer("Реклама в меню выключена.")
    else:
        set_setting("ad", text)
        await m.answer("Реклама в меню включена ✅")


@router.message(Command("broadcast"))
async def broadcast(m: Message, command: CommandObject):
    if not is_admin(m):
        return
    text = (command.args or "").strip()
    if not text:
        await m.answer("Использование: /broadcast текст рассылки")
        return
    ids = [r["id"] for r in db.execute("SELECT id FROM users").fetchall()]
    await m.answer(f"Рассылка на {len(ids)} игроков началась...")
    ok = 0
    for i in ids:
        try:
            await bot.send_message(i, text, reply_markup=main_kb())
            ok += 1
        except TelegramAPIError:
            pass
        await asyncio.sleep(0.05)
    await m.answer(f"Готово. Доставлено: {ok}/{len(ids)}")


# ---------------------------------------------------------------- ЗАПАСНОЙ ОБРАБОТЧИК


@router.message(F.text)
async def fallback(m: Message):
    u = get_user(m.from_user.id, m.from_user.full_name)
    await m.answer(menu_text(u), reply_markup=main_kb())


# ---------------------------------------------------------------- ЗАПУСК


async def health(request):
    return web.Response(text="ok")


async def main():
    global BOT_USERNAME
    app = web.Application()
    app.router.add_get("/", health)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", int(os.environ.get("PORT", "10000"))).start()

    BOT_USERNAME = (await bot.get_me()).username
    dp = Dispatcher()
    dp.include_router(router)
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
