import asyncio
import logging
import json
import os
import time
import random
from decimal import Decimal, InvalidOperation
import html
import traceback  # 
import re

import requests
from bs4 import BeautifulSoup
import pandas as pd

from google import genai
from google.genai import types  # Официальный SDK от Google для Gemini
from dotenv import load_dotenv

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    CallbackQueryHandler,
    MessageHandler,
    filters,
)

# Загружаем переменные окружения из .env
load_dotenv()

# --- ⚙️ КОНФИГУРАЦИЯ ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or ""
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY") or ""
TASKS_FILE_PATH = "tasks.xlsx"
DATA_FILE_PATH = "user_data.json"

# Константа базового адреса для Project Euler
PROJECT_EULER_BASE = "https://projecteuler.net"

# Настройка логирования
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Основная рабочая модель (ОБНОВИЛИ НА БЕСПЛАТНУЮ И УМНУЮ 1.5)
GEMINI_MODEL_NAME = "gemini-3.5-flash"

# Инициализация Google Gemini через НОВЫЙ SDK (ОБНОВЛЕНО)
ai_client = None
if GEMINI_API_KEY:
    ai_client = genai.Client(api_key=GEMINI_API_KEY)
    logger.info(f"Новый SDK Google GenAI успешно инициализирован. Модель: {GEMINI_MODEL_NAME}")
else:
    logger.critical("КРИТИЧЕСКАЯ ОШИБКА: Переменная GEMINI_API_KEY не найдена в .env!")

# Асинхронный лок для предотвращения повреждения user_data.json при одновременной записи
user_data_lock = asyncio.Lock()


# --------------------------
# 📝 УТИЛИТЫ ОЧИСТКИ И БЕЗОПАСНОГО ФОРМАТИРОВАНИЯ ТЕКСТА
# --------------------------

def strip_html_tags(text: str) -> str:
    """Полностью удаляет HTML теги. Нужен для безопасного фоллбэка при сбоях."""
    if not text: return ""
    return re.sub('<[^<]+?>', '', text)


def clean_ai_html(text: str) -> str:
    """
    Базовая очистка от маркдауна (оставляем для совместимости с текущими вызовами).
    """
    if not text: 
        return ""
    text = re.sub(r'^```html\s*', '', text, flags=re.IGNORECASE)
    text = re.sub(r'^```\s*', '', text)
    text = re.sub(r'\s*```$', '', text)
    text = text.replace("**", "")
    text = re.sub(r'\n{3,}', '\n\n', text)
    lines = [line.rstrip() for line in text.split('\n')]
    return '\n'.join(lines).strip()


def format_ai_html(text: str) -> str:
    """
    Продвинутая очистка: защищает валидные теги Telegram и безопасно экранирует < и >.
    Решает проблемы с <p> и математическими знаками.
    """
    if not text:
        return ""
    
    # 1. Заменяем веб-теги переносов и абзацев на обычные переводы строк
    text = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'<p\s*[^>]*>', '\n', text, flags=re.IGNORECASE)
    text = text.replace('</p>', '')
    text = re.sub(r'<div\s*[^>]*>', '\n', text, flags=re.IGNORECASE)
    text = text.replace('</div>', '')
    
    # 2. Маскируем разрешенные Telegram теги
    text = re.sub(r'<b>', '【B_START】', text, flags=re.IGNORECASE)
    text = re.sub(r'</b>', '【B_END】', text, flags=re.IGNORECASE)
    text = re.sub(r'<i>', '【I_START】', text, flags=re.IGNORECASE)
    text = re.sub(r'</i>', '【I_END】', text, flags=re.IGNORECASE)
    text = re.sub(r'<s>', '【S_START】', text, flags=re.IGNORECASE)
    text = re.sub(r'</s>', '【S_END】', text, flags=re.IGNORECASE)
    text = re.sub(r'<u>', '【U_START】', text, flags=re.IGNORECASE)
    text = re.sub(r'</u>', '【U_END】', text, flags=re.IGNORECASE)
    text = re.sub(r'</code>', '【C_END】', text, flags=re.IGNORECASE)
    text = re.sub(r'</pre>', '【P_END】', text, flags=re.IGNORECASE)
    
    # Учитываем возможные атрибуты для тегов кода
    text = re.sub(r'<code\s*([^>]*)>', r'【C_START:\1】', text, flags=re.IGNORECASE)
    text = re.sub(r'<pre\s*([^>]*)>', r'【P_START:\1】', text, flags=re.IGNORECASE)
    
    # 3. Безопасно экранируем все опасные символы (превратит x < y в x &lt; y)
    text = html.escape(text, quote=False)
    
    # 4. Возвращаем теги Telegram на место
    text = text.replace('【B_START】', '<b>').replace('【B_END】', '</b>')
    text = text.replace('【I_START】', '<i>').replace('【I_END】', '</i>')
    text = text.replace('【S_START】', '<s>').replace('【S_END】', '</s>')
    text = text.replace('【U_START】', '<u>').replace('【U_END】', '</u>')
    
    # ИСПРАВЛЕНО: Теперь тут стоит правильный закрывающий слэш </code>
    text = text.replace('【C_END】', '</code>').replace('【P_END】', '</pre>')
    
    text = re.sub(r'【C_START:([^】]*)】', r'<code\1>', text)
    text = re.sub(r'【P_START:([^】]*)】', r'<pre\1>', text)
    
    return text.strip()


