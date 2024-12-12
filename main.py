
import logging
import asyncio
import os
import uuid
import config
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.storage.memory import MemoryStorage
from yt_dlp import YoutubeDL
from vk_api import VkApi
from aiogram.dispatcher.router import Router
from aiogram.types import FSInputFile, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery


# Настройка логирования
logging.basicConfig(level=logging.INFO)

# Создаем экземпляр бота и хранилище для состояний
bot = Bot(token=config.API_TOKEN)
vk_session = VkApi(token=config.VK_API_TOKEN)
print(config.VK_API_TOKEN)
vk = vk_session.get_api()

storage = MemoryStorage()
dp = Dispatcher(storage=storage)
router = Router()

# Определяем состояния для ожидания выбора и ссылки
class DownloadStates(StatesGroup):
    choosing_action = State()
    waiting_for_video_link = State()
    waiting_for_music_query = State()
    browsing_music_results = State()

# Функция для скачивания видео или музыки
def download_media(url: str, media_type: str) -> str:
    download_folder = "downloads"
    os.makedirs(download_folder, exist_ok=True)
    
    unique_id = str(uuid.uuid4())  # Уникальный идентификатор
    output_name = f"{unique_id}.%(ext)s"
    
    ydl_opts = {
        'format': 'bestaudio' if media_type == 'music' else 'best',
        'outtmpl': os.path.join(download_folder, output_name),
        'noplaylist': True,
        'quiet': True,
    }
    output_path = None
    try:
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)  # Получение информации о медиа
            output_path = ydl.prepare_filename(info)  # Получаем итоговый путь к файлу
    except Exception as e:
        logging.error(f"Ошибка при скачивании файла: {e}")
        raise e

    return output_path

# Функция для создания инлайн-клавиатуры выбора действия
def choice_keyboard():
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Видео", callback_data="download_video"),
         InlineKeyboardButton(text="Музыка", callback_data="download_music")]
    ])
    return keyboard

# Функция для создания инлайн-клавиатуры для навигации по результатам
def navigation_keyboard(page: int):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"prev_{page}"),
         InlineKeyboardButton(text="Вперед ➡️", callback_data=f"next_{page}")]
    ])
    return keyboard

# Обработчик команды /start
@router.message(Command("start"))
async def send_welcome(message: types.Message, state: FSMContext):
    await bot.send_message(message.chat.id, "Привет! Что будем качать?", reply_markup=choice_keyboard())
    await state.set_state(DownloadStates.choosing_action)

# Обработчик для инлайн-кнопки "Видео"
@router.callback_query(F.data == "download_video")
async def choose_video(callback_query: CallbackQuery, state: FSMContext):
    await bot.send_message(callback_query.message.chat.id, "Отправьте ссылку на видео.")
    await state.set_state(DownloadStates.waiting_for_video_link)
    await callback_query.answer()

# Обработчик для инлайн-кнопки "Музыка"
@router.callback_query(F.data == "download_music")
async def choose_music(callback_query: CallbackQuery, state: FSMContext):
    await bot.send_message(callback_query.message.chat.id, "Введите название трека или исполнителя.")
    await state.set_state(DownloadStates.waiting_for_music_query)
    await callback_query.answer()


@router.message(DownloadStates.waiting_for_video_link)
async def download_and_send_video(message: types.Message, state: FSMContext):
    url = message.text
    try:
        await bot.send_message(message.chat.id, "Скачиваю видео, пожалуйста, подождите...")
        video_path = download_media(url, 'video')

        if not os.path.exists(video_path):
            await bot.send_message(message.chat.id, "Файл не найден после скачивания.")
            logging.error("Файл не найден после скачивания.")
            return

        video = FSInputFile(video_path)
        await bot.send_video(
            chat_id=message.chat.id,
            video=video,
            supports_streaming=True
        )
        
        logging.info("Видео успешно отправлено.")
        
        await bot.send_message(message.chat.id, "Что будем качать дальше?", reply_markup=choice_keyboard())
        await state.set_state(DownloadStates.choosing_action)

    except Exception as e:
        logging.error(f"Ошибка при скачивании или отправке видео: {e}")
        await bot.send_message(message.chat.id, "Не удалось скачать или отправить видео. Проверьте ссылку или попробуйте позже.")


@router.message(DownloadStates.waiting_for_music_query)
async def search_music(message: types.Message, state: FSMContext):
    query = message.text
    try:
        # Выполняем поиск музыки через VK API
        results = vk.audio.search(q=query, count=100)  # Ищем до 100 треков по запросу
        if not results['items']:
            await bot.send_message(message.chat.id, "По вашему запросу ничего не найдено.")
            return

        # Сохраняем результаты поиска и текущую страницу
        await state.update_data(music_results=results['items'], page=0)
        await display_music_results(message, state, 0)  # Отображаем первую страницу

    except Exception as e:
        logging.error(f"Ошибка при поиске музыки: {e}")
        await bot.send_message(message.chat.id, "Произошла ошибка при поиске музыки. Попробуйте позже.")

# Функция для отображения результатов поиска по странице
async def display_music_results(message: types.Message, state: FSMContext, page: int):
    data = await state.get_data()
    results = data['music_results']
    start = page * 5
    end = start + 5
    page_results = results[start:end]

    text = "Результаты поиска:\n\n"
    for i, track in enumerate(page_results, start=1):
        text += f"{i}. {track['artist']} - {track['title']}\n"

    await bot.send_message(
        message.chat.id,
        text=text,
        reply_markup=navigation_keyboard(page)
    )
    await state.update_data(page=page)
    await state.set_state(DownloadStates.browsing_music_results)

# Обработчик для кнопок навигации (вперед и назад)
@router.callback_query(DownloadStates.browsing_music_results)
async def navigate_music_results(callback_query: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    page = data.get('page', 0)

    # Определяем направление навигации
    if callback_query.data.startswith("next"):
        page += 1
    elif callback_query.data.startswith("prev"):
        page -= 1

    # Проверяем границы страниц
    if page < 0:
        page = 0
    elif page * 5 >= len(data['music_results']):
        page -= 1  # Возвращаемся на предыдущую страницу, если выходим за пределы

    await display_music_results(callback_query.message, state, page)
    await callback_query.answer()

# Основная функция для запуска бота
async def main():
    dp.include_router(router)
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

# Запуск бота
if __name__ == '__main__':
    asyncio.run(main())