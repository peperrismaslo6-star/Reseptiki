import os
import re
import random
import sqlite3

import telebot
from telebot import types

# База лежит рядом с этим файлом, независимо от того, откуда запущен бот
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'recipes.db')

if not os.path.exists(DB_PATH):
    raise SystemExit(
        f"Не найдена база данных: {DB_PATH}\n"
        "Сначала запусти baza_resept.py, чтобы создать recipes.db."
    )

# Токен лучше хранить не в коде, а в переменной окружения BOT_TOKEN
bot = telebot.TeleBot(os.environ["BOT_TOKEN"])

# Сколько рецептов показывать за один раз
MAX_RESULTS = 3

# Соответствие кнопок бота значениям cooking_method в базе
METHOD_MAP = {
    "В аэрогриле": "В аэрогриле",
    "В мультиварке": "В мультиварке",
    "В духовке": "В духовке",
    "В микроволновке": "В микроволновке",
    "На плите (в кастрюле и/или на сковороде)": "На плите",
    "В кастрюле": "На плите",
}

TIME_LIMITS = {          # минуты
    "До 30 минут": 30,
    "До часа": 60,
    "До 1,5 часа": 90,
    "До 2 часов": 120,
    "До 3 часов": 180,
}

PRICE_LIMITS = {         # рубли
    "До 200 рублей": 200,
    "До 400 рублей": 400,
    "До 500 рублей": 500,
    "До 600 рублей": 600,
}

CALORIES_LIMITS = {      # ккал (min, max)
    "До 100 ккал": (0, 100),
    "До 200 ккал": (0, 200),
    "До 300 ккал": (0, 300),
    "До 400 ккал": (0, 400),
    "Больше 400 ккал": (401, 10**6),
}


# ---------- работа с базой данных ----------

