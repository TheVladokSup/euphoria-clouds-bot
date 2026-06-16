import logging
import asyncio
import sqlite3
import random
import string
import json
import os
from contextlib import contextmanager
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery,
    InputMediaPhoto, FSInputFile
)
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.exceptions import TelegramBadRequest

# ==========================================
# НАСТРОЙКИ
# ==========================================
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не найден в переменных окружения!")
ADMIN_IDS = [5540080919, 7148060753, 1654526291]
MANAGER_USERNAME = "zxccskd"

# Относительные пути к изображениям (рекомендуется положить папку logos рядом со скриптом)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOGOS_DIR = os.path.join(BASE_DIR, "logos")

PATH_MAIN_MENU = os.path.join(LOGOS_DIR, "main_menu.png")
PATH_PROFILE = os.path.join(LOGOS_DIR, "profile.png")
PATH_ASSORTMENT = os.path.join(LOGOS_DIR, "assortment.png")
PATH_RECEIPT = os.path.join(LOGOS_DIR, "receipt.png")
PATH_HELP = os.path.join(LOGOS_DIR, "help.png")
PATH_CART = os.path.join(LOGOS_DIR, "cart.png")

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

last_cart_activity = {}
active_tasks = set()

# ==========================================
# БАЗА ДАННЫХ
# ==========================================
def init_db():
    conn = sqlite3.connect("shop.db")
    cursor = conn.cursor()

    cursor.execute('''
                   CREATE TABLE IF NOT EXISTS users
                   (
                       user_id
                       INTEGER
                       PRIMARY
                       KEY,
                       joined_date
                       TEXT,
                       notif_restock
                       INTEGER
                       DEFAULT
                       1,
                       notif_new_item
                       INTEGER
                       DEFAULT
                       1,
                       notif_new_order
                       INTEGER
                       DEFAULT
                       1
                   )
                   ''')

    cursor.execute('''
                   CREATE TABLE IF NOT EXISTS items
                   (
                       id
                       INTEGER
                       PRIMARY
                       KEY
                       AUTOINCREMENT,
                       category
                       TEXT,
                       name
                       TEXT,
                       price
                       REAL,
                       quantity
                       INTEGER
                   )
                   ''')

    cursor.execute('''
                   CREATE TABLE IF NOT EXISTS receipts
                   (
                       id
                       INTEGER
                       PRIMARY
                       KEY
                       AUTOINCREMENT,
                       receipt_number
                       TEXT
                       UNIQUE,
                       item_name
                       TEXT,
                       category
                       TEXT,
                       quantity
                       INTEGER,
                       total_amount
                       REAL,
                       payment_type
                       TEXT,
                       buyer_info
                       TEXT,
                       date_time
                       TEXT,
                       user_id
                       INTEGER
                       DEFAULT
                       NULL,
                       status
                       TEXT
                       DEFAULT
                       'Завершен',
                       cart_data
                       TEXT
                       DEFAULT
                       NULL
                   )
                   ''')

    # Динамическое обновление старых баз данных
    try:
        cursor.execute("ALTER TABLE receipts ADD COLUMN user_id INTEGER DEFAULT NULL")
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute("ALTER TABLE receipts ADD COLUMN status TEXT DEFAULT 'Завершен'")
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute("ALTER TABLE receipts ADD COLUMN cart_data TEXT DEFAULT NULL")
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute("ALTER TABLE users ADD COLUMN notif_restock INTEGER DEFAULT 1")
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute("ALTER TABLE users ADD COLUMN notif_new_item INTEGER DEFAULT 1")
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute("ALTER TABLE users ADD COLUMN notif_new_order INTEGER DEFAULT 1")
    except sqlite3.OperationalError:
        pass

    conn.commit()
    conn.close()

init_db()

# ==========================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ И ФОРМАТИРОВАНИЕ
# ==========================================
def format_num(val) -> str:
    if val is None:
        return "0"
    try:
        num = float(val)
        if num.is_integer():
            return f"{int(num):,}".replace(",", " ")
        return f"{num:,.2f}".replace(",", " ")
    except (ValueError, TypeError):
        return str(val)

def format_datetime(dt_str) -> str:
    if not dt_str:
        return ""
    try:
        dt = datetime.strptime(str(dt_str).split(".")[0], "%Y-%m-%d %H:%M:%S")
        return dt.strftime("%d.%m.%Y, %H:%M")
    except ValueError:
        return str(dt_str)

def get_people_word(count: int) -> str:
    last_two_digits = count % 100
    last_digit = count % 10
    if 11 <= last_two_digits <= 14:
        return "человек"
    if last_digit == 1:
        return "человек"
    if 2 <= last_digit <= 4:
        return "человека"
    return "человек"

def get_user_count() -> int:
    conn = sqlite3.connect("shop.db")
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM users")
    count = cursor.fetchone()[0]
    conn.close()
    return count

async def broadcast_notification(text: str, column_filter: str = None):
    conn = sqlite3.connect("shop.db")
    cursor = conn.cursor()
    if column_filter:
        cursor.execute(f"SELECT user_id FROM users WHERE {column_filter} = 1")
    else:
        cursor.execute("SELECT user_id FROM users")
    users = cursor.fetchall()
    conn.close()

    for user in users:
        try:
            await bot.send_message(chat_id=user[0], text=text, parse_mode="HTML")
            await asyncio.sleep(0.05)
        except Exception as e:
            logging.error(f"Ошибка рассылки пользователю {user[0]}: {e}")

# ==========================================
# УМНЫЙ ДИНАМИЧЕСКИЙ ИНТЕРФЕЙС С ИЗОБРАЖЕНИЯМИ
# ==========================================
async def update_user_menu(callback: CallbackQuery, photo_path: str, caption: str, reply_markup=None,
                           parse_mode="Markdown"):
    photo = FSInputFile(photo_path)
    if callback.message.photo:
        try:
            await callback.message.edit_media(
                media=InputMediaPhoto(media=photo, caption=caption, parse_mode=parse_mode),
                reply_markup=reply_markup
            )
        except Exception:
            try:
                await callback.message.delete()
            except Exception:
                pass
            await callback.message.answer_photo(
                photo=photo,
                caption=caption,
                parse_mode=parse_mode,
                reply_markup=reply_markup
            )
    else:
        try:
            await callback.message.delete()
        except Exception:
            pass
        await callback.message.answer_photo(
            photo=photo,
            caption=caption,
            parse_mode=parse_mode,
            reply_markup=reply_markup
        )

async def update_menu_text(callback: CallbackQuery, text: str, reply_markup=None, parse_mode="Markdown"):
    if callback.message.photo:
        try:
            await callback.message.edit_caption(caption=text, reply_markup=reply_markup, parse_mode=parse_mode)
        except TelegramBadRequest as e:
            if "message is not modified" in str(e):
                await callback.answer()
            else:
                raise e
    else:
        try:
            await callback.message.edit_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
        except TelegramBadRequest as e:
            if "message is not modified" in str(e):
                await callback.answer()
            else:
                raise e


# ==========================================
# СОСТОЯНИЯ (FSM)
# ==========================================
class AdminStates(StatesGroup):
    choosing_category_for_add = State()
    entering_name = State()
    entering_price = State()
    entering_quantity = State()
    choosing_item_for_edit = State()
    entering_new_quantity = State()
    cashbox_choosing_item = State()
    cashbox_entering_quantity = State()
    cashbox_entering_amount = State()
    cashbox_entering_buyer = State()


class UserStates(StatesGroup):
    entering_receipt_code = State()
    browsing_catalog = State()


# ==========================================
# КЛАВИАТУРЫ ПОЛЬЗОВАТЕЛЯ
# ==========================================
def get_main_menu(cart_count: int = 0):
    cart_text = f"🛒 Корзина ({format_num(cart_count)})" if cart_count > 0 else "🛒 Корзина"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="👤 Мой профиль", callback_data="user_profile")],
            [InlineKeyboardButton(text="🛍 Ассортимент", callback_data="user_assortment")],
            [InlineKeyboardButton(text="🧾 Активация чека", callback_data="user_enter_receipt"),
             InlineKeyboardButton(text="❓ Помощь", callback_data="user_help")],
            [InlineKeyboardButton(text=cart_text, callback_data="view_cart")]
        ]
    )


def get_assortment_menu(cart_count: int = 0):
    cart_text = f"🛒 Посмотреть корзину ({format_num(cart_count)})" if cart_count > 0 else "🛒 Корзина"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💨 Жижи", callback_data="user_cat_Жижи"),
             InlineKeyboardButton(text="🩹 Снюс", callback_data="user_cat_Снюс")],
            [InlineKeyboardButton(text="📦 Расходники", callback_data="user_cat_Расходники"),
             InlineKeyboardButton(text="🔋 Одноразки", callback_data="user_cat_Одноразки")],
            [InlineKeyboardButton(text="🛠 Услуги", callback_data="user_cat_Услуги")],
            [InlineKeyboardButton(text=cart_text, callback_data="view_cart")],
            [InlineKeyboardButton(text="↩️️ В главное меню", callback_data="to_main_menu")]
        ]
    )