async def send_html_message(chat_id, text, reply_markup=None, context=None):
    """Безопасная отправка с продвинутым форматированием HTML."""
    try:
        if not text: return None
        safe_text = format_ai_html(text) # Применяем супер-очистку перед отправкой
        return await context.bot.send_message(
            chat_id=chat_id, 
            text=safe_text, 
            parse_mode=ParseMode.HTML, 
            reply_markup=reply_markup
        )
    except Exception as e:
        tb_str = traceback.format_exc() # Сохраняем подробные логи ошибок!
        logger.warning(f"Ошибка HTML форматирования при отправке сообщения. Сбой: {e}\n{tb_str}")
        
        clean_text = strip_html_tags(text)
        return await context.bot.send_message(
            chat_id=chat_id, 
            text=clean_text, 
            reply_markup=reply_markup
        )


async def edit_html_message(update: Update, text: str, reply_markup=None):
    """Безопасное редактирование с продвинутым форматированием HTML."""
    try:
        if not text: return
        safe_text = format_ai_html(text) # Применяем супер-очистку перед изменением
        await update.callback_query.edit_message_text(
            text=safe_text, 
            parse_mode=ParseMode.HTML, 
            reply_markup=reply_markup
        )
    except Exception as e:
        tb_str = traceback.format_exc() # Сохраняем подробные логи ошибок!
        logger.warning(f"Ошибка HTML форматирования при изменении сообщения. Сбой: {e}\n{tb_str}")
        
        clean_text = strip_html_tags(text)
        try:
            await update.callback_query.edit_message_text(
                text=clean_text, 
                reply_markup=reply_markup
            )
        except Exception:
            pass
        
# --------------------------
# 🤖 АСИНХРОННОЕ ЯДРО ВЗАИМОДЕЙСТВИЯ С GEMINI API
# --------------------------

async def gemini_chat_request(contents: str, system_instruction: str = None, timeout: int = 60) -> str:
    """
    Умный ИИ-запрос. 
    УБРАН цикл retries, чтобы не конфликтовать со встроенным механизмом tenacity 
    в SDK Google и не вызывать экспоненциальные баны (48+ сек).
    """
    if not ai_client:
        logger.error("Запрос отклонён: клиент Gemini не инициализирован.")
        return "Ошибка конфигурации ИИ."

    # Динамически собираем конфиг
    config_kwargs = {"temperature": 0.3}
    if system_instruction:
        config_kwargs["system_instruction"] = system_instruction
        
    config = types.GenerateContentConfig(**config_kwargs)
    
    try:
        # Делаем ровно ОДИН запрос. Никакого спама серверов.
        response = await asyncio.wait_for(
            ai_client.aio.models.generate_content(
                model=GEMINI_MODEL_NAME, 
                contents=contents, 
                config=config
            ),
            timeout=timeout
        )
        return response.text if response.text else ""
        
    except Exception as e:
        err_str = str(e)
        
        # Читаем ошибку лимитов (429)
        if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
            match = re.search(r'retry in (\d+(?:\.\d+)?)s', err_str)
            wait_sec = int(float(match.group(1)) + 1.0) if match else 15
            logger.warning(f"Лимит Google. Передаем юзеру паузу: {wait_sec} сек.")
            raise ValueError(f"COOLDOWN:{wait_sec}")
            
        # Читаем ошибку перегрузки самого Гугла (503)
        elif "503" in err_str or "UNAVAILABLE" in err_str:
            logger.warning("Серверы Google перегружены (503).")
            raise ValueError("OVERLOAD")
            
        logger.error(f"Неизвестная ошибка ИИ: {err_str}")
        raise e


# --------------------------
# 💾 МОДУЛЬ РАБОТЫ С БАЗОЙ ДАННЫХ (EXCEL И JSON SEED)
# --------------------------

def load_tasks_from_excel(filepath: str) -> dict:
    """
    Загружает учебные задачи из локального Excel файла.
    Каждая вкладка (Sheet) интерпретируется как отдельный раздел (например, school_5).
    """
    tasks_db = {}
    if not os.path.exists(filepath):
        logger.warning(f"Файл базы данных задач '{filepath}' не найден. Локальные математические задачи недоступны.")
        return {}
    try:
        xl_file = pd.ExcelFile(filepath)
        for sheet_name in xl_file.sheet_names:
            df = pd.read_excel(filepath, sheet_name=sheet_name, dtype={"id": str})
            
            # ФИКС БАГА: заменяем любые пропуски (NaN) в ячейках на пустые строки
            df = df.fillna("")
            
            tasks_list = df.to_dict(orient="records")
            for task in tasks_list:
                task["id"] = str(task.get("id", "")).strip()
                task["answer"] = str(task.get("answer", "")).strip()
                task["type"] = "math"
                if "task_text" in task:
                    task["task_text"] = str(task["task_text"]).strip()
            tasks_db[sheet_name] = tasks_list
        logger.info(f"Локальная БД успешно загружена. Доступно разделов: {list(tasks_db.keys())}")
    except Exception as e:
        tb_str = traceback.format_exc()
        logger.error(f"Критическая ошибка при чтении Excel файла {filepath}: {e}\n{tb_str}")
    return tasks_db


