import os
import asyncio
from typing import Optional
import re
import aiohttp
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message

load_dotenv()   #загружаем переменные из .env

TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN", "")
API_BASE_URL = os.getenv("API_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
BOT_API_KEY = os.getenv("BOT_API_KEY", "")


WHITELIST_IDS = set()
for x in os.getenv("WHITELIST_IDS", "").split(","):   #разрешаем пользоваться ботом только определённым пользователям
    x = x.strip()
    if x.isdigit():
        WHITELIST_IDS.add(int(x))

#создаем бота и диспетчер (обработчик сообщений)
bot = Bot(token=TG_BOT_TOKEN)
dp = Dispatcher()


def is_allowed(user_id: int) -> bool:   #проверка на доступ
    return user_id in WHITELIST_IDS


def parse_review_text(text: str):
    """
    Ожидаем 3+ строки:
    1) Имя Фамилия
    2) Марка Модель
    3) Рейтинг
    4) Текст отзыва (может быть много строк)
    """
    lines = [l.strip() for l in (text or "").splitlines() if l.strip()]   #разбиваем текст на строки и убираем пустые
    if len(lines) < 3:   #должно быть минимум 3 строки (имя, машина, рейтинг или отзыв)
        return None

    name = lines[0]
    car = lines[1]

    rating = 5
    rest = lines[2:]

    # если 3-я строка — число 1..5 => это рейтинг
    if rest and rest[0].isdigit():
        r = int(rest[0])
        if 1 <= r <= 5:
            rating = r
            rest = rest[1:]

    comment = "\n".join(rest).strip()   #сам отзыв
    if not comment:
        return None

    return name, car, rating, comment


async def telegram_file_to_url(file_id: str) -> Optional[str]:
    #отдаём прямую ссылку на файл Telegram
    file = await bot.get_file(file_id)
    return f"https://api.telegram.org/file/bot{TG_BOT_TOKEN}/{file.file_path}"


@dp.message(Command("start"))   #хэндлер команды /start
async def start(message: Message):
    if not is_allowed(message.from_user.id):
        await message.answer("Нет доступа.")
        return

    await message.answer(   #подсказка что вводить для пользователя
        "Отправь отзыв одним сообщением (3+ строки):\n"
        "1) Имя Фамилия\n"
        "2) Марка Модель\n"
        "3) Рейтинг (например, 5)\n"
        "4) Текст отзыва (можно в несколько строк)\n\n"
        "Фото можно прикрепить к этому же сообщению."
    )


def normalize_phone(text: str) -> str | None:
    digits = re.sub(r"\D", "", text)  # убираем всё кроме цифр

    if len(digits) == 11 and digits.startswith("8"):
        digits = "7" + digits[1:]

    if len(digits) == 11 and digits.startswith("7"):
        return digits

    return None

@dp.message(Command("phone"))   #хэндлер команды /phone
async def set_phone(message: Message):
    if not is_allowed(message.from_user.id):
        await message.answer("Нет доступа.")
        return

    # /phone +7 999 111-22-33
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Пример: /phone +7 999 111-22-33")
        return

    phone_raw = parts[1].strip()
    phone = normalize_phone(phone_raw)

    if not phone:
        await message.answer("❌ Неверный формат. Пример: 79991112233")
        return

    headers = {"X-API-KEY": BOT_API_KEY}   #защита API

    async with aiohttp.ClientSession() as session:
        async with session.put(
            f"{API_BASE_URL}/contacts/phone",   #вызываем наш ендпоинт FastAPI
            json={"phone": phone},
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=20),
        ) as resp:
            if resp.status >= 400:
                body = await resp.text()
                await message.answer(f"Ошибка API: {resp.status}\n{body}")
                return

    await message.answer(f"✅ Телефон обновлён: {phone}")

@dp.message(F.text | F.photo)   #хэндлер ловит текст и фото
async def handle_review(message: Message):
    if not is_allowed(message.from_user.id):
        await message.answer("Нет доступа.")
        return

    if (message.text or "").startswith("/"):
        return

    text = message.text or message.caption or ""   #получение текста или фото
    parsed = parse_review_text(text)   #парсинг
    if not parsed:
        await message.answer(
            "Неверный формат.\n\nПример:\n"
            "Иван Иванов\n"
            "Toyota Camry\n"
            "5\n"
            "Очень доволен качеством!"
        )
        return

    full_name, car, rating, comment = parsed

    avatar_url = None
    if message.photo:
        file_id = message.photo[-1].file_id  # самое большое
        avatar_url = await telegram_file_to_url(file_id)

    payload = {   #это уходит в FastAPI
        "name": full_name,
        "car": car,
        "comment": comment,
        "rating": rating,
        "avatar_url": avatar_url,
    }

    headers = {}
    if BOT_API_KEY:
        headers["X-API-KEY"] = BOT_API_KEY

    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{API_BASE_URL}/reviews/",   #вызываем наш ендпоинт FastAPI
            json=payload,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=20),
        ) as resp:
            if resp.status >= 400:
                body = await resp.text()
                await message.answer(f"Ошибка API: {resp.status}\n{body}")
                return

    await message.answer("✅ Отзыв отправлен на сайт!")


async def main():
    if not TG_BOT_TOKEN:
        raise RuntimeError("TG_BOT_TOKEN is empty")
    if not WHITELIST_IDS:
        print("WARNING: WHITELIST_IDS is empty — бот никого не пустит.")
    await dp.start_polling(bot)   #бот начинает слушать Telegram


if __name__ == "__main__":
    asyncio.run(main())   #запуск