def get_back_to_menu_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="↩️ В главное меню", callback_data="to_main_menu")]]
    )

def get_profile_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔔 Настройка уведомлений", callback_data="user_notif_settings")],
            [InlineKeyboardButton(text="↩️ В главное меню", callback_data="to_main_menu")]
        ]
    )

def get_notif_settings_keyboard(user_id: int) -> InlineKeyboardMarkup:
    conn = sqlite3.connect("shop.db")
    cursor = conn.cursor()
    cursor.execute("SELECT notif_new_item, notif_restock FROM users WHERE user_id = ?", (user_id,))
    res = cursor.fetchone()
    conn.close()

    n_new, n_restock = res if res else (1, 1)

    buttons = [
        [InlineKeyboardButton(text=f"{'🔔' if n_new == 1 else '🔕'} Новинки ассортимента",
                              callback_data="toggle_notif_notif_new_item")],
        [InlineKeyboardButton(text=f"{'🔔' if n_restock == 1 else '🔕'} Рестоки (поступление товара)",
                              callback_data="toggle_notif_notif_restock")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="user_profile")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# ==========================================
# ХЕНДЛЕРЫ ПОЛЬЗОВАТЕЛЬСКОГО ИНТЕРФЕЙСА & КОРЗИНЫ
# ==========================================

@dp.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    await state.set_state(None)
    user_id = message.from_user.id
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    conn = sqlite3.connect("shop.db")
    cursor = conn.cursor()
    cursor.execute("INSERT OR IGNORE INTO users (user_id, joined_date) VALUES (?, ?)", (user_id, now_str))
    conn.commit()
    conn.close()

    total_users = get_user_count()
    word = get_people_word(total_users)

    fsm_data = await state.get_data()
    cart = fsm_data.get("cart", {})
    cart_count = sum(cart.values())

    last_cart_activity.pop(user_id, None)

    photo = FSInputFile(PATH_MAIN_MENU)
    await message.answer_photo(
        photo=photo,
        caption=f"👋 Привет, {message.from_user.first_name}! Добро пожаловать в Euphoria Clouds!\n\n"
                f"📈 Нашим магазином пользуются уже <b>{format_num(total_users)}</b> {word}, спасибо вам!\n\n"
                f"Выбери нужный раздел меню ниже 👇",
        reply_markup=get_main_menu(cart_count),
        parse_mode="HTML"
    )


@dp.callback_query(F.data == "to_main_menu")
async def back_to_menu(callback: CallbackQuery, state: FSMContext):
    await state.set_state(None)
    fsm_data = await state.get_data()
    cart = fsm_data.get("cart", {})
    cart_count = sum(cart.values())

    await update_user_menu(
        callback=callback,
        photo_path=PATH_MAIN_MENU,
        caption=f"👋 С возращением в магазин Euphoria Clouds!\n\n"
                f"Выбери нужный раздел меню ниже 👇",
        reply_markup=get_main_menu(cart_count),
        parse_mode=None
    )


@dp.callback_query(F.data == "user_assortment")
async def user_assortment_channels(callback: CallbackQuery, state: FSMContext):
    await state.set_state(UserStates.browsing_catalog)
    fsm_data = await state.get_data()
    cart = fsm_data.get("cart", {})
    cart_count = sum(cart.values())

    await update_user_menu(
        callback=callback,
        photo_path=PATH_ASSORTMENT,
        caption="🛍 **Ассортимент**\n\nДобавляй товары в корзину с помощью кнопок под ними:",
        reply_markup=get_assortment_menu(cart_count),
        parse_mode="Markdown"
    )


@dp.callback_query(F.data.startswith("user_cat_"))
async def show_inline_category_items(callback: CallbackQuery, state: FSMContext, category: str = None):
    if category is None:
        category = callback.data.replace("user_cat_", "")

    await state.set_state(UserStates.browsing_catalog)

    fsm_data = await state.get_data()
    cart = fsm_data.get("cart", {})

    conn = sqlite3.connect("shop.db")
    cursor = conn.cursor()
    cursor.execute("SELECT id, name, price, quantity FROM items WHERE category = ?", (category,))
    items = cursor.fetchall()
    conn.close()

    if not items:
        await callback.answer(f"❌ Временно нет в наличии!", show_alert=True)
        return

    keyboard_buttons = []
    response = f"🛍 **Раздел: {category}**\n\n"

    for item in items:
        item_id, name, price, stock = item
        in_cart = cart.get(str(item_id), 0)

        status = f"{format_num(stock)} шт." if stock > 0 else "❌ Нет в наличии"
        response += f"🔹 **{name}**\nЦена: {format_num(price)} ₽ | Наличие: {status}\n"
        if in_cart > 0:
            response += f"👉 _В твоей корзине:_ *{format_num(in_cart)} шт.*\n"
        response += "\n"

        if stock > 0:
            keyboard_buttons.append([
                InlineKeyboardButton(text=f"➖ {name}", callback_data=f"cart_minus_{item_id}_{category}"),
                InlineKeyboardButton(text=f"➕ {name}", callback_data=f"cart_plus_{item_id}_{category}")
            ])
        else:
            keyboard_buttons.append([
                InlineKeyboardButton(text=f"🚫 {name} (нет в наличии)", callback_data=f"out_of_stock_{item_id}")
            ])

    # Изменение 1: Кнопка корзины располагается выше кнопки "К категориям"
    total_items_in_cart = sum(cart.values())
    if total_items_in_cart > 0:
        keyboard_buttons.append([
            InlineKeyboardButton(text=f"🛒 Посмотреть корзину ({format_num(total_items_in_cart)})",
                                 callback_data="view_cart")
        ])

    keyboard_buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="user_assortment")])

    await update_menu_text(
        callback=callback,
        text=response + "💡 Нажимай ➕ или ➖, чтобы собрать свой заказ.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard_buttons),
        parse_mode="Markdown"
    )


@dp.callback_query(F.data.startswith("cart_"))
async def handle_cart_change(callback: CallbackQuery, state: FSMContext):
    parts = callback.data.split("_")
    action = parts[1]
    item_id = parts[2]
    category = parts[3]

    conn = sqlite3.connect("shop.db")
    cursor = conn.cursor()
    cursor.execute("SELECT name, price, quantity FROM items WHERE id = ?", (item_id,))
    item = cursor.fetchone()
    conn.close()

    if not item:
        await callback.answer("❌ Ошибка: товар не найден на складе!", show_alert=True)
        return

    name, price, stock = item
    fsm_data = await state.get_data()
    cart = fsm_data.get("cart", {})
    current_qty = cart.get(str(item_id), 0)

    if action == "plus":
        if current_qty >= stock:
            await callback.answer(f"🙅‍♂️ Нельзя добавить больше!\n\nНа складе осталось всего {format_num(stock)} шт.",
                                  show_alert=True)
            return
        cart[str(item_id)] = current_qty + 1
        await callback.answer(f"Добавлено: {name}")
    elif action == "minus":
        if current_qty > 0:
            cart[str(item_id)] = current_qty - 1
            if cart[str(item_id)] == 0:
                del cart[str(item_id)]
            await callback.answer(f"Удалено: {name}")
        else:
            await callback.answer("❌ Этого товара и так нет в твоей корзине!", show_alert=True)
            return

    await state.update_data(cart=cart)

    if cart:
        last_cart_activity[callback.from_user.id] = datetime.now()
    else:
        last_cart_activity.pop(callback.from_user.id, None)

    await show_inline_category_items(callback, state, category=category)


@dp.callback_query(F.data.startswith("out_of_stock_"))
async def out_of_stock_handler(callback: CallbackQuery):
    item_id = callback.data.replace("out_of_stock_", "")
    conn = sqlite3.connect("shop.db")
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM items WHERE id = ?", (item_id,))
    item = cursor.fetchone()
    conn.close()

    name = item[0] if item else "Данный товар"
    await callback.answer(
        f"❌ На данный момент позиции «{name}» сейчас нет в наличии!\n\nСледите за обновлениями бота, скоро будет ресток!",
        show_alert=True)