def load_user_data() -> dict:
    """Загружает состояния сессий пользователей из JSON."""
    try:
        if not os.path.exists(DATA_FILE_PATH):
            return {}
        with open(DATA_FILE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            # JSON хранит ключи как строки, конвертируем обратно в int (ID пользователей Telegram)
            return {int(k) if k.isdigit() else k: v for k, v in data.items()}
    except Exception as e:
        tb_str = traceback.format_exc()
        logger.error(f"Ошибка при загрузке user_data.json: {e}\n{tb_str}")
        return {}


def save_user_data(data: dict):
    """Сохраняет текущие состояния сессий пользователей в JSON."""
    try:
        # Конвертируем числовые ключи ID в строки для корректной сериализации в JSON
        serializable = {str(k): v for k, v in data.items()}
        with open(DATA_FILE_PATH, "w", encoding="utf-8") as f:
            json.dump(serializable, f, ensure_ascii=False, indent=4)
    except Exception as e:
        tb_str = traceback.format_exc()
        logger.error(f"Ошибка при сохранении user_data.json: {e}\n{tb_str}")


# --------------------------
# 🌐 ЗАЩИЩЕННЫЙ АСИНХРОННЫЙ ПАРСЕР PROJECT EULER
# --------------------------

async def get_project_euler_problem_async(exclude_ids: list) -> dict:
    """
    Загружает случайную уникальную задачу с официального сайта Project Euler,
    после чего отправляет условие в Gemini для перевода и красивой HTML верстки.
    """
    # Вычисляем доступные задачи через множества (исключает зависание цикла)
    available_ids = list(set(range(1, 151)) - set(int(x) for x in exclude_ids if x.isdigit()))
    if not available_ids:
        logger.warning("Все доступные задачи Project Euler решены в этой сессии.")
        return None
    problem_id = random.choice(available_ids)
            
    url = f"https://projecteuler.net/problem={problem_id}"
    
    # ФИКС БАГА 403 Forbidden: подставляем реальный заголовок браузера
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    try:
        loop = asyncio.get_running_loop()
        # Скачиваем страницу асинхронно
        response = await loop.run_in_executor(
            None, 
            lambda: requests.get(url, headers=headers, timeout=10)
        )
        
        if response.status_code != 200:
            logger.error(f"Не удалось загрузить задачу Euler #{problem_id}. Статус-код сервера: {response.status_code}")
            return None
            
        soup = BeautifulSoup(response.content, "html.parser")
        
        title_tag = soup.find("h2")
        original_title = title_tag.text.strip() if title_tag else f"Problem {problem_id}"
        content_div = soup.find("div", class_="problem_content")
        
        if not content_div:
            logger.error(f"Блок 'problem_content' не обнаружен на странице задачи #{problem_id}")
            return None
            
        raw_text = content_div.get_text(separator="\n").strip()
        
        # Системный промпт для перевода условий силами Gemini
        system_prompt = (
            "Ты — редактор учебника по математике. Твоя задача — перевести задачу с Project Euler на русский язык "
            "и оформить её, используя HTML теги, поддерживаемые Telegram.\n"
            "Правила оформления:\n"
            "1. Заголовок задачи сделай жирным (<b>Заголовок</b>).\n"
            "2. Переменные и формулы внутри текста выделяй тегом <code> (например: <code>x² + y² = z²</code>).\n"
            "3. Для математических знаков используй Unicode (², ³, √, ∑, ≤, ≥, ≠, ×, ÷), чтобы это читалось как текст в книге.\n"
            "   Пример: вместо $a^2$ пиши a², вместо $x_i$ пиши xᵢ.\n"
            "4. Структурируй текст абзацами.\n"
            "5. НЕ используй Markdown, только HTML (<b>, <i>, <code>, <pre>).\n"
            "6. Не давай решения, только условие.\n"
            "7. Текст должен читаться легко, как в книге."
        )
        
        user_content = f"Title: {original_title}\n\nTask Body:\n{raw_text}"
        
        try:
            # Делаем запрос к нашему ИИ-ядру Gemini
            ai_response = await gemini_chat_request(contents=user_content, system_instruction=system_prompt)
            translated_text = clean_ai_html(ai_response)
        except Exception as e:
            logger.error(f"Ошибка ИИ-перевода для задачи #{problem_id}: {e}")
            # Если Гугл задушил лимитами, честно пишем об этом пользователю
            translated_text = (
                f"<b>{original_title}</b>\n\n"
                f"{raw_text}\n\n"
                f"<i>(⚠️ ИИ-переводчик временно недоступен из-за лимитов Google. Показан оригинальный текст.)</i>"
            )

        task_text = (
            f"📐 <b>Project Euler — Задача №{problem_id}</b>\n\n"
            f"{translated_text}\n\n"
            f"<i>Отправь ответ числом или код решения одним сообщением.</i>"
        )
        
        return {
            "id": str(problem_id), 
            "task_text": task_text, 
            "answer": "CODE_CHECK_REQUIRED", 
            "type": "euler", 
            "url": url
        }
    except Exception as e:
        tb_str = traceback.format_exc()
        logger.error(f"Ошибка выполнения модуля парсинга Euler: {e}\n{tb_str}")
        return None
# --------------------------
# 🧮 МАТЕМАТИЧЕСКИЕ НОРМАЛИЗАТОРЫ И ПРОВЕРКА ЧИСЛОВЫХ ОТВЕТОВ
# --------------------------

def normalize_answer(answer_str: str) -> Decimal | str:
    """
    Приводит строку ответа к единому стандарту, безопасно обрабатывая нули.
    """
    answer_str = str(answer_str).strip().replace(",", ".")
    try:
        d = Decimal(answer_str)
        # Если число равно нулю, возвращаем его без нормализации (чтобы не убить нули после запятой)
        return d.normalize() if d != 0 else Decimal("0")
    except InvalidOperation:
        return answer_str.lower()


def is_correct_math_answer(user_answer: str, correct_answer: str) -> bool:
    """
    Сравнивает ответ студента с эталоном из Excel.
    Для чисел учитывает погрешность вычислений (до 9 знака после запятой).
    """
    norm_user = normalize_answer(user_answer)
    norm_correct = normalize_answer(correct_answer)
    
    # Если оба ответа успешно распознаны как числа, сравниваем их математически
    if isinstance(norm_user, Decimal) and isinstance(norm_correct, Decimal):
        return abs(norm_user - norm_correct) < Decimal("1e-9")
        
    # Если это текстовые ответы (например, формулы или выражения), сравниваем строки
    return norm_user == norm_correct


# --------------------------
# 💻 ИИ-МОДУЛЬ ИНТЕЛЛЕКТУАЛЬНОЙ ВАЛИДАЦИИ СТУДЕНЧЕСКОГО КОДА
# --------------------------

async def check_code_with_ai(task_text: str, user_code: str) -> tuple[bool, str]:
    """
    Анализирует присланный студентом программный код с помощью ИИ Gemini.
    Возвращает кортеж: (Допуск_верно: bool, Комментарий_ментора: str)
    """
    system_prompt = (
        "Ты — старший разработчик и строгий, но справедливый ментор. Проверь код студента на языке Python/C++/JS к задаче.\n"
        "Правила проверки:\n"
        "1. Если логика верна, алгоритм оптимален и код гарантированно правильно решает задачу — ответь первой строкой строго: CORRECT\n"
        "2. Если есть логическая ошибка, синтаксический сбой или код решает задачу неверно — ответь первой строкой строго: INCORRECT\n"
        "После ключевого слова (CORRECT или INCORRECT) со следующей строки на русском языке кратко и емко объясни, "
        "в чем сильные стороны решения или где именно кроется ошибка (не давая готового рабочего кода взамен, студент должен додуматься сам).\n"
        "Не придирайся к мелкому стилю (PEP8), проверяй только математическую и алгоритмическую логику решения."
    )
    
    user_content = f"ЗАДАЧА:\n{strip_html_tags(task_text)}\n\nКОД СТУДЕНТА:\n{user_code}"
    
    try:
        # ИСПОЛЬЗУЕМ НАШЕ ОБНОВЛЕННОЕ AI-ЯДРО ИЗ БЛОКА №2
        reply = await gemini_chat_request(contents=user_content, system_instruction=system_prompt)
        reply = reply.strip()
        
        # Определяем вердикт
        is_correct = reply.upper().startswith("CORRECT")
        
        # ФИКС ОШИБКИ СТАРЫХ СТРУКТУР: вырезаем префиксы через регулярные выражения
        explanation = reply
        explanation = re.sub(r'^CORRECT\s*', '', explanation, flags=re.IGNORECASE)
        explanation = re.sub(r'^INCORRECT\s*', '', explanation, flags=re.IGNORECASE)
        
        return is_correct, explanation.strip()
    except Exception as e:
        # ТУТ ТРАССИРОВКА ОБЕСПЕЧИВАЕТ СВЕЧЕНИЕ ИМПОРТА TRACEBACK И СПАСАЕТ ОТ ЗАВИСАНИЙ!
        tb_str = traceback.format_exc()
        logger.error(f"Ошибка ИИ-валидации кода в модуле check_code_with_ai: {e}\n{tb_str}")
        return False, "Произошла техническая задержка при проверке кода нейросетью. Пожалуйста, попробуйте отправить код еще раз."


# --------------------------
# 🎹 ИНТЕРФЕЙСНЫЙ МОДУЛЬ: ГЕНЕРАТОР ДИНАМИЧЕСКИХ КЛАВИАТУР
# --------------------------

def get_keyboard(step: str, user_info: dict = None) -> InlineKeyboardMarkup:
    """Генерирует разметку Inline-кнопок Telegram в зависимости от текущего шага пользователя."""
    if step == "edu_level":
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("🏫 Школа (5-11 класс)", callback_data="edu_school")],
            [InlineKeyboardButton("🎓 ВУЗ (1-2 курс)", callback_data="edu_uni")],
            [InlineKeyboardButton("💻 Project Euler (Код)", callback_data="edu_euler")]
        ])
        
    elif step == "class_course":
        if user_info and user_info.get("edu_type") == "euler":
            return InlineKeyboardMarkup([[InlineKeyboardButton("🚀 Начать", callback_data="diff_Standard")]])
        
        buttons = []
        # Выбираем диапазон в зависимости от типа обучения
        items = [f"{i} класс" for i in range(5, 12)] if user_info.get("edu_type") == "school" else [f"{i} курс" for i in range(1, 3)]
        
        row = []
        for x in items:
            tag = x.split()[0]
            # Динамическая проверка: есть ли задачи в глобальной tasks_db для этого раздела
            if tasks_db.get(f"{user_info['edu_type']}_{tag}"):
                row.append(InlineKeyboardButton(f"📚 {x}", callback_data=f"class_{tag}"))
            if len(row) == 2:
                buttons.append(row)
                row = []
        if row: 
            buttons.append(row)
        
        if not buttons:
            return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад в меню", callback_data="restart_menu")]])
        return InlineKeyboardMarkup(buttons)
        
    elif step == "num_tasks":
        nums = [1, 3, 5]
        buttons = [[InlineKeyboardButton(f"{n} зад.", callback_data=f"num_{n}") for n in nums]]
        if user_info and user_info.get('edu_type') != 'euler':
            buttons.append([InlineKeyboardButton("🪐 Все доступные задачи", callback_data="num_all")])
        return InlineKeyboardMarkup(buttons)
        
    elif step == "task":
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Проверить ответ", callback_data="check_answer")],
            [InlineKeyboardButton("💡 Подсказка ИИ", callback_data="hint"),
             InlineKeyboardButton("📝 Разбор решения", callback_data="solution")],
            [InlineKeyboardButton("⏭️ Пропустить задачу", callback_data="skip_task")]
        ])
        
    elif step == "next":
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("⏭️ Следующая задача", callback_data="next_task")],
        ])
        
    elif step == "end":
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Главное меню", callback_data="restart_menu")],
            [InlineKeyboardButton("🏁 Завершить работу", callback_data="finish_session")]
        ])
        
    return InlineKeyboardMarkup([])


