# AI Репетитор для Telegram

Telegram-бот для решения математических задач и задач по программированию с использованием Google Gemini.

## Возможности

### Математические задачи

* Загрузка задач из Excel-файла (`tasks.xlsx`)
* Поддержка школьных классов (5–11)
* Поддержка вузовских курсов (1–2)
* Проверка числовых ответов
* Подсчёт статистики решённых задач

### Project Euler

* Получение случайных задач с сайта Project Euler
* Перевод условия на русский язык через Gemini
* Проверка программных решений с помощью ИИ

### Подсказки и разборы

Для каждой задачи доступны:

* Подсказка от ИИ
* Пошаговый разбор решения
* Проверка ответа
* Переход к следующей задаче

### Статистика

После завершения сессии выводится:

* количество решённых задач;
* количество правильных ответов;
* процент успешности;
* время выполнения.

## Используемые технологии

* Python
* python-telegram-bot
* Google Gemini API
* Pandas
* Requests
* BeautifulSoup4
* AsyncIO

## Структура проекта

```text
.
├── math_test.py
├── tasks.xlsx
├── user_data.json
├── .env
└── README.md
```

## Установка

Установить зависимости:

```bash
pip install python-telegram-bot
pip install google-genai
pip install pandas
pip install requests
pip install beautifulsoup4
pip install python-dotenv
```

или через requirements.txt.

## Настройка

Создать файл `.env`:

```env
TELEGRAM_BOT_TOKEN=YOUR_TELEGRAM_TOKEN
GEMINI_API_KEY=YOUR_GEMINI_API_KEY
```

## Запуск

```bash
python math_test.py
```

## Формат базы задач

Каждый лист Excel представляет отдельный раздел.

Примеры названий листов:

```text
school_5
school_6
school_7
school_8
school_9
school_10
school_11

uni_1
uni_2
```

Обязательные столбцы:

| Поле      | Описание             |
| --------- | -------------------- |
| id        | идентификатор задачи |
| task_text | условие              |
| answer    | правильный ответ     |

## Команды

### /start

Запуск бота и выбор режима обучения.

## Что реализовано

* Асинхронная работа с Telegram API
* Асинхронная работа с Gemini
* Хранение данных пользователей в JSON
* Проверка числовых ответов через Decimal
* Обработка ошибок Gemini API
* Защита от одновременной записи пользовательских данных
* Парсинг задач Project Euler

## Автор

Pet-проект для изучения Python, Telegram Bot API и интеграции LLM-моделей.