@dp.callback_query(F.data == "view_cart")
async def view_user_cart(callback: CallbackQuery, state: FSMContext):
    fsm_data = await state.get_data()
    cart = fsm_data.get("cart", {})

    if not cart:
        await callback.answer("❌ Твоя корзина пока пуста!", show_alert=True)
        return

    conn = sqlite3.connect("shop.db")
    cursor = conn.cursor()

    cart_text = "🛒 **Твоя корзина:**\n\n"
    total_cost = 0.0

    for item_id, qty in cart.items():
        cursor.execute("SELECT name, price FROM items WHERE id = ?", (item_id,))
        item = cursor.fetchone()
        if item:
            name, price = item
            cost = price * qty
            total_cost += cost
            cart_text += f"▪️ **{name}**\n   {format_num(qty)} шт. × {format_num(price)} ₽ = *{format_num(cost)} ₽*\n\n"

    conn.close()

    cart_text += f"━━━━━━━━━━\n"
    cart_text += f"💰 **Итого к оплате: {format_num(total_cost)} ₽**\n\n"
    cart_text += "✨ Нажми кнопку ниже, чтобы отправить заказ на подтверждение менеджеру."

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Оформить заказ и оплатить", callback_data="checkout_order")],
        [InlineKeyboardButton(text="🗑 Очистить корзину", callback_data="clear_cart")],
        [InlineKeyboardButton(text="⬅️ Вернуться в каталог", callback_data="user_assortment")]
    ])

    await update_user_menu(
        callback=callback,
        photo_path=PATH_CART,
        caption=cart_text,
        reply_markup=keyboard,
        parse_mode="Markdown"
    )


@dp.callback_query(F.data == "clear_cart")
async def clear_cart_handler(callback: CallbackQuery, state: FSMContext):
    await state.update_data(cart={})
    last_cart_activity.pop(callback.from_user.id, None)
    await callback.answer("Корзина очищена")

    await update_user_menu(
        callback=callback,
        photo_path=PATH_MAIN_MENU,
        caption="👋 Добро пожаловать в EuphoriaClouds!\n\nВыбери нужный раздел меню ниже 👇",
        reply_markup=get_main_menu(0),
        parse_mode=None
    )


# =======================================================
# СИСТЕМА CRM: ОТПРАВКА ЗАКАЗА НА РАССМОТРЕНИЕ АДМИНАМ
# =======================================================
@dp.callback_query(F.data == "checkout_order")
async def checkout_order_handler(callback: CallbackQuery, state: FSMContext):
    fsm_data = await state.get_data()
    cart = fsm_data.get("cart", {})

    if not cart:
        await callback.answer("🛒 Твоя корзина пуста!", show_alert=True)
        return

    buyer_id = callback.from_user.id

    one_hour_ago = (datetime.now() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    conn = sqlite3.connect("shop.db")
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM receipts WHERE user_id = ? AND date_time > ?", (buyer_id, one_hour_ago))
    recent_orders_count = cursor.fetchone()[0]
    conn.close()

    if recent_orders_count >= 3:
        await callback.answer(
            "🚫 Превышен лимит заказов!\n\nТы не можешь отправлять более 3 заказов в час. Пожалуйста, подожди немного.",
            show_alert=True
        )
        return

    buyer_name = callback.from_user.first_name
    buyer_username = f"@{callback.from_user.username}" if callback.from_user.username else "Нет юзернейма"

    order_num = f"ORD-" + ''.join(random.choices(string.ascii_uppercase + string.digits, k=5))
    now_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    conn = sqlite3.connect("shop.db")
    cursor = conn.cursor()

    order_details = ""
    db_items_summary = []
    total_cost = 0.0
    total_qty = 0

    for item_id, qty in cart.items():
        cursor.execute("SELECT name, price, quantity FROM items WHERE id = ?", (item_id,))
        item = cursor.fetchone()
        if item:
            name, price, stock = item

            if stock < qty:
                await callback.answer(
                    f"❌ Ошибка наличия!\n\nТовара «{name}» осталось всего {stock} шт. Измени состав корзины!",
                    show_alert=True)
                conn.close()
                return

            cost = price * qty
            total_cost += cost
            total_qty += qty
            order_details += f"• {name} — {format_num(qty)} шт. ({format_num(cost)} ₽)\n"
            db_items_summary.append(f"{name} ({qty} шт.)")

    db_items_string = ", ".join(db_items_summary)

    cursor.execute('''
                   INSERT INTO receipts (receipt_number, item_name, category, quantity, total_amount, payment_type,
                                         buyer_info, date_time, user_id, status, cart_data)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ''', (order_num, db_items_string, "Корзина", total_qty, total_cost,
                         "Online", f"{buyer_name} ({buyer_username})", now_time, buyer_id, "В обработке",
                         json.dumps(cart)))

    conn.commit()

    manager_msg = (
        f"🚨 **Поступил новый заказ!**\n\n"
        f"🆔 **Номер заказа:** `{order_num}`\n"
        f"👤 **Покупатель:** {buyer_name} ({buyer_username})\n"
        f"🆔 **ID Клиента:** `{buyer_id}`\n"
        f"━━━━━━━━━━\n"
        f"📦 **Состав заказа:**\n{order_details}"
        f"━━━━━━━━━━\n"
        f"💰 **Сумма:** {format_num(total_cost)} ₽\n\n"
        f"⏳ _Заказ ожидает решения в управлении заказами!_"
    )

    crm_keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Принять", callback_data=f"crm_confirm_{order_num}"),
            InlineKeyboardButton(text="❌ Отклонить", callback_data=f"crm_reject_{order_num}")
        ]
    ])

    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(chat_id=admin_id, text=manager_msg, parse_mode="Markdown",
                                   reply_markup=crm_keyboard)
        except Exception as e:
            logging.error(f"Не удалось отправить уведомление администратору {admin_id}: {e}")

    conn.close()

    client_msg = (
        f"⏳ **Твой заказ `{order_num}` успешно отправлен на проверку!**\n\n"
        f"📋 **Состав заказа:**\n{order_details}\n"
        f"💰 **Итоговая сумма:** *{format_num(total_cost)} ₽*\n\n"
        f"✨ Менеджер уже проверяет наличие позиций на складе. Статус заказа можно отслеживать в меню «👤 Мой профиль».\n"
        f"🔔 Как только заказ одобрят, бот сразу же пришлет уведомление!"
    )

    await state.update_data(cart={})
    last_cart_activity.pop(buyer_id, None)

    await callback.answer("✅ Заказ отправлен администраторам!", show_alert=False)

    await update_user_menu(
        callback=callback,
        photo_path=PATH_MAIN_MENU,
        caption=client_msg,
        reply_markup=get_back_to_menu_keyboard(),
        parse_mode="Markdown"
    )

# ==========================================
# КЛАВИАТУРЫ АДМИНКИ
# ==========================================
def get_admin_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📁 Управление заказами", callback_data="admin_crm")],
        [
            InlineKeyboardButton(text="📦 Товары", callback_data="admin_goods"),
            InlineKeyboardButton(text="💰 Касса", callback_data="admin_cashbox"),
            InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")
        ],
        [InlineKeyboardButton(text="👤 В меню клиента", callback_data="to_main_menu")]
    ])

def get_admin_goods_menu():
    # Изменение 2: Расположение кнопок "Добавить" и "Изменить количество" в одну строку, "Удалить" снизу. Добавлен склад.
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="➕ Добавить позицию", callback_data="admin_add"),
            InlineKeyboardButton(text="✏️ Изменить количество", callback_data="admin_edit_qty")
        ],
        [InlineKeyboardButton(text="📋 Просмотр склада", callback_data="admin_view_stock")],
        [InlineKeyboardButton(text="❌ Удалить позицию", callback_data="admin_delete")],
        [InlineKeyboardButton(text="↩️ В главное меню", callback_data="to_admin_menu")]
    ])

def get_cashbox_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🧾 Сформировать новый чек", callback_data="cash_create")],
        [InlineKeyboardButton(text="📜 История чеков", callback_data="admin_receipts_history")],
        [InlineKeyboardButton(text="↩️ В главное меню", callback_data="to_admin_menu")]
    ])

def get_back_to_admin_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="↩️️ В главное меню", callback_data="to_admin_menu")]])

def get_categories_keyboard():
    categories = ["💨 Жижи", "🩹 Снюс", "📦 Расходники", "🔋 Одноразки", "🛠 Услуги"]
    buttons = [[InlineKeyboardButton(text=cat, callback_data=f"adm_cat_{cat}")] for cat in categories]
    buttons.append([InlineKeyboardButton(text="❌ Отмена", callback_data="admin_goods")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_items_inline_keyboard(action_prefix):
    conn = sqlite3.connect("shop.db")
    cursor = conn.cursor()
    cursor.execute("SELECT id, name, category, quantity FROM items")
    items = cursor.fetchall()
    conn.close()

    buttons = []
    for item in items:
        buttons.append([InlineKeyboardButton(text=f"[{item[2]}] {item[1]} ({format_num(item[3])} шт.)",
                                             callback_data=f"{action_prefix}_{item[0]}")])

    if action_prefix == "cashitem":
        buttons.append([InlineKeyboardButton(text="↩️ В главное меню", callback_data="admin_cashbox")])
    else:
        buttons.append([InlineKeyboardButton(text="↩️ В главное меню", callback_data="admin_goods")])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_cancel_goods_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="admin_goods")]])


def get_cancel_cashbox_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="admin_cashbox")]])


# ==========================================
# ХЕНДЛЕРЫ КОРНЕВОЙ АДМИН-ПАНЕЛИ
# ==========================================
@dp.message(Command("admin"))
async def cmd_admin(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS: return
    await state.clear()
    await message.answer("🔑 Панель управления магазином\n\nВыбери нужный раздел:", reply_markup=get_admin_menu(),
                         parse_mode="Markdown")


@dp.callback_query(F.data == "to_admin_menu")
async def back_to_admin(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS: return
    await state.clear()
    try:
        await callback.message.edit_text("🔑 Панель управления магазином\n\nВыбери нужный раздел:",
                                         reply_markup=get_admin_menu(), parse_mode="Markdown")
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            await callback.answer()
        else:
            raise e

@dp.callback_query(F.data == "admin_goods")
async def admin_goods_main(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS: return
    await state.clear()
    try:
        await callback.message.edit_text("📦 **Управление складом и товарами**\n\nВыбери нужное действие:",
                                         reply_markup=get_admin_goods_menu(), parse_mode="Markdown")
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            await callback.answer()
        else:
            raise e

@dp.callback_query(F.data == "admin_cashbox")
async def admin_cashbox_main(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS: return
    await state.clear()
    try:
        await callback.message.edit_text("💰 **Виртуальная касса (офлайн-продажи)**:", reply_markup=get_cashbox_menu(),
                                         parse_mode="Markdown")
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            await callback.answer()
        else:
            raise e

@dp.callback_query(F.data == "admin_view_stock")
async def admin_view_stock_handler(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS: return

    conn = sqlite3.connect("shop.db")
    cursor = conn.cursor()
    cursor.execute("SELECT category, name, price, quantity FROM items ORDER BY category, name")
    items = cursor.fetchall()
    conn.close()

    if not items:
        await callback.answer("❌ Склад пуст!", show_alert=True)
        return

    stock_text = "📋 Текущее состояние склада:\n"
    current_cat = ""

    for cat, name, price, qty in items:
        if cat != current_cat:
            current_cat = cat
            stock_text += f"\n📂 **Раздел: {current_cat}**\n"
            stock_text += "━━━━━━━━━━\n"

        status = f"🟢 {format_num(qty)} шт." if qty > 0 else "🔴 Нет в наличии!"
        stock_text += f"▪️ **{name}** — {format_num(price)} ₽ | {status}\n"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="↩️️ В главное меню", callback_data="admin_goods")]
    ])

    try:
        await callback.message.edit_text(stock_text, reply_markup=kb, parse_mode="Markdown")
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            await callback.answer()
        else:
            raise e


# =======================================================
# СИСТЕМА CRM: ПОДМЕНЮ, СПИСКИ И АРХИВАЦИЯ
# =======================================================
@dp.callback_query(F.data == "admin_crm")
async def admin_crm_menu(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS: return

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="⏳ Новые", callback_data="crm_list_processing"),
            InlineKeyboardButton(text="📦 Ожидают оплаты", callback_data="crm_list_accepted")
        ],
        [InlineKeyboardButton(text="🗄 Архив сделок (история)", callback_data="crm_list_archive")],
        [InlineKeyboardButton(text="↩️ В главное меню", callback_data="to_admin_menu")]
    ])
    try:
        await callback.message.edit_text("📥 Управление заказами:\n",
                                         reply_markup=keyboard)
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            await callback.answer()
        else:
            raise e


@dp.callback_query(F.data.startswith("crm_list_"))
async def admin_crm_list_view(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS: return
    list_type = callback.data.replace("crm_list_", "")

    conn = sqlite3.connect("shop.db")
    cursor = conn.cursor()

    if list_type == "processing":
        title = "⏳ Новые заказы:"
        cursor.execute(
            "SELECT receipt_number, total_amount, buyer_info FROM receipts WHERE status = 'В обработке' ORDER BY id DESC")
    elif list_type == "accepted":
        title = "📦 Ожидают оплаты/выдачи:"
        cursor.execute(
            "SELECT receipt_number, total_amount, buyer_info FROM receipts WHERE status = 'Принят' ORDER BY id DESC")
    elif list_type == "archive":
        title = "🗄 Архив сделок:"
        cursor.execute(
            "SELECT receipt_number, total_amount, buyer_info, status FROM receipts WHERE status IN ('Оплачен', 'Отклонен', 'Завершен') ORDER BY id DESC")
    else:
        conn.close()
        return

    orders = cursor.fetchall()
    conn.close()

    if not orders:
        try:
            await callback.message.edit_text(f"{title}\n\n❌ В этой категории пока нет записей!",
                                             reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                                                 [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_crm")]]))
        except TelegramBadRequest as e:
            if "message is not modified" in str(e):
                await callback.answer()
            else:
                raise e
        return

    buttons = []
    for order in orders:
        if list_type == "archive":
            r_num, amount, buyer, status = order
            name_part = buyer.split(" (")[0]
            status_icon = "✅" if status in ("Оплачен", "Завершен") else "❌"
            buttons.append([InlineKeyboardButton(text=f"{status_icon} {r_num} — {format_num(amount)} ₽ ({name_part})",
                                                 callback_data=f"crm_view_{r_num}")])
        else:
            r_num, amount, buyer = order
            name_part = buyer.split(" (")[0]
            icon = "⏳" if list_type == "processing" else "📦"
            buttons.append([InlineKeyboardButton(text=f"{icon} {r_num} — {format_num(amount)} ₽ ({name_part})",
                                                 callback_data=f"crm_view_{r_num}")])

    buttons.append([InlineKeyboardButton(text="↩️ В главное меню", callback_data="admin_crm")])
    try:
        await callback.message.edit_text(f"{title}\n\nВыберите заказ для просмотра детализации:",
                                         reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            await callback.answer()
        else:
            raise e


@dp.callback_query(F.data.startswith("crm_view_"))
async def admin_crm_view_single_order(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS: return
    order_num = callback.data.replace("crm_view_", "")

    conn = sqlite3.connect("shop.db")
    cursor = conn.cursor()
    cursor.execute(
        "SELECT receipt_number, item_name, total_amount, buyer_info, date_time, status FROM receipts WHERE receipt_number = ?",
        (order_num,))
    order = cursor.fetchone()
    conn.close()

    if not order:
        await callback.answer("⚠️ Ошибка: Заказ не найден в базе данных!", show_alert=True)
        return

    r_num, items_str, amount, buyer, dt, status = order
    formatted_items = "\n".join([f"• {i.strip()}" for i in items_str.split(",")])

    msg = (
        f"📋 **Карточка заказа `{r_num}`**\n\n"
        f"⚙️ **Текущий статус:** `{status}`\n"
        f"👤 **Покупатель:** {buyer}\n"
        f"📅 **Создан:** {format_datetime(dt)}\n"
        f"━━━━━━━━━━\n"
        f"📦 **Состав заказа:**\n{formatted_items}\n"
        f"━━━━━━━━━━\n"
        f"💰 **Сумма:** {format_num(amount)} ₽"
    )

    inline_keyboard = []

    if status == "В обработке":
        inline_keyboard.append([
            InlineKeyboardButton(text="✅ Принять заказ", callback_data=f"crm_confirm_{r_num}"),
            InlineKeyboardButton(text="❌ Отклонить", callback_data=f"crm_reject_{r_num}")
        ])
        back_target = "crm_list_processing"
    elif status == "Принят":
        inline_keyboard.append([
            InlineKeyboardButton(text="💳 Отметить оплаченным", callback_data=f"crm_pay_{r_num}"),
            InlineKeyboardButton(text="❌ Отклонить заказ", callback_data=f"crm_reject_{r_num}")
        ])
        back_target = "crm_list_accepted"
    else:
        back_target = "crm_list_archive"

    inline_keyboard.append([InlineKeyboardButton(text="⬅️ Назад к списку", callback_data=back_target)])
    try:
        await callback.message.edit_text(msg, reply_markup=InlineKeyboardMarkup(inline_keyboard=inline_keyboard),
                                         parse_mode="Markdown")
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            await callback.answer()
        else:
            raise e


@dp.callback_query(F.data.startswith("crm_confirm_"))
async def crm_confirm_handler(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS: return
    order_num = callback.data.replace("crm_confirm_", "")

    conn = sqlite3.connect("shop.db")
    cursor = conn.cursor()
    cursor.execute("SELECT status, cart_data, user_id, total_amount FROM receipts WHERE receipt_number = ?",
                   (order_num,))
    order = cursor.fetchone()

    if not order:
        await callback.answer("⚠️ Критическая ошибка!\n\nЗаказ не найден в базе данных.", show_alert=True)
        conn.close()
        return

    status, cart_data, user_id, total_amount = order
    if status != "В обработке":
        await callback.answer(f"🚫 Действие невозможно!\n\nЗаказ уже обработан. Статус: {status}", show_alert=True)
        conn.close()
        return

    cart = json.loads(cart_data) if cart_data else {}

    for item_id, qty in cart.items():
        cursor.execute("SELECT name, quantity FROM items WHERE id = ?", (item_id,))
        item = cursor.fetchone()
        if not item or item[1] < qty:
            name = item[0] if item else f"Товар ID {item_id}"
            stock = item[1] if item else 0
            await callback.answer(
                f"❌ Списание невозможно!\n\nТовара «{name}» на складе {stock} шт., а в заказе требуется {qty} шт.",
                show_alert=True)
            conn.close()
            return

    for item_id, qty in cart.items():
        cursor.execute("UPDATE items SET quantity = quantity - ? WHERE id = ?", (qty, item_id))

    cursor.execute("UPDATE receipts SET status = 'Принят' WHERE receipt_number = ?", (order_num,))
    conn.commit()
    conn.close()

    await callback.answer("Заказ подтвержден!")

    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="⬅️ Вернуться в управление заказами", callback_data="admin_crm")]])
    try:
        await callback.message.edit_text(
            f"✅ Заказ {order_num} успешно подтвержден!\n\nТовары списаны со склада, заказ переведен в список принятых. Клиенту отправлено уведомление о готовности к оплате.",
            reply_markup=kb)
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            pass
        else:
            raise e

    try:
        await bot.send_message(
            chat_id=user_id,
            text=f"🎉 Твой заказ {order_num} одобрен менеджером и ожидает оплаты!\n\n"
                 f"💰 Сумма к оплате: *{format_num(total_amount)} ₽*\n\n"
                 f"💬 Свяжитесь с менеджером для оплаты и получения: @{MANAGER_USERNAME}",
            parse_mode="Markdown"
        )
    except Exception as e:
        logging.error(f"Не удалось отправить уведомление клиенту {user_id}: {e}")

@dp.callback_query(F.data.startswith("crm_pay_"))
async def crm_pay_handler(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS: return
    order_num = callback.data.replace("crm_pay_", "")

    conn = sqlite3.connect("shop.db")
    cursor = conn.cursor()
    cursor.execute("SELECT status, user_id, total_amount FROM receipts WHERE receipt_number = ?", (order_num,))
    order = cursor.fetchone()

    if not order:
        await callback.answer("⚠️ Ошибка: Заказ не найден!", show_alert=True)
        conn.close()
        return

    status, user_id, total_amount = order
    if status != "Принят":
        await callback.answer("🚫 Некорректный статус!\n\nПометить оплаченным можно только принятый заказ.",
                              show_alert=True)
        conn.close()
        return

    cursor.execute("UPDATE receipts SET status = 'Оплачен' WHERE receipt_number = ?", (order_num,))
    conn.commit()
    conn.close()

    await callback.answer("Заказ оплачен!")
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="⬅️ Вернуться в CRM", callback_data="admin_crm")]])
    try:
        await callback.message.edit_text(
            f"💳 Заказ {order_num} успешно помечена оплаченной!\n\nСделка завершена и перенесена в Архив.",
            reply_markup=kb)
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            pass
        else:
            raise e

    try:
        await bot.send_message(
            chat_id=user_id,
            text=f"✅ Твой заказ {order_num} на сумму {format_num(total_amount)} ₽ успешно оплачен!\n\n"
                 f"✨ Спасибо за покупку! Ждем тебя снова в нашем магазине!",
            parse_mode="Markdown"
        )
    except Exception as e:
        logging.error(f"Не удалось отправить уведомление клиенту {user_id}: {e}")


@dp.callback_query(F.data.startswith("crm_reject_"))
async def crm_reject_handler(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS: return
    order_num = callback.data.replace("crm_reject_", "")

    conn = sqlite3.connect("shop.db")
    cursor = conn.cursor()
    cursor.execute("SELECT status, user_id, cart_data FROM receipts WHERE receipt_number = ?", (order_num,))
    order = cursor.fetchone()

    if not order:
        await callback.answer("⚠️ Ошибка: Заказ не найден.", show_alert=True)
        conn.close()
        return

    status, user_id, cart_data = order
    if status in ("Отклонен", "Оплачен", "Завершен"):
        await callback.answer(f"🚫 Отмена невозможна!\n\nНельзя отменить сделку со статусом: {status}", show_alert=True)
        conn.close()
        return

    restored_to_stock = False

    if status == "Принят":
        cart = json.loads(cart_data) if cart_data else {}
        for item_id, qty in cart.items():
            cursor.execute("UPDATE items SET quantity = quantity + ? WHERE id = ?", (qty, item_id))
        restored_to_stock = True

    cursor.execute("UPDATE receipts SET status = 'Отклонен' WHERE receipt_number = ?", (order_num,))
    conn.commit()
    conn.close()

    await callback.answer("Заказ отклонен")
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_crm")]])

    info_msg = f"❌ Заказ {order_num} был отклонен и отправлен в архив.\n"
    if restored_to_stock:
        info_msg += "📦 Товары успешно возвращены обратно на склад!\n"
    info_msg += "Клиент получил уведомление."

    try:
        await callback.message.edit_text(info_msg, reply_markup=kb)
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            pass
        else:
            raise e

    try:
        await bot.send_message(
            chat_id=user_id,
            text=f"❌ К сожалению, твой заказ {order_num} был отклонен менеджером.\n\n"
                 f"❓ Ты можешь написать в поддержку @{MANAGER_USERNAME}, чтобы уточнить детали.",
            parse_mode="Markdown"
        )
    except Exception as e:
        logging.error(f"Не удалось отправить уведомление клиенту {user_id}: {e}")

# ==========================================
# АДМИН-ЛОГИКА: ДОБАВЛЕНИЕ, РЕДАКТИРОВАНИЕ & УДАЛЕНИЕ
# ==========================================
@dp.callback_query(F.data == "admin_add")
async def admin_add_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS: return
    try:
        await callback.message.edit_text("Выбери категорию для нового товара:",
                                         reply_markup=get_categories_keyboard())
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            await callback.answer()
        else:
            raise e
    await state.set_state(AdminStates.choosing_category_for_add)


@dp.callback_query(AdminStates.choosing_category_for_add, F.data.startswith("adm_cat_"))
async def admin_add_category(callback: CallbackQuery, state: FSMContext):
    category = callback.data.replace("adm_cat_", "")
    await state.update_data(category=category)
    try:
        await callback.message.edit_text(f"Категория: *{category}*.\n\nВведи название товара:",
                                         parse_mode="Markdown",
                                         reply_markup=get_cancel_goods_keyboard())
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            await callback.answer()
        else:
            raise e
    await state.set_state(AdminStates.entering_name)


@dp.message(AdminStates.entering_name)
async def admin_add_name(message: Message, state: FSMContext):
    # Изменение 4: Проверка на существование одноименного товара
    name_input = message.text.strip()

    conn = sqlite3.connect("shop.db")
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM items WHERE LOWER(name) = LOWER(?)", (name_input,))
    existing_item = cursor.fetchone()
    conn.close()

    if existing_item:
        await message.answer(
            f"❌ Товар с названием «{name_input}» уже существует на складе!\n"
            f"Пожалуйста, укажи уникальное название:",
            reply_markup=get_cancel_goods_keyboard()
        )
        return

    await state.update_data(name=name_input)
    await message.answer("Введи цену товара (в рублях):", reply_markup=get_cancel_goods_keyboard())
    await state.set_state(AdminStates.entering_price)


@dp.message(AdminStates.entering_price)
async def admin_add_price(message: Message, state: FSMContext):
    try:
        price = float(message.text)
        await state.update_data(price=price)
        await message.answer("Введи стартовое количество товара на складе:",
                             reply_markup=get_cancel_goods_keyboard())
        await state.set_state(AdminStates.entering_quantity)
    except ValueError:
        await message.answer("Пожалуйста, введи корректное число для цены:", reply_markup=get_cancel_goods_keyboard())


@dp.message(AdminStates.entering_quantity)
async def admin_add_quantity(message: Message, state: FSMContext):
    try:
        quantity = int(message.text)
        data = await state.get_data()

        conn = sqlite3.connect("shop.db")
        cursor = conn.cursor()
        cursor.execute("INSERT INTO items (category, name, price, quantity) VALUES (?, ?, ?, ?)",
                       (data['category'], data['name'], data['price'], quantity))
        conn.commit()
        conn.close()

        await message.answer(f"✅ Товар **{data['name']}** успешно создан и доступен в каталоге!", parse_mode="Markdown",
                             reply_markup=get_admin_goods_menu())

        if quantity > 0:
            notif_text = (
                f"🔥 <b>У НАС ГОРЯЧАЯ НОВИНКА!</b>\n\n"
                f"📦 В раздел <b>{data['category']}</b> поступил новый товар:\n\n"
                f"🔹 <b>{data['name']}</b>\n"
                f"💰 Цена: {format_num(data['price'])} ₽\n"
                f"📦 В наличии: <b>{format_num(quantity)} шт.</b>\n\n"
                f"🚀 Заходи в каталог и добавляй в корзину!"
            )
            task = asyncio.create_task(broadcast_notification(notif_text, column_filter="notif_new_item"))
            active_tasks.add(task)
            task.add_done_callback(active_tasks.discard)

        await state.clear()
    except ValueError:
        await message.answer("Введи целое число:", reply_markup=get_cancel_goods_keyboard())


@dp.callback_query(F.data == "admin_delete")
async def admin_delete_list(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS: return
    keyboard = get_items_inline_keyboard("del")
    if len(keyboard.inline_keyboard) <= 1:
        await callback.answer("❌ Склад пуст!", show_alert=True)
        return
    try:
        await callback.message.edit_text("Выбери товар для **удаления**:", reply_markup=keyboard,
                                         parse_mode="Markdown")
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            await callback.answer()
        else:
            raise e


@dp.callback_query(F.data.startswith("del_"))
async def admin_delete_confirm(callback: CallbackQuery):
    item_id = int(callback.data.replace("del_", ""))
    conn = sqlite3.connect("shop.db")
    cursor = conn.cursor()
    cursor.execute("DELETE FROM items WHERE id = ?", (item_id,))
    conn.commit()
    conn.close()
    await callback.answer("Товар удален!")
    try:
        await callback.message.edit_text("✅ Товар успешно удален со склада.", reply_markup=get_admin_goods_menu())
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            pass
        else:
            raise e


@dp.callback_query(F.data == "admin_edit_qty")
async def admin_edit_list(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS: return
    keyboard = get_items_inline_keyboard("editqty")
    if len(keyboard.inline_keyboard) <= 1:
        await callback.answer("❌ Список товаров пуст!", show_alert=True)
        return
    try:
        await callback.message.edit_text("Выбери товар для изменения остатка:", reply_markup=keyboard)
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            await callback.answer()
        else:
            raise e
    await state.set_state(AdminStates.choosing_item_for_edit)


@dp.callback_query(AdminStates.choosing_item_for_edit, F.data.startswith("editqty_"))
async def admin_edit_chosen(callback: CallbackQuery, state: FSMContext):
    item_id = int(callback.data.replace("editqty_", ""))
    await state.update_data(edit_item_id=item_id)
    try:
        await callback.message.edit_text("Введи **новое количество** товара на складе:", parse_mode="Markdown",
                                         reply_markup=get_cancel_goods_keyboard())
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            await callback.answer()
        else:
            raise e
    await state.set_state(AdminStates.entering_new_quantity)


@dp.message(AdminStates.entering_new_quantity)
async def admin_edit_quantity_save(message: Message, state: FSMContext):
    try:
        new_qty = int(message.text)
        data = await state.get_data()

        conn = sqlite3.connect("shop.db")
        cursor = conn.cursor()
        cursor.execute("SELECT name, category, quantity, price FROM items WHERE id = ?", (data['edit_item_id'],))
        item = cursor.fetchone()

        if not item:
            await message.answer("Ошибка: товар не найден.", reply_markup=get_admin_goods_menu())
            conn.close()
            await state.clear()
            return

        old_qty = item[2]
        cursor.execute("UPDATE items SET quantity = ? WHERE id = ?", (new_qty, data['edit_item_id']))
        conn.commit()
        conn.close()

        await message.answer("✅ Количество товара на складе успешно обновлено!", reply_markup=get_admin_goods_menu())

        if old_qty == 0 and new_qty > 0:
            notif_text = (
                f"⚡️ <b>ДОЛГОЖДАННЫЙ РЕСТОК!</b>\n\n"
                f"📦 Товар из раздела <b>{item[1]}</b> снова доступен к покупке:\n\n"
                f"🔹 <b>{item[0]}</b>\n"
                f"💰 Цена: {format_num(item[3])} ₽\n"
                f"➕ Поступило: <b>+{format_num(new_qty)} шт.</b>\n"
                f"📦 В наличии: <b>{format_num(new_qty)} шт.</b>\n\n"
                f"🏃‍♂️ Позиция активна, можно собирать в корзину!"
            )
            task = asyncio.create_task(broadcast_notification(notif_text, column_filter="notif_restock"))
            active_tasks.add(task)
            task.add_done_callback(active_tasks.discard)

        await state.clear()
    except ValueError:
        await message.answer("Введи целое число:", reply_markup=get_cancel_goods_keyboard())


# ==========================================
# АДМИН-ЛОГИКА: СИСТЕМА КАССЫ (ОФЛАЙН ЧЕКИ)
# ==========================================
@dp.callback_query(F.data == "cash_create")
async def cash_create_receipt(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS: return
    keyboard = get_items_inline_keyboard("cashitem")
    if len(keyboard.inline_keyboard) <= 1:
        await callback.answer("❌ В базе нет товаров для проведения продажи через кассу!", show_alert=True)
        return
    try:
        await callback.message.edit_text("🧾 **Формирование чека**\n\nВыбери проданный товар:", reply_markup=keyboard,
                                         parse_mode="Markdown")
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            await callback.answer()
        else:
            raise e
    await state.set_state(AdminStates.cashbox_choosing_item)


@dp.callback_query(AdminStates.cashbox_choosing_item, F.data.startswith("cashitem_"))
async def cash_item_chosen(callback: CallbackQuery, state: FSMContext):
    item_id = int(callback.data.replace("cashitem_", ""))
    conn = sqlite3.connect("shop.db")
    cursor = conn.cursor()
    cursor.execute("SELECT name, category, price, quantity FROM items WHERE id = ?", (item_id,))
    item = cursor.fetchone()
    conn.close()

    if not item:
        try:
            await callback.message.edit_text("Товар не найден.", reply_markup=get_cashbox_menu())
        except TelegramBadRequest as e:
            if "message is not modified" in str(e):
                await callback.answer()
            else:
                raise e
        await state.clear()
        return
    await state.update_data(item_id=item_id, item_name=item[0], item_cat=item[1], base_price=item[2], max_qty=item[3])
    try:
        await callback.message.edit_text(
            f"Товар: **{item[0]}** (Остаток: {format_num(item[3])} шт.)\n\nВведи **количество**:",
            parse_mode="Markdown",
            reply_markup=get_cancel_cashbox_keyboard())
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            await callback.answer()
        else:
            raise e
    await state.set_state(AdminStates.cashbox_entering_quantity)


@dp.message(AdminStates.cashbox_entering_quantity)
async def cash_quantity_entered(message: Message, state: FSMContext):
    try:
        qty = int(message.text)
        data = await state.get_data()
        if qty <= 0 or qty > data['max_qty']:
            await message.answer(f"Неверное число. На складе доступно: {format_num(data['max_qty'])} шт.",
                                 reply_markup=get_cancel_cashbox_keyboard())
            return
        recommended_amount = data['base_price'] * qty
        await state.update_data(quantity=qty)
        await message.answer(
            f"Выбрано: {format_num(qty)} шт.\nРекомендуемая сумма: **{format_num(recommended_amount)} ₽**\n\nВведи **итоговую сумму**:",
            parse_mode="Markdown", reply_markup=get_cancel_cashbox_keyboard())
        await state.set_state(AdminStates.cashbox_entering_amount)
    except ValueError:
        await message.answer("Введи целое число:", reply_markup=get_cancel_cashbox_keyboard())


@dp.message(AdminStates.cashbox_entering_amount)
async def cash_amount_entered(message: Message, state: FSMContext):
    try:
        amount = float(message.text)
        await state.update_data(total_amount=amount)
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💵 Наличные", callback_data="paytype_Наличные")],
            [InlineKeyboardButton(text="💳 Безналичные", callback_data="paytype_Безналичные")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="admin_cashbox")]
        ])
        await message.answer("Выбери тип оплаты:", reply_markup=keyboard)
        await state.set_state(AdminStates.cashbox_entering_buyer)
    except ValueError:
        await message.answer("Введи число:", reply_markup=get_cancel_cashbox_keyboard())


@dp.callback_query(AdminStates.cashbox_entering_buyer, F.data.startswith("paytype_"))
async def cash_paytype_chosen(callback: CallbackQuery, state: FSMContext):
    await state.update_data(payment_type=callback.data.replace("paytype_", ""))
    try:
        await callback.message.edit_text("Укажи имя покупателя:",
                                         reply_markup=get_cancel_cashbox_keyboard())
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            await callback.answer()
        else:
            raise e


@dp.message(AdminStates.cashbox_entering_buyer)
async def cash_final_receipt(message: Message, state: FSMContext):
    buyer = message.text
    data = await state.get_data()

    receipt_num = f"EC-{datetime.now().strftime('%Y%m%d')}-" + ''.join(
        random.choices(string.ascii_uppercase + string.digits, k=4))
    now_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    conn = sqlite3.connect("shop.db")
    cursor = conn.cursor()
    cursor.execute('''
                   INSERT INTO receipts (receipt_number, item_name, category, quantity, total_amount, payment_type,
                                         buyer_info, date_time, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'Завершен')
                   ''', (receipt_num, data['item_name'], data['item_cat'], data['quantity'], data['total_amount'],
                         data['payment_type'], buyer, now_time))
    cursor.execute("UPDATE items SET quantity = quantity - ? WHERE id = ?", (data['quantity'], data['item_id']))
    conn.commit()
    conn.close()

    await message.answer(
        f"🧾 **Чек успешно сформирован!**\n━━━━━━━━━━\n"
        f"🆔 **Номер:** `{receipt_num}`\n📅 **Время:** {format_datetime(now_time)}\n"
        f"📦 **Товар:** {data['item_name']}\n🔢 **Количество:** {format_num(data['quantity'])} шт.\n"
        f"💰 **Сумма:** {format_num(data['total_amount'])} ₽\n👤 **Покупатель:** {buyer}\n"
        f"━━━━━━━━━━\n📌 _Передай код для активации покупателю._",
        parse_mode="Markdown", reply_markup=get_cashbox_menu()
    )
    await state.clear()


# ==========================================
# СТАТИСТИКА
# ==========================================
@dp.callback_query(F.data == "admin_stats")
async def admin_stats(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return

    conn = sqlite3.connect("shop.db")
    cursor = conn.cursor()

    # === Статистика по типам оплаты ===
    cursor.execute(
        "SELECT SUM(total_amount), SUM(quantity) FROM receipts WHERE payment_type='Наличные' AND status='Завершен'")
    cash_data = cursor.fetchone()

    cursor.execute(
        "SELECT SUM(total_amount), SUM(quantity) FROM receipts WHERE payment_type='Безналичные' AND status='Завершен'")
    card_data = cursor.fetchone()

    cursor.execute(
        "SELECT SUM(total_amount), SUM(quantity) FROM receipts WHERE payment_type='Online' AND status IN ('Принят', 'Оплачен')")
    online_data = cursor.fetchone()

    # === Расширенная статистика ===
    cursor.execute("""
        SELECT 
            COUNT(*) as total_orders,
            SUM(total_amount) as total_sum,
            AVG(total_amount) as avg_check,
            COUNT(DISTINCT user_id) as unique_buyers
        FROM receipts 
        WHERE status IN ('Оплачен', 'Завершен')
    """)
    stats = cursor.fetchone()

    cursor.execute("SELECT COUNT(*) FROM users")
    total_users = cursor.fetchone()[0]

    # Топ-5 товаров
    cursor.execute("""
        SELECT item_name, SUM(quantity) as total_sold, SUM(total_amount) as total_revenue
        FROM receipts 
        WHERE status IN ('Оплачен', 'Завершен')
        GROUP BY item_name
        ORDER BY total_revenue DESC
        LIMIT 5
    """)
    top_items = cursor.fetchall()

    conn.close()

    # Подготовка данных
    cash_sum = cash_data[0] or 0
    cash_qty = cash_data[1] or 0
    card_sum = card_data[0] or 0
    card_qty = card_data[1] or 0
    online_sum = online_data[0] or 0
    online_qty = online_data[1] or 0

    total_sum = cash_sum + card_sum + online_sum
    total_orders = stats[0] or 0
    avg_check = stats[2] or 0
    unique_buyers = stats[3] or 0

    # Формирование текста
    text = "📊 Статистика\n\n"

    text += f"👥 Клиенты: {format_num(total_users)} всего, {format_num(unique_buyers)} с покупками\n\n"

    text += f"📦 Заказы: {format_num(total_orders)} завершённых\n"
    text += f"💰 Средний чек: {format_num(avg_check)} ₽\n\n"

    # Блок оплаты (как было раньше)
    text += "━━━━━━━━━━\n"
    text += f"💵 Наличные (оффлайн): {format_num(cash_sum)} ₽ ({format_num(cash_qty)} шт.)\n"
    text += f"💳 Безналичные (оффлайн): {format_num(card_sum)} ₽ ({format_num(card_qty)} шт.)\n"
    text += f"🛒 Онлайн-заказы: {format_num(online_sum)} ₽ ({format_num(online_qty)} шт.)\n\n"
    text += f"<b>💰 Общая выручка: {format_num(total_sum)} ₽</b>\n"
    text += "━━━━━━━━━━\n\n"

    # Топ-5 товаров
    if top_items:
        text += f"🏆 Топ-5 самых продаваемых товаров:\n\n"
        for i, (name, sold, revenue) in enumerate(top_items, 1):
            text += f"{i}. {name}\n"
            text += f"   {format_num(sold)} шт. – {format_num(revenue)} ₽\n\n"
    else:
        text += "🏆 Топ-5 товаров:\n\n"
        text += "❌ Пока нет завершённых продаж, поэтому топ сформировать невозможно.\n\n"

    text += "⚠️ Учитываются только заказы со статусом «Оплачен» и «Завершен»!"

    keyboard = get_back_to_admin_keyboard()

    try:
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            await callback.answer()

# ==========================================
# ИСТОРИЯ ЧЕКОВ
# ==========================================
@dp.callback_query(F.data == "admin_receipts_history")
async def admin_receipts_history(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return

    conn = sqlite3.connect("shop.db")
    cursor = conn.cursor()
    cursor.execute("""
        SELECT receipt_number, date_time, buyer_info, total_amount, 
               payment_type, status 
        FROM receipts 
        ORDER BY id DESC 
        LIMIT 10
    """)
    receipts = cursor.fetchall()
    conn.close()

    if not receipts:
        await callback.message.edit_text(
            "❌ Пока нет ни одного чека.\n\nСформируйте первый через кнопку «Новый чек».",
            reply_markup=get_cashbox_menu()
        )
        return

    text = "📜 История чеков\n\nПоследние 10 чеков:\n\n"

    buttons = []
    for r in receipts:
        num, dt, buyer, amount, ptype, status = r
        short_buyer = buyer.split(" (")[0] if buyer and " (" in buyer else buyer
        date_str = format_datetime(dt)[:10]

        icon = "🧾" if ptype in ("Наличные", "Безналичные") else "🛒"
        status_icon = "✅" if status in ("Оплачен", "Завершен") else "⏳"

        text += f"{icon} {num} • {date_str} • {format_num(amount)} ₽\n"

        buttons.append([
            InlineKeyboardButton(
                text=f"{status_icon} {num} – {format_num(amount)} ₽",
                callback_data=f"receipt_detail_{num}"
            )
        ])

    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_cashbox")])

    try:
        await callback.message.edit_text(text,
                                         reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
                                         parse_mode="HTML")
    except TelegramBadRequest:
        await callback.answer("Ошибка отображения", show_alert=True)


@dp.callback_query(F.data.startswith("receipt_detail_"))
async def receipt_detail_view(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return

    receipt_num = callback.data.replace("receipt_detail_", "")

    conn = sqlite3.connect("shop.db")
    cursor = conn.cursor()
    cursor.execute("""
        SELECT receipt_number, item_name, total_amount, payment_type, 
               buyer_info, date_time, status 
        FROM receipts WHERE receipt_number = ?
    """, (receipt_num,))
    receipt = cursor.fetchone()
    conn.close()

    if not receipt:
        await callback.answer("Чек не найден", show_alert=True)
        return

    num, items, amount, ptype, buyer, dt, status = receipt

    # Делаем в стиле пользовательского "Инфо"
    alert_text = (
        f"🧾 Чек: {num}\n"
        f"⚙️ Статус: {status}\n"
        f"📅 Дата: {format_datetime(dt)}\n"
        f"👤 Покупатель: {buyer}\n"
        f"💵 Тип оплаты: {ptype}\n"
        f"━━━━━━━━━━\n"
        f"📦 Состав:\n{items}\n"
        f"━━━━━━━━━━\n"
        f"💰 Итого: {format_num(amount)} ₽"
    )

    # Для админа делаем большое всплывающее окно (show_alert=True)
    await callback.answer(text=alert_text, show_alert=True)

# ==========================================
# ОСТАЛЬНЫЕ СТАНДАРТНЫЕ ХЕНДЛЕРЫ
# ==========================================
@dp.callback_query(F.data == "user_help")
async def help_callback(callback: CallbackQuery):
    await update_user_menu(
        callback=callback,
        photo_path=PATH_HELP,
        caption=f"❓ Помощь\n\nИмеются вопросы по заказу? Напиши менеджеру: @{MANAGER_USERNAME}\n\n⏰ График: с 10:00 до 22:00",
        reply_markup=get_back_to_menu_keyboard(),
        parse_mode="Markdown"
    )

@dp.callback_query(F.data == "user_enter_receipt")
async def user_enter_receipt_start(callback: CallbackQuery, state: FSMContext):
    await update_user_menu(
        callback=callback,
        photo_path=PATH_RECEIPT,
        caption="🧾 **Активация чека**\n\nВведи уникальный номер чека:",
        reply_markup=get_back_to_menu_keyboard(),
        parse_mode="Markdown"
    )
    await state.set_state(UserStates.entering_receipt_code)

@dp.message(UserStates.entering_receipt_code)
async def user_receipt_code_processing(message: Message, state: FSMContext):
    code = message.text.strip()
    conn = sqlite3.connect("shop.db")
    cursor = conn.cursor()
    cursor.execute("SELECT id, item_name, total_amount, user_id FROM receipts WHERE receipt_number = ?", (code,))
    receipt = cursor.fetchone()

    if not receipt:
        await message.answer("❌ Чек не найден. Попробуй снова:", reply_markup=get_back_to_menu_keyboard())
        conn.close()
        return
    if receipt[3] is not None:
        await message.answer("⚠️ Данный чек уже активирован!", reply_markup=get_back_to_menu_keyboard())
        conn.close()
        await state.clear()
        return

    cursor.execute("UPDATE receipts SET user_id = ? WHERE receipt_number = ?", (message.from_user.id, code))
    conn.commit()
    conn.close()

    await message.answer(f"✅ **Чек успешно добавлен!**\n\nТовар: *{receipt[1]}*\nСумма: *{format_num(receipt[2])} ₽*",
                         parse_mode="Markdown", reply_markup=get_back_to_menu_keyboard())
    await state.clear()


@dp.callback_query(F.data == "user_profile")
async def user_profile_screen(callback: CallbackQuery):
    profile_text = (
        f"👤 **Мой профиль**\n\n"
        f"• **Имя:** {callback.from_user.first_name}\n"
        f"• **Твой ID:** `{callback.from_user.id}`\n"
        f"━━━━━━━━━━\n"
        f"Управляй своими заказами и настраивай уведомления с помощью меню ниже 👇"
    )

    inline_keyboard = [
        [InlineKeyboardButton(text="📦 Мои заказы", callback_data="user_orders")],
        [InlineKeyboardButton(text="🔔 Настройка уведомлений", callback_data="user_notif_settings")],
        [InlineKeyboardButton(text="↩️ В главное меню", callback_data="to_main_menu")]
    ]

    await update_user_menu(
        callback=callback,
        photo_path=PATH_PROFILE,
        caption=profile_text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=inline_keyboard),
        parse_mode="Markdown"
    )

# Новый хендлер для отображения списка заказов
@dp.callback_query(F.data == "user_orders")
async def user_orders_screen(callback: CallbackQuery):
    conn = sqlite3.connect("shop.db")
    cursor = conn.cursor()
    cursor.execute(
        "SELECT receipt_number, total_amount, payment_type, status FROM receipts WHERE user_id = ? ORDER BY id DESC",
        (callback.from_user.id,))
    user_receipts = cursor.fetchall()
    conn.close()

    if not user_receipts:
        # ← Вот это новое поведение
        await callback.answer(
            "❌ У тебя пока нет заказов. Самое время сделать первый!",
            show_alert=True
        )
        return  # Важно! Выходим, чтобы не обновлять меню

    # Если заказы есть — показываем список как раньше
    orders_text = (
        f"📦 **Мои заказы**\n\n"
        f"🧾 **История покупок ({format_num(len(user_receipts))} шт.):**\n\n"
    )

    inline_keyboard = []

    for receipt in user_receipts:
        r_num, amount, p_type, status = receipt

        if status == "В обработке":
            icon = "⏳"
        elif status == "Принят":
            icon = "📦"
        elif status == "Оплачен":
            icon = "✅"
        elif status == "Отклонен":
            icon = "❌"
        else:
            icon = "🧾"

        orders_text += f"{icon} `{r_num}` — **{format_num(amount)} ₽** ({p_type})\n"

        inline_keyboard.append([
            InlineKeyboardButton(text=f"👁 Инфо {r_num}", callback_data=f"view_rec_info_{r_num}")
        ])

    inline_keyboard.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="user_profile")])

    await update_user_menu(
        callback=callback,
        photo_path=PATH_PROFILE,
        caption=orders_text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=inline_keyboard),
        parse_mode="Markdown"
    )

# Новый хендлер для отображения информации о чеке во всплывающем окне по центру
@dp.callback_query(F.data.startswith("view_rec_info_"))
async def view_receipt_info_alert(callback: CallbackQuery):
    receipt_code = callback.data.replace("view_rec_info_", "")

    conn = sqlite3.connect("shop.db")
    cursor = conn.cursor()
    cursor.execute(
        "SELECT item_name, total_amount, date_time, status, payment_type FROM receipts WHERE receipt_number = ?",
        (receipt_code,))
    res = cursor.fetchone()
    conn.close()

    if not res:
        await callback.answer("⚠️ Чек не найден в системе.", show_alert=True)
        return

    item_name, total_amount, date_time, status, payment_type = res

    # Формируем компактный текст для всплывающего уведомления
    alert_text = (
        f"📋 Заказ: {receipt_code}\n"
        f"⚙️ Статус: {status}\n"
        f"📅 Дата: {format_datetime(date_time)}\n"
        f"💵 Тип: {payment_type}\n"
        f"━━━━━━━━━━\n"
        f"📦 Состав:\n{item_name}\n"
        f"━━━━━━━━━━\n"
        f"💰 Оплачено: {format_num(total_amount)} ₽"
    )

    # Используем show_alert=True для вывода окна строго посередине экрана
    await callback.answer(text=alert_text, show_alert=True)


# ==========================================
# ИНТЕРФЕЙС НАСТРОЕК УВЕДОМЛЕНИЙ
# ==========================================
@dp.callback_query(F.data == "user_notif_settings")
async def user_notif_settings_handler(callback: CallbackQuery):
    user_id = callback.from_user.id
    text = (
        "🔔 Настройка уведомлений\n\n"
        "Здесь ты можешь гибко управлять уведомлениями от нашего магазина.\n\n"
        "Нажми на кнопку, чтобы включить (🔔) или отключить (🔕) интересующий тебя тип уведомлений:"
    )
    await update_menu_text(
        callback=callback,
        text=text,
        reply_markup=get_notif_settings_keyboard(user_id),
        parse_mode="Markdown"
    )


@dp.callback_query(F.data.startswith("toggle_notif_"))
async def toggle_notification_setting(callback: CallbackQuery):
    field = callback.data.replace("toggle_notif_", "")
    user_id = callback.from_user.id

    conn = sqlite3.connect("shop.db")
    cursor = conn.cursor()
    cursor.execute(f"SELECT {field} FROM users WHERE user_id = ?", (user_id,))
    res = cursor.fetchone()

    if res:
        new_val = 0 if res[0] == 1 else 1
        cursor.execute(f"UPDATE users SET {field} = ? WHERE user_id = ?", (new_val, user_id))
        conn.commit()
    else:
        new_val = 0
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute(f"INSERT INTO users (user_id, joined_date, {field}) VALUES (?, ?, ?)",
                       (user_id, now_str, new_val))
        conn.commit()

    conn.close()

    try:
        await callback.message.edit_reply_markup(reply_markup=get_notif_settings_keyboard(user_id))
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            await callback.answer()
        else:
            raise e