# --------------------------
# Visual UX Helpers
# --------------------------

async def send_thinking(update: Update, text: str = "🧠 <i>ИИ-Репетитор генерирует мысль...</i>"):
    """Вспомогательный визуальный статус загрузки (анимация ожидания для пользователя)."""
    try:
        if update.callback_query:
            return await update.callback_query.message.reply_text(text, parse_mode=ParseMode.HTML)
        return await update.message.reply_text(text, parse_mode=ParseMode.HTML)
    except Exception:
        return None
# --------------------------
# 🗃️ ИНИЦИАЛИЗАЦИЯ ГЛОБАЛЬНЫХ ХРАНИЛИЩ ДАННЫХ
# --------------------------
# Загружаем базу задач из Excel и сессии пользователей из JSON при старте приложения
tasks_db = load_tasks_from_excel(TASKS_FILE_PATH)
user_data = load_user_data()


# --------------------------
# 🚦 ОБРАБОТЧИКИ КОМАНД И СИСТЕМНАЯ ОЧИСТКА ЧАТА
# --------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /start. Сбрасывает состояние пользователя и открывает главное меню."""
    user_id = update.effective_user.id
    async with user_data_lock:
        user_data[user_id] = {
            "state": "start",
            "chat_id": update.effective_chat.id,
            "history": [],
            "user_messages": []
        }
        save_user_data(user_data)
        
    welcome_text = (
        "👋 <b>Привет! Я твой AI-репетитор по математике и программированию.</b>\n\n"
        "Я умею:\n"
        "• Генерировать умные задачи под твой уровень.\n"
        "• Проверять текстовые ответы и полноценный программный код решения.\n"
        "• Выступать ментором: давать подсказки и разборы шагов без прямой выдачи ответов.\n\n"
        "<i>Выбери желаемый режим обучения:</i>"
    )
    
    if update.callback_query:
        await edit_html_message(update, welcome_text, get_keyboard("edu_level"))
    else:
        await send_html_message(update.effective_chat.id, welcome_text, get_keyboard("edu_level"), context)


async def cleanup_messages(info: dict, context: ContextTypes.DEFAULT_TYPE):
    """
    Автоматический менеджер очистки экрана (UX-помощник).
    Удаляет временные сообщения: уведомления об ошибках ввода, анимации ожидания ИИ, старые ответы.
    """
    chat_id = info.get("chat_id")
    if not chat_id: 
        return

    keys_to_clean = ["msg_errors", "msg_think", "msg_answer", "user_messages"]
    
    for key in keys_to_clean:
        items = info.get(key, [])
        if isinstance(items, int): 
            items = [items]
        if not items: 
            continue
        
        for mid in items:
            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=mid)
            except Exception:
                pass  # Сообщение уже удалено пользователем или истек таймаут Telegram (48 часов)
        info[key] = []
    save_user_data(user_data)


# --------------------------
# 🎮 РАСПРЕДЕЛИТЕЛЬ НАЖАТИЙ НА КНОПКИ (CALLBACK QUERY HANDLER)
# --------------------------

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Основной диспетчер интерактивного меню бота."""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data
    
    user_info = user_data.setdefault(user_id, {})
    user_info["chat_id"] = query.message.chat_id

    # 1. Выбор образовательного трека
    if data.startswith("edu_"):
        user_info["edu_type"] = data.split("_")[1]
        user_info["state"] = "choosing_course"
        save_user_data(user_data)
        await edit_html_message(update, "📂 <b>Выберите интересующий подраздел / класс обучения:</b>", get_keyboard("class_course", user_info))
        return

    # 2. Выбор конкретного класса/курса
    if data.startswith(("class_", "diff_")):
        if "class_" in data:
            user_info["class_course"] = data.split("_")[1]
        user_info["state"] = "choosing_num"
        save_user_data(user_data)
        await edit_html_message(update, "🔢 <b>Укажите объем сессии: сколько задач планируете решить?</b>", get_keyboard("num_tasks", user_info))
        return

    # 3. Конфигурация и запуск сессии задач
    if data.startswith("num_"):
        num_str = data.split("_")[1]
        
        if user_info["edu_type"] == "euler":
            total_available = 150  # Ограничение рандомайзера для Project Euler
        else:
            db_key = f"{user_info['edu_type']}_{user_info.get('class_course', 'Standard')}"
            total_available = len(tasks_db.get(db_key, []))
            
        if total_available == 0:
            await edit_html_message(update, "❌ <b>Ошибка: В выбранном разделе базы данных пока нет задач.</b>", get_keyboard("edu_level"))
            return

        to_solve = total_available if num_str == "all" else int(num_str)
        
        user_info.update({
            "num_tasks": to_solve,
            "solved": 0,
            "correct": 0,
            "start_time": time.time(),
            "history": [],
            "state": "solving",
            "last_input": None,
            "msg_errors": [],
            "user_messages": []
        })
        save_user_data(user_data)
        await start_task(update, context, query)
        return

    # 4. Логика интерактивного взаимодействия внутри задачи
    if user_info.get("state") == "solving":
        
        # КНОПКА: ПРОВЕРИТЬ ОТВЕТ
        if data == "check_answer":
            if not user_info.get("last_input"):
                sent = await context.bot.send_message(
                    chat_id=user_info["chat_id"], 
                    text="⚠️ <b>Вы ничего не ввели!</b> Пожалуйста, напишите ответ или пришлите код решения сообщением в чат, а затем нажмите кнопку проверки."
                )
                user_info.setdefault("msg_errors", []).append(sent.message_id)
                return
            await process_check(user_id, update, context)
            return

        # КНОПКА: ПОДСКАЗКА ИИ
        if data == "hint":
            await cleanup_messages(user_info, context)
            think_msg = await send_thinking(update, "🧠 <i>ИИ-Ментор формулирует наводящую подсказку...</i>")
            if think_msg:
                user_info["msg_think"] = think_msg.message_id
            
            prompt = (
                "Ты — опытный преподаватель. Сформулируй наводящую подсказку к математической или IT задаче.\n"
                "КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО использовать LaTeX (символы $). Вместо этого используй аккуратный Unicode (², ³, √, ≠).\n"
                "Оформи ответ для Telegram. ИСПОЛЬЗУЙ ТОЛЬКО теги <b>, <i>, <code>. \n"
                "ЗАПРЕЩЕНО использовать теги <ul>, <ol>, <li>, <p>, <br>, <html>. Для списков используй обычный символ маркера (•).\n"
                "Ни при каких обстоятельствах не давай финального ответа и не пиши готовый программный код. Студент должен додуматься сам."
            )
            user_content = f"Задача:\n{strip_html_tags(user_info['task']['task_text'])}"
            
            try:
                # ФИКС СТАРЫХ СТРУКТУР OPENAI -> НОВОЕ ИИ-ЯДРО GEMINI SDK
                ai_response = await gemini_chat_request(contents=user_content, system_instruction=prompt, timeout=200)
                hint_text = clean_ai_html(ai_response)
                
                full_text = f"{user_info['task']['task_text']}\n\n💡 <b>Подсказка от ментора:</b>\n{hint_text}"
                await edit_html_message(update, full_text, get_keyboard("task"))
            except Exception as e:
                err_str = str(e)
                logger.error(f"Ошибка генерации подсказки: {err_str}")
                
                if "COOLDOWN:" in err_str:
                    wait_sec = err_str.split("COOLDOWN:")[1]
                    err_text = f"⏳ <b>Анти-Спам Google.</b>\nВы исчерпали лимит бесплатных запросов. Пожалуйста, подождите ровно <b>{wait_sec} секунд</b>."
                elif "OVERLOAD" in err_str:
                    err_text = "⚠️ <b>Серверы Google сейчас перегружены.</b>\nСлишком много людей используют ИИ в данный момент. Подождите пару минут."
                else:
                    err_text = "⚠️ Не удалось связаться с ИИ. Попробуйте еще раз."
                
                sent_err = await context.bot.send_message(user_info["chat_id"], err_text, parse_mode="HTML")
                user_info.setdefault("msg_errors", []).append(sent_err.message_id)
                save_user_data(user_data)
            
            try:
                await context.bot.delete_message(user_info["chat_id"], user_info["msg_think"])
            except Exception: pass
            user_info["msg_think"] = None
            return

        # КНОПКА: РАЗБОР РЕШЕНИЯ
        if data == "solution":
            await cleanup_messages(user_info, context)
            think_msg = await send_thinking(update, "🧠 <i>ИИ-Репетитор составляет подробный разбор задачи...</i>")
            if think_msg:
                user_info["msg_think"] = think_msg.message_id
            
            prompt = (
                "Ты — составитель академического учебника. Напиши детальное, пошаговое решение задачи для книги.\n"
                "Оформи структуру строго для Telegram: разрешены ТОЛЬКО теги <b>, <i>, <code>. \n"
                "КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО выводить HTML-каркас (<!DOCTYPE>, <html>, <body>, <h1>, <h2>, <ul>, <li>).\n"
                "Разделяй этапы жирным текстом (например, <b>Шаг 1</b>). Для списков используй символ (•).\n"
                "Формулы и выражения обязательно помещай внутрь тегов <code>. Запрещено использовать LaTeX ($).\n"
                "В самом конце обязательно выведи строчку: <b>Ответ: [значение]</b>."
            )
            user_content = f"Задача:\n{strip_html_tags(user_info['task']['task_text'])}"
            
            try:
                ai_response = await gemini_chat_request(contents=user_content, system_instruction=prompt, timeout=200)
                sol_text = clean_ai_html(ai_response)
                
                # ИСПРАВЛЕНО: сохраняем message_id отправленного разбора
                sent_sol = await send_html_message(user_info["chat_id"], f"📝 <b>Официальный разбор решения:</b>\n\n{sol_text}", context=context)
                if sent_sol:
                    user_info.setdefault("msg_answer", []).append(sent_sol.message_id)
                    save_user_data(user_data) # Обязательно сохраняем состояние
                
                await query.edit_message_reply_markup(reply_markup=get_keyboard("next"))
            except Exception as e:
                err_str = str(e)
                logger.error(f"Ошибка генерации разбора: {err_str}")
                
                if "COOLDOWN:" in err_str:
                    wait_sec = err_str.split("COOLDOWN:")[1]
                    err_text = f"⏳ <b>Анти-Спам Google.</b>\nВы исчерпали лимит бесплатных запросов. Пожалуйста, подождите ровно <b>{wait_sec} секунд</b>."
                elif "OVERLOAD" in err_str:
                    err_text = "⚠️ <b>Серверы Google сейчас перегружены.</b>\nСлишком много людей используют ИИ в данный момент. Подождите пару минут."
                else:
                    err_text = "⚠️ Не удалось связаться с ИИ. Попробуйте еще раз."
                
                sent_err = await send_html_message(user_info["chat_id"], err_text, context=context)
                if sent_err:
                    user_info.setdefault("msg_errors", []).append(sent_err.message_id)
                    save_user_data(user_data)
                
            try:
                await context.bot.delete_message(user_info["chat_id"], user_info["msg_think"])
            except Exception: pass
            user_info["msg_think"] = None
            return

        # КНОПКА: ПРОПУСТИТЬ / СЛЕДУЮЩАЯ ЗАДАЧА
        if data in ("skip_task", "next_task"):
            await cleanup_messages(user_info, context)
            user_info["solved"] += 1
            save_user_data(user_data)
            
            if user_info["solved"] >= user_info["num_tasks"]:
                await show_statistics(user_info, context, update)
            else:
                await start_task(update, context, query)
            return

    # 5. Навигация выхода
    if data == "restart_menu":
        await start(update, context)
        return
        
    if data == "finish_session":
        await query.edit_message_reply_markup(None)
        await context.bot.send_message(user_info["chat_id"], "👋 Занятие окончено. Ты отлично поработал сегодня! Приходи снова, когда захочешь порешать задачи.")
        return


# --------------------------
# 🚀 МОДУЛЬ ВЫДАЧИ ЗАДАНИЙ И АНАЛИЗА РЕЗУЛЬТАТОВ
# --------------------------

async def start_task(update: Update, context: ContextTypes.DEFAULT_TYPE, query=None):
    """Выбирает случайную задачу из пула или парсит Project Euler, упаковывая в HTML-интерфейс."""
    user_id = update.effective_user.id
    user_info = user_data[user_id]
    chat_id = user_info["chat_id"]
    user_info["last_input"] = None
    
    if user_info["edu_type"] == "euler":
        if query:
            try:
                await query.edit_message_text("⏳ <i>Подключаюсь к Project Euler и перевожу условие...</i>", parse_mode=ParseMode.HTML)
            except Exception: pass
        
        task = await get_project_euler_problem_async(user_info["history"])
        if not task:
            await send_html_message(chat_id, "❌ Сервер Project Euler временно недоступен или защищен. Пожалуйста, попробуйте позже.", context=context)
            return
    else:
        # Извлечение локальной математической задачи из Excel
        db_key = f"{user_info['edu_type']}_{user_info['class_course']}"
        pool = [t for t in tasks_db.get(db_key, []) if str(t["id"]) not in user_info["history"]]
        
        if not pool:
            await show_statistics(user_info, context, update)
            return
            
        raw_task = random.choice(pool)
        formatted_text = (
            f"📚 <b>Раздел {user_info['class_course']} — Задача №{raw_task['id']}</b>\n\n"
            f"{raw_task['task_text']}\n\n"
            f"<i>Решите задачу на бумаге / в IDE и отправьте числовой ответ в чат.</i>"
        )
        task = {
            "id": str(raw_task["id"]),
            "task_text": formatted_text,
            "answer": raw_task["answer"],
            "type": "math"
        }

    user_info["task"] = task
    user_info["history"].append(task["id"])
    save_user_data(user_data)
    
    if query:
        await edit_html_message(update, task["task_text"], get_keyboard("task"))
    else:
        await send_html_message(chat_id, task["task_text"], get_keyboard("task"), context)


async def process_check(user_id: int, update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Организует проверку ответа студента математическим компаратором или ИИ-валидатором кода."""
    info = user_data[user_id]
    current_task = info["task"]
    user_ans = info["last_input"]
    
    think_msg = await send_thinking(update, "🔍 <i>Экспертная проверка вашего решения...</i>")
    
    if current_task["type"] == "euler":
        # Эвристическая проверка: похоже ли присланное сообщение на код
        if not any(c in user_ans for c in "={}()[]+-*/;") and len(user_ans.split()) < 3:
            is_correct = False
            fail_reason = "Для задач Project Euler требуется отправить именно программный код решения (Python/C++/JS), а не только конечный числовой ответ."
        else:
            # Асинхронная ИИ-валидация присланного кода решения через Gemini
            is_correct, fail_reason = await check_code_with_ai(current_task["task_text"], user_ans)
    else:
        # Строгая математическая сверка числовых значений с Excel
        is_correct = is_correct_math_answer(user_ans, current_task["answer"])
        fail_reason = "Значение не совпало с эталонным математическим ответом учебника."

    try:
        await context.bot.delete_message(info["chat_id"], think_msg.message_id)
    except Exception: pass

    if is_correct:
        info["correct"] += 1
        save_user_data(user_data)
        success_text = "✅ <b>Абсолютно верно!</b> Алгоритм и вычисления идеальны. Великолепная работа!"
        
        if getattr(update, "callback_query", None):
            await update.callback_query.edit_message_text(success_text, parse_mode=ParseMode.HTML, reply_markup=get_keyboard("next"))
        else:
            await send_html_message(info["chat_id"], success_text, get_keyboard("next"), context)
    else:
        error_text = f"❌ <b>Решение не принято.</b>\n\n{fail_reason}\n\n<i>Вы можете исправить ошибку, отправить новый ответ/код или запросить помощь ИИ.</i>"
        sent = await send_html_message(info["chat_id"], error_text, context=context)
        info.setdefault("msg_errors", []).append(sent.message_id)
        save_user_data(user_data)


async def show_statistics(info: dict, context: ContextTypes.DEFAULT_TYPE, update: Update):
    """Генерирует финальный аналитический отчет успеваемости студента за текущую сессию."""
    total = info["num_tasks"]
    correct = info["correct"]
    elapsed = int(time.time() - info["start_time"])
    mins, secs = divmod(elapsed, 60)
    
    stats = (
        "🏁 <b>Учебная сессия успешно завершена!</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⏱ <b>Затраченное время:</b> {mins} мин. {secs} сек.\n"
        f"✅ <b>Правильных решений:</b> {correct} из {total}\n"
        f"📊 <b>Процент успеха:</b> {round((correct / total) * 100, 1)}%\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "<i>Отличный шаг к прокачке hard-скиллов! Выберите дальнейшее действие:</i>"
    )
    
    if getattr(update, "callback_query", None):
        await update.callback_query.edit_message_text(stats, parse_mode=ParseMode.HTML, reply_markup=get_keyboard("end"))
    else:
        await send_html_message(info["chat_id"], stats, get_keyboard("end"), context)


# --------------------------
# 📥 ПЕРЕХВАТ ТЕКСТОВЫХ ОТВЕТОВ СТУДЕНТА И СИСТЕМНЫЕ ОШИБКИ
# --------------------------

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Принимает программный код или числа, отправленные пользователем, очищая мусорные алерты."""
    user_id = update.effective_user.id
    text = update.message.text
    
    if user_id not in user_data:
        user_data[user_id] = {"state": "start", "chat_id": update.effective_chat.id}
    
    info = user_data[user_id]
    
    if info.get("state") == "solving" and info.get("task"):
        info["last_input"] = text
        info["user_messages"].append(update.message.message_id)
        
        # Удаляем прошлые сообщения об ошибках, гарантируя чистоту UX
        if info.get("msg_errors"):
            for mid in info["msg_errors"]:
                try: 
                    await context.bot.delete_message(info["chat_id"], mid)
                except Exception: pass
            info["msg_errors"] = []
            
        save_user_data(user_data)
        # Автоматически запускаем валидацию ответа, чтобы пользователь не ждал
        await process_check(user_id, update, context)
        return

    if info.get("state") == "start":
        await update.message.reply_text("Пожалуйста, нажмите команду /start, чтобы запустить интерактивное меню.")


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    """
    Глобальный системный обработчик непредвиденных сбоев.
    ИСПОЛЬЗУЕТ TRACEBACK, РАЗВОРАЧИВАЯ ПОЛНОЕ ДЕРЕВО ОШИБКИ ДЛЯ ДЕБАГА.
    """
    tb_str = traceback.format_exc()
    logger.critical(f"Критическое исключение в рантайме бота! Обнаружен сбой: {context.error}\nТрассировка стека:\n{tb_str}")


# --------------------------
# 🏁 ТОЧКА ВХОДА (MAIN RUNNER)
# --------------------------

def main():
    """Сборка приложения на базе python-telegram-bot и запуск бесконечного цикла опроса (Polling)."""
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    # Регистрация диспетчеров событий
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # Привязка нашего продвинутого логировщика трассировок
    app.add_error_handler(error_handler)
    
    logger.info("==================================================")
    logger.info("🤖 Модульный ИИ-репетитор УСПЕШНО ЗАПУЩЕН И ГОТОВ К РАБОТЕ!")
    logger.info("==================================================")
    
    app.run_polling()


if __name__ == "__main__":
    main()