def to_number(value):
    """Число из значения БД: 150, '250-310' (среднее), '30' -> число. Пусто -> None."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    nums = [float(n) for n in re.findall(r"\d+(?:[.,]\d+)?", str(value).replace(",", "."))]
    if not nums:
        return None
    return sum(nums) / len(nums)


def to_minutes(value):
    """Время в минутах: '30', '45 мин', '1 ч 10 мин' -> число."""
    if value is None:
        return None
    text = str(value).lower()
    hours = re.search(r"(\d+)\s*ч", text)
    minutes = re.search(r"(\d+)\s*(?:мин)?\s*$", text)
    if hours:
        mins = re.search(r"ч\s*(\d+)", text)
        return int(hours.group(1)) * 60 + (int(mins.group(1)) if mins else 0)
    return int(minutes.group(1)) if minutes else None


def find_recipes(answers):
    """Ищет в recipes.db рецепты под ответы пользователя."""
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    sql = """
        SELECT d.name, d.category, r.cooking_method, r.ingredients,
               r.recipe, r.time, r.calories, r.price, r.image
        FROM recipes r
        JOIN dishes d ON d.id = r.dish_id
        WHERE d.category = ?
    """
    params = [answers["Блюдо"]]

    method = METHOD_MAP.get(answers.get("Способ приготовления"))
    if method:
        sql += " AND r.cooking_method = ?"
        params.append(method)

    rows = conn.execute(sql, params).fetchall()
    conn.close()

    max_time = TIME_LIMITS.get(answers.get("Время приготовления"))
    max_price = PRICE_LIMITS.get(answers.get("Цена"))
    cal_range = CALORIES_LIMITS.get(answers.get("Калорийность"))

    result = []
    for row in rows:
        minutes = to_minutes(row["time"])
        price = to_number(row["price"])
        calories = to_number(row["calories"])

        if max_time is not None and minutes is not None and minutes > max_time:
            continue
        if max_price is not None and price is not None and price > max_price:
            continue
        # если калории важны, рецепты без данных о калориях пропускаем
        if cal_range is not None:
            if calories is None or not (cal_range[0] <= calories <= cal_range[1]):
                continue
        result.append(row)

    return result


def format_recipe(row):
    def show(value, unit):
        return f"{value} {unit}" if value not in (None, "") else "не указано"

    time_text = str(row["time"]) if row["time"] else None
    if time_text and time_text.isdigit():
        time_text += " мин"

    return (
        f"🍽 {row['name']}\n"
        f"Способ: {row['cooking_method']}\n"
        f"Время: {time_text or 'не указано'}\n"
        f"Цена: {show(row['price'], 'руб.')}\n"
        f"Калории: {show(row['calories'], 'ккал')}\n\n"
        f"Ингредиенты:\n{row['ingredients']}\n\n"
        f"Приготовление:\n{row['recipe']}"
    )


def send_recipe(chat_id, row):
    """Отправляет картинку (если есть и открывается) и затем текст рецепта."""
    if row["image"]:
        try:
            bot.send_photo(chat_id, row["image"], caption=f"🍽 {row['name']}")
        except Exception as e:
            # ссылка может не открываться — тогда просто показываем текст
            print(f"Не удалось отправить картинку {row['image']}: {e}")
    send_long(chat_id, format_recipe(row))


def send_long(chat_id, text, **kwargs):
    """Telegram не принимает сообщения длиннее 4096 символов."""
    for i in range(0, len(text), 4000):
        bot.send_message(chat_id, text[i:i + 4000], **kwargs)

user_answers = {}

# Создание клавиатуры
def create_keyboard(buttons):
    keyboard = types.ReplyKeyboardMarkup(
        resize_keyboard=True,
        one_time_keyboard=True
    )

    for button in buttons:
        keyboard.add(types.KeyboardButton(button))

    return keyboard


# Главное меню
menu_keyboard = create_keyboard([
    "Завтрак",
    "Суп",
    "Ужин",
    "Десерт"
])

# Способы приготовления завтрака и ужина
cooking_keyboard = create_keyboard([
    "В аэрогриле",
    "В мультиварке",
    "В духовке",
    "В микроволновке",
    "На плите (в кастрюле и/или на сковороде)"
])

# Способы приготовления супа
soup_keyboard = create_keyboard([
    "В кастрюле",
    "В мультиварке"
])

# Время приготовления
time_keyboard = create_keyboard([
    "До 30 минут",
    "До часа",
    "До 1,5 часа",
    "До 2 часов",
    "До 3 часов"
])

# Время приготовления супа
soup_time_keyboard = create_keyboard([
    "До часа",
    "До 1,5 часа",
    "До 2 часов",
    "До 3 часов"
])

# Цена
price_keyboard = create_keyboard([
    "До 200 рублей",
    "До 400 рублей",
    "До 500 рублей",
    "До 600 рублей"
])

# Важны ли калории?
calories_question_keyboard = create_keyboard([
    "Да",
    "Нет"
])

# Калорийность
calories_keyboard = create_keyboard([
    "До 100 ккал",
    "До 200 ккал",
    "До 300 ккал",
    "До 400 ккал",
    "Больше 400 ккал"
])


# Начало работы бота
@bot.message_handler(commands=["start"])
def start(message):
    user_answers[message.chat.id] = {}

    bot.send_message(
        message.chat.id,
        "Привет! Что ты хочешь приготовить?",
        reply_markup=menu_keyboard
    )


# Выбор завтрака или ужина
@bot.message_handler(func=lambda message: message.text in [
    "Завтрак", "Ужин"
])
def choose_meal(message):
    user_answers[message.chat.id] = {
        "Блюдо": message.text
    }

    bot.send_message(
        message.chat.id,
        "Отлично! Как будешь готовить?",
        reply_markup=cooking_keyboard
    )

    bot.register_next_step_handler(
        message, choose_cooking_method
    )


# Выбор способа приготовления
def choose_cooking_method(message):
    method = message.text

    if method not in [
        "В аэрогриле",
        "В мультиварке",
        "В духовке",
        "В микроволновке",
        "На плите (в кастрюле и/или на сковороде)"
    ]:
        bot.send_message(
            message.chat.id,
            "Пожалуйста, выбери способ приготовления с помощью кнопок.",
            reply_markup=cooking_keyboard
        )

        bot.register_next_step_handler(
            message, choose_cooking_method
        )
        return

    user_answers[message.chat.id]["Способ приготовления"] = method

    bot.send_message(
        message.chat.id,
        "Отлично! Выбери время приготовления.",
        reply_markup=time_keyboard
    )

    bot.register_next_step_handler(
        message, choose_meal_time
    )


# Выбор времени завтрака или ужина
def choose_meal_time(message):
    time = message.text

    if time not in [
        "До 30 минут",
        "До часа",
        "До 1,5 часа",
        "До 2 часов",
        "До 3 часов"
    ]:
        bot.send_message(
            message.chat.id,
            "Пожалуйста, выбери время приготовления с помощью кнопок.",
            reply_markup=time_keyboard
        )

        bot.register_next_step_handler(
            message, choose_meal_time
        )
        return

    user_answers[message.chat.id]["Время приготовления"] = time
    ask_price(message.chat.id)


# Выбор супа
@bot.message_handler(func=lambda message: message.text == "Суп")
def choose_soup(message):
    user_answers[message.chat.id] = {
        "Блюдо": "Суп"
    }

    bot.send_message(
        message.chat.id,
        "Отлично! В чём будешь готовить суп?",
        reply_markup=soup_keyboard
    )

    bot.register_next_step_handler(
        message, choose_soup_method
    )


# Выбор способа приготовления супа
def choose_soup_method(message):
    method = message.text

    if method not in ["В кастрюле", "В мультиварке"]:
        bot.send_message(
            message.chat.id,
            "Пожалуйста, выбери способ приготовления с помощью кнопок.",
            reply_markup=soup_keyboard
        )

        bot.register_next_step_handler(
            message, choose_soup_method
        )
        return

    user_answers[message.chat.id]["Способ приготовления"] = method

    bot.send_message(
        message.chat.id,
        "Отлично! Выбери время приготовления.",
        reply_markup=soup_time_keyboard
    )

    bot.register_next_step_handler(
        message, choose_soup_time
    )


# Выбор времени приготовления супа
def choose_soup_time(message):
    time = message.text

    if time not in [
        "До часа",
        "До 1,5 часа",
        "До 2 часов",
        "До 3 часов"
    ]:
        bot.send_message(
            message.chat.id,
            "Пожалуйста, выбери время приготовления с помощью кнопок.",
            reply_markup=soup_time_keyboard
        )

        bot.register_next_step_handler(
            message, choose_soup_time
        )
        return

    user_answers[message.chat.id]["Время приготовления"] = time
    ask_price(message.chat.id)


# Выбор десерта
@bot.message_handler(func=lambda message: message.text == "Десерт")
def choose_dessert(message):
    user_answers[message.chat.id] = {
        "Блюдо": "Десерт"
    }

    bot.send_message(
        message.chat.id,
        "Отлично! Выбери время приготовления десерта.",
        reply_markup=time_keyboard
    )

    bot.register_next_step_handler(
        message, choose_dessert_time
    )


# Выбор времени приготовления десерта
def choose_dessert_time(message):
    time = message.text

    if time not in [
        "До 30 минут",
        "До часа",
        "До 1,5 часа",
        "До 2 часов",
        "До 3 часов"
    ]:
        bot.send_message(
            message.chat.id,
            "Пожалуйста, выбери время приготовления с помощью кнопок.",
            reply_markup=time_keyboard
        )

        bot.register_next_step_handler(
            message, choose_dessert_time
        )
        return

    user_answers[message.chat.id]["Время приготовления"] = time
    ask_price(message.chat.id)


# Вопрос о цене
def ask_price(chat_id):
    bot.send_message(
        chat_id,
        "Выбери цену.",
        reply_markup=price_keyboard
    )

    bot.register_next_step_handler_by_chat_id(
        chat_id, choose_price
    )


# Выбор цены
def choose_price(message):
    price = message.text

    if price not in [
        "До 200 рублей",
        "До 400 рублей",
        "До 500 рублей",
        "До 600 рублей"
    ]:
        bot.send_message(
            message.chat.id,
            "Пожалуйста, выбери цену с помощью кнопок.",
            reply_markup=price_keyboard
        )

        bot.register_next_step_handler(
            message, choose_price
        )
        return

    user_answers[message.chat.id]["Цена"] = price

    bot.send_message(
        message.chat.id,
        "Ответь, пожалуйста, важны ли тебе калории?",
        reply_markup=calories_question_keyboard
    )

    bot.register_next_step_handler(
        message, choose_calories_importance
    )


# Важны ли калории?
def choose_calories_importance(message):
    answer = message.text

    if answer not in ["Да", "Нет"]:
        bot.send_message(
            message.chat.id,
            "Пожалуйста, выбери «Да» или «Нет».",
            reply_markup=calories_question_keyboard
        )

        bot.register_next_step_handler(
            message, choose_calories_importance
        )
        return

    user_answers[message.chat.id]["Калории важны"] = answer

    if answer == "Да":
        bot.send_message(
            message.chat.id,
            "Выбери калорийность.",
            reply_markup=calories_keyboard
        )

        bot.register_next_step_handler(
            message, choose_calories
        )
    else:
        finish_poll(message.chat.id)


# Выбор калорийности
def choose_calories(message):
    calories = message.text

    if calories not in [
        "До 100 ккал",
        "До 200 ккал",
        "До 300 ккал",
        "До 400 ккал",
        "Больше 400 ккал"
    ]:
        bot.send_message(
            message.chat.id,
            "Пожалуйста, выбери калорийность с помощью кнопок.",
            reply_markup=calories_keyboard
        )

        bot.register_next_step_handler(
            message, choose_calories
        )
        return

    user_answers[message.chat.id]["Калорийность"] = calories
    finish_poll(message.chat.id)


# Завершение опроса
def finish_poll(chat_id):
    answers = user_answers[chat_id]

    result = "Твои ответы:\n\n"

    for question, answer in answers.items():
        result += f"{question}: {answer}\n"

    result += "\nПриятного приготовления!"

    bot.send_message(
        chat_id,
        result,
        reply_markup=create_end_keyboard()
    )

    msg = bot.send_message(
        chat_id,
        "Ты нашел, что тебе нужно?",
        reply_markup=create_end_keyboard()
    )

    bot.register_next_step_handler(msg, ask_for_another_dish)

def create_end_keyboard():
    keyboard = types.ReplyKeyboardMarkup(
        resize_keyboard=True,
        one_time_keyboard=True
    )
    keyboard.add("Да")
    keyboard.add("Нет")
    return keyboard

def ask_for_another_dish(message):
    if message.text == "Нет":
        bot.send_message(
            message.chat.id,
            "Напиши название своего блюда"
        )
        bot.register_next_step_handler(message, receive_dish_name)

    elif message.text == "Да":
        bot.send_message(
            message.chat.id,
            "Хорошо! Приятного приготовления! 😊",
            reply_markup=types.ReplyKeyboardRemove()
        )

    else:
        bot.send_message(
            message.chat.id,
            "Пожалуйста, выбери один из вариантов.",
            reply_markup=create_end_keyboard()
        )
        bot.register_next_step_handler(message, ask_for_another_dish)

def receive_dish_name(message):
    dish_name = message.text.strip()

    if not dish_name or dish_name.startswith("/"):
        msg = bot.send_message(
            message.chat.id,
            "Пожалуйста, напиши название блюда."
        )
        bot.register_next_step_handler(
            msg, receive_dish_name
        )
        return

    msg = bot.send_message(
        message.chat.id,
        f"Ты хочешь приготовить: «{dish_name}».\n\n"
        "Название получено!\n"
        "Мы обязательно добавим его позже")


if __name__ == "__main__":
    bot.infinity_polling()