# ==========================================
# ФОНОВАЯ ЗАДАЧА: НАПОМИНАНИЕ О КОРЗИНЕ
# ==========================================
async def cart_reminder_loop():
    while True:
        try:
            await asyncio.sleep(60)
            now = datetime.now()

            for user_id, last_time in list(last_cart_activity.items()):
                if now - last_time > timedelta(minutes=15):
                    last_cart_activity.pop(user_id, None)

                    # Изменение 5: Запрашиваем живой FSM-контекст пользователя и проверяем реальное наполнение корзины
                    state_context = dp.fsm.get_context(bot=bot, chat_id=user_id, user_id=user_id)
                    fsm_data = await state_context.get_data()
                    cart = fsm_data.get("cart", {})

                    if not cart:
                        continue  # Если корзина пуста, ничего не шлём

                    try:
                        await bot.send_message(
                            chat_id=user_id,
                            text="🛒 **Ты кое-что забыл в корзине!**\n\n"
                                 "Мы заметили, что ты собрал товары в корзину, но не завершил оформление заказа. "
                                 "Пока ты думаешь, позиции могут закончиться на складе! 😱\n\n"
                                 "👋 Возвращайся в бота и нажми кнопку **«🛒 Корзина»**, чтобы отправить заказ менеджеру!",
                            parse_mode="Markdown"
                        )
                    except Exception as e:
                        logging.error(f"Не удалось отправить напоминание о корзине пользователю {user_id}: {e}")
        except Exception as global_err:
            logging.error(f"Критическая ошибка в фоновом цикле корзины: {global_err}")
            await asyncio.sleep(10)  # Защита от бесконечного быстрого падения цикла


# ==========================================
# ЗАПУСК БОТА
# ==========================================
async def main():
    # Защищаем задачу от сборщика мусора, добавляя её в active_tasks
    reminder_task = asyncio.create_task(cart_reminder_loop())
    active_tasks.add(reminder_task)
    reminder_task.add_done_callback(active_tasks.discard)

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())