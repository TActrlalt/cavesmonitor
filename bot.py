#!/usr/bin/env python3
import json
import logging
import datetime
import html
import os
import io

import pandas as pd

from datetime import timezone, timedelta

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    WebAppInfo,
    KeyboardButton,
    ReplyKeyboardMarkup,
    Chat
)
from telegram.constants import ParseMode, ChatType
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    ChatMemberHandler,
    filters
)
from telegram.error import Conflict

# ------------------ ВАЖНО: ВПИСЫВАЕМ АДМИН ID ------------------
# Здесь указываем ID пользователей, которым разрешено удалять ЛЮБЫЕ формы
ADMIN_USERS = [
    ,   # замените на нужные вам ID
    
]
# ---------------------------------------------------------------

# Токен вашего бота
TOKEN = ""

# ---------------------------------------------------------------------------
# Укажите ID чата для форм (сообщения формы) и для алармов/мониторинга.
# Замените значения ниже на нужные ID.
FORM_CHAT_ID = -    # TODO: Замените на ID чата для форм
ALARM_CHAT_ID = -   # TODO: Замените на ID чата для алармов и мониторинга
# ---------------------------------------------------------------------------

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Файлы для сохранения данных
FORMS_FILE = "active_forms.json"       # Активные формы
KNOWN_CHATS_FILE = "known_chats.json"  # Известные чаты
JOURNAL_FILE = "journal_forms.json"    # Журнал всех форм

TZ_LOCAL = timezone(timedelta(hours=3))

# Глобовый словарь активных форм.
active_forms = {}

# Глобовый список всех форм (журнал).
journal_forms = []

# Глобовый словарь известных чатов {chat_id: chat_title}
known_chats = {}

def load_forms():
    global active_forms
    if os.path.exists(FORMS_FILE):
        try:
            with open(FORMS_FILE, "r", encoding="utf-8") as f:
                active_forms = json.load(f)
            logger.info("Формы успешно загружены из файла.")
        except Exception as e:
            logger.error(f"Ошибка загрузки форм: {e}")
            active_forms = {}
    else:
        active_forms = {}

def save_forms():
    try:
        with open(FORMS_FILE, "w", encoding="utf-8") as f:
            json.dump(active_forms, f, ensure_ascii=False, indent=4, default=str)
    except Exception as e:
        logger.error(f"Ошибка сохранения форм: {e}")

def load_known_chats():
    global known_chats
    if os.path.exists(KNOWN_CHATS_FILE):
        try:
            with open(KNOWN_CHATS_FILE, "r", encoding="utf-8") as f:
                known_chats = json.load(f)
            logger.info("Известные чаты загружены.")
        except Exception as e:
            logger.error(f"Ошибка загрузки KNOWN_CHATS_FILE: {e}")
            known_chats = {}
    else:
        known_chats = {}

def save_known_chats():
    try:
        with open(KNOWN_CHATS_FILE, "w", encoding="utf-8") as f:
            json.dump(known_chats, f, ensure_ascii=False, indent=4)
    except Exception as e:
        logger.error(f"Ошибка сохранения KNOWN_CHATS_FILE: {e}")

def load_journal():
    global journal_forms
    if os.path.exists(JOURNAL_FILE):
        try:
            with open(JOURNAL_FILE, "r", encoding="utf-8") as f:
                journal_forms = json.load(f)
            logger.info("Журнал форм успешно загружен.")
        except Exception as e:
            logger.error(f"Ошибка загрузки журнала форм: {e}")
            journal_forms = []
    else:
        journal_forms = []

def save_journal():
    try:
        with open(JOURNAL_FILE, "w", encoding="utf-8") as f:
            json.dump(journal_forms, f, ensure_ascii=False, indent=4, default=str)
    except Exception as e:
        logger.error(f"Ошибка сохранения журнала форм: {e}")

def get_form_summary(form_data: dict) -> str:
    """Формирование отчёта по форме с использованием HTML-форматирования."""
    summary = "<b></b>\n"
    summary += f"<b>Система:</b> {html.escape(form_data.get('system', '—'))}\n"
    summary += f"<b>Имя:</b> {html.escape(form_data.get('name', '—'))}\n"
    summary += f"<b>Дата ухода:</b> {html.escape(form_data.get('date_down', '—'))} {html.escape(form_data.get('time_down', '—'))}\n"
    summary += f"<b>Дата выхода:</b> {html.escape(form_data.get('date_up', '—'))} {html.escape(form_data.get('time_up', '—'))}\n"
    summary += f"<b>Контрольное время:</b> {html.escape(form_data.get('control', '—'))}\n"
    summary += f"<b>Участники:</b>\n{html.escape(form_data.get('participants', '—'))}\n"
    summary += f"<b>Цель:</b> {html.escape(form_data.get('purpose', '—'))}\n"
    summary += f"<b>Телефон:</b> {html.escape(form_data.get('phone', '—'))}\n"
    summary += f"<b>Дополнительно:</b> {html.escape(form_data.get('additional', '—'))}\n"
    return summary

async def send_to_reports(context: ContextTypes.DEFAULT_TYPE, text: str, parse_mode=ParseMode.HTML, reply_to_map: dict = None, alarm_only: bool = False) -> list:
    """
    Отправка сообщений.
    Если alarm_only==True – отправляем сообщение в чат для алармов,
    иначе – в чат для форм.
    """
    msg_ids = []
    if alarm_only:
        destination_chats = [ALARM_CHAT_ID]
    else:
        destination_chats = [FORM_CHAT_ID]
    for chat_id in destination_chats:
        kwargs = {"parse_mode": parse_mode}
        if reply_to_map and (chat_id in reply_to_map):
            kwargs["reply_to_message_id"] = reply_to_map[chat_id]
        try:
            msg = await context.bot.send_message(chat_id, text, **kwargs)
            msg_ids.append(msg.message_id)
        except Exception as e:
            logger.error(f"Ошибка при отправке в чат {chat_id}: {e}")
    return msg_ids

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_type = update.effective_chat.type
    
    # Если сообщение из группового чата:
    if chat_type != ChatType.PRIVATE:
        chat = update.effective_chat
        text = f"Этот чат имеет ID: {chat.id}"
        # Формируем deep link в личный чат с ботом, который передаёт параметр /start
        deep_link = f"https://t.me/{context.bot.username}?start=start"
        button = InlineKeyboardButton(text="Заполнить форму ✍️", url=deep_link)
        reply_markup = InlineKeyboardMarkup([[button]])
        await update.message.reply_text(text, reply_markup=reply_markup)
        # Сохраняем чат в known_chats (если название отсутствует, используем тип чата)
        known_chats[str(chat.id)] = chat.title if chat.title else f"{chat.type} {chat.id}"
        save_known_chats()
        return

    # В личном чате:
    await update.message.reply_text("Привет! Используйте команды /start, /info, /journal и другие для работы с ботом.")
    
    # Отправляем кнопку для заполнения формы (через Web App)
    web_app_button = KeyboardButton(
        text="Заполнить форму ✍️",
        web_app=WebAppInfo(url="https://panelhouses.ru/scform.html")
    )
    reply_markup = ReplyKeyboardMarkup(
        [[web_app_button]],
        resize_keyboard=True,
        one_time_keyboard=True
    )
    await update.message.reply_text("Нажмите кнопку ниже для заполнения формы:", reply_markup=reply_markup)

async def web_app_data_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Логируем полное обновление для отладки
    logger.info("Получено обновление web_app_data: %s", update.to_dict())
    
    if update.message and update.message.web_app_data:
        user = update.effective_user

        if str(user.id) in active_forms:
            await update.message.reply_text("У вас уже есть активная форма. Дождитесь завершения предыдущей записи.")
            return

        data_str = update.message.web_app_data.data
        try:
            form_data = json.loads(data_str)
        except json.JSONDecodeError as e:
            # Попытка исправить возможные проблемы с кодировкой
            try:
                data_str_fixed = data_str.encode("latin-1").decode("utf-8")
                form_data = json.loads(data_str_fixed)
            except Exception as e2:
                await update.message.reply_text("Ошибка обработки данных формы.")
                logger.error(f"Ошибка загрузки данных формы: {e2}")
                return
        except Exception as e:
            await update.message.reply_text("Ошибка обработки данных формы.")
            logger.error(f"Ошибка загрузки данных формы: {e}")
            return

        if user.username:
            username = f"@{user.username}"
        else:
            username = user.full_name
        original_name = form_data.get("name", "—")
        # Формируем имя с учётом username
        form_data["name"] = f"{original_name} ({username})"
        
        #####################################################################
        # ЕСЛИ КОНТРОЛЬНОЕ ВРЕМЯ (HH:MM) <= времени выхода, то увеличиваем дату на 1 день
        # и записываем результат обратно в form_data["control"] в формате YYYY-MM-DD HH:MM.
        #####################################################################
        date_up_str = form_data.get("date_up")
        time_up_str = form_data.get("time_up")
        control_str = form_data.get("control")

        if date_up_str and time_up_str and control_str:
            try:
                # Парсим дату/время выхода
                local_exit_dt_local = datetime.datetime.strptime(
                    f"{date_up_str} {time_up_str}",
                    "%Y-%m-%d %H:%M"
                ).replace(tzinfo=TZ_LOCAL)

                # Пробуем понять, задан ли control сразу с датой (YYYY-MM-DD HH:MM) или только (HH:MM)
                try:
                    # Если control_str = "2025-02-21 12:00"
                    local_control_dt_local = datetime.datetime.strptime(
                        control_str, "%Y-%m-%d %H:%M"
                    ).replace(tzinfo=TZ_LOCAL)
                except ValueError:
                    # Иначе считаем, что там только "HH:MM"
                    temp_local_control = datetime.datetime.strptime(
                        f"{date_up_str} {control_str}",
                        "%Y-%m-%d %H:%M"
                    ).replace(tzinfo=TZ_LOCAL)

                    # Если контроль <= выхода, прибавляем сутки
                    if temp_local_control <= local_exit_dt_local:
                        temp_local_control += datetime.timedelta(days=1)

                    local_control_dt_local = temp_local_control

                # Формируем итоговую строку "YYYY-MM-DD HH:MM" для записи в form_data["control"]
                corrected_str = local_control_dt_local.strftime("%Y-%m-%d %H:%M")
                form_data["control"] = corrected_str

            except Exception as e:
                logger.warning(f"Ошибка вычисления контрольного времени: {e}")
        #####################################################################

        summary_text = get_form_summary(form_data)
        # Отправляем отчёт с HTML‑форматированием в чат для форм и в чат для алармов
        report_msg_ids_form = await send_to_reports(context, summary_text, parse_mode=ParseMode.HTML, alarm_only=False)
        report_msg_ids_alarm = await send_to_reports(context, summary_text, parse_mode=ParseMode.HTML, alarm_only=True)
        report_msg_ids = report_msg_ids_form + report_msg_ids_alarm
        chat_ids = [FORM_CHAT_ID, ALARM_CHAT_ID]

        # Сохраняем только необходимые данные для мониторинга
        record = {
            "report_msg_ids": report_msg_ids,
            "chat_ids": chat_ids,
            "date_up": form_data.get("date_up"),
            "time_up": form_data.get("time_up"),
            "control": form_data.get("control"),
            "filled_at": datetime.datetime.utcnow().isoformat(),
            "not_exited_notified": False,
            "alarm_notified": False,
            "user_id": user.id,
            "username": username,
            "system": form_data.get("system")
        }
        active_forms[str(user.id)] = record
        save_forms()

        # Добавляем запись в журнал всех форм
        journal_forms.append(record)
        save_journal()

        await update.message.reply_text("✅ Форма успешно отправлена!")
    else:
        await update.message.reply_text("Нет данных веб‑приложения.")

async def test_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    await update.message.reply_text(f"Бот работает. ID чата: {chat_id} ✅")

async def group_reply_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Проверяем, что сообщение является ответом и его текст строго равен одному из нужных вариантов
    if update.message and update.message.reply_to_message and update.message.text:
        text = update.message.text.strip()
        # Приводим текст к верхнему регистру для точного сравнения.
        if text.upper() not in {"ВЫШЕЛ", "ВЫШЛА", "ВЫШЛИ", "ВЫБРОС"}:
            return

        reply_msg = update.message.reply_to_message
        attempt_user_id = update.effective_user.id

        for uid, form in list(active_forms.items()):
            if reply_msg.message_id in form.get("report_msg_ids", []):
                # Разрешаем удалять форму, если это автор формы ИЛИ пользователь в ADMIN_USERS
                if (int(uid) == attempt_user_id) or (attempt_user_id in ADMIN_USERS):
                    del active_forms[uid]
                    save_forms()
                    await update.message.reply_text("👍 Форма удалена (статус: вышел).")
                    logger.info(f"Пользователь {uid} вышел (удалил форму), форма удалена.")
                    try:
                        original_text = reply_msg.text
                        if original_text:
                            new_text = original_text + "\n\n✅ Пользователь вышел."
                            await context.bot.edit_message_text(
                                new_text,
                                chat_id=reply_msg.chat_id,
                                message_id=reply_msg.message_id,
                                parse_mode=ParseMode.HTML
                            )
                    except Exception as e:
                        logger.error(f"Ошибка при редактировании сообщения для пользователя {uid}: {e}")
                else:
                    await update.message.reply_text("❌ Форму может удалить только её автор или администратор.")
                break

async def count_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    systems = {}
    for form in active_forms.values():
        sys_name = form.get("system")
        if sys_name is not None:
            systems.setdefault(sys_name, 0)
            systems[sys_name] += 1
    if not systems:
        await update.message.reply_text("Нет активных записей.")
        return
    lines = [f"Активные записи: {len(active_forms)}"]
    for uid, form in active_forms.items():
        username = form.get("username", str(uid))
        chat_ids = form.get("chat_ids", [])
        report_msg_ids = form.get("report_msg_ids", [])
        if report_msg_ids and chat_ids:
            links = []
            for cid, mid in zip(chat_ids, report_msg_ids):
                cid_str = str(cid)
                if cid_str.startswith("-100"):
                    links.append(f"https://t.me/c/{cid_str[4:]}/{mid}")
                else:
                    links.append("Нет ссылки")
            link = " | ".join(links)
        else:
            link = "Нет ссылки"
        lines.append(f"• {username}: {link}")
    await update.message.reply_text("\n".join(lines))

async def status_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not active_forms:
        await update.message.reply_text("Активных форм нет.")
        return
    lines = [f"Статус активных форм: {len(active_forms)}"]
    for uid, form in active_forms.items():
        username = form.get("username", str(uid))
        date_up = form.get("date_up", "—")
        time_up = form.get("time_up", "—")
        chat_ids = form.get("chat_ids", [])
        report_msg_ids = form.get("report_msg_ids", [])
        if report_msg_ids and chat_ids:
            links = []
            for cid, mid in zip(chat_ids, report_msg_ids):
                cid_str = str(cid)
                if cid_str.startswith("-100"):
                    links.append(f"https://t.me/c/{cid_str[4:]}/{mid}")
                else:
                    links.append("Нет ссылки")
            link = " | ".join(links)
        else:
            link = "Нет ссылки"
        lines.append(f"• {username} (выход {date_up} {time_up}): {link}")
    await update.message.reply_text("\n".join(lines))

async def send_shraficheskie_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_to_reports(context, "Шрафичечски 😜", alarm_only=True)
    await update.message.reply_text("✅ Сообщение 'Шрафичечски' отправлено в чат для алармов.")

async def info_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Команда /info работает только в личном чате и выдаёт Excel с активными формами
    if update.effective_chat.type != ChatType.PRIVATE:
        return
    if not active_forms:
        await update.message.reply_text("Нет активных форм для формирования отчёта.")
        return

    data = []
    for uid, form in active_forms.items():
        chat_ids = form.get("chat_ids", [])
        report_msg_ids = form.get("report_msg_ids", [])
        if report_msg_ids and chat_ids:
            links = []
            for cid, mid in zip(chat_ids, report_msg_ids):
                cid_str = str(cid)
                if cid_str.startswith("-100"):
                    links.append(f"https://t.me/c/{cid_str[4:]}/{mid}")
                else:
                    links.append("Нет ссылки")
            link = " | ".join(links)
        else:
            link = "Нет ссылки"
        data.append({
            "User ID": form.get("user_id"),
            "Username": form.get("username"),
            "System": form.get("system"),
            "Дата выхода": form.get("date_up"),
            "Время выхода": form.get("time_up"),
            "Контроль": form.get("control"),
            "Заполнено (UTC)": form.get("filled_at"),
            "Не вышел уведомлено": form.get("not_exited_notified"),
            "Аларм уведомлено": form.get("alarm_notified"),
            "Отчёт": link
        })
    df = pd.DataFrame(data)
    
    excel_buffer = io.BytesIO()
    with pd.ExcelWriter(excel_buffer, engine="xlsxwriter") as writer:
        df.to_excel(writer, index=False, sheet_name="Статистика (Активные)")
    excel_buffer.seek(0)
    
    await update.message.reply_document(document=excel_buffer, filename="active_forms.xlsx", caption="Статистика активных форм.")

async def journal_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Команда /journal работает только в личном чате и выдаёт Excel с журналом всех форм
    if update.effective_chat.type != ChatType.PRIVATE:
        return
    if not journal_forms:
        await update.message.reply_text("Журнал форм пуст.")
        return

    data = []
    for record in journal_forms:
        chat_ids = record.get("chat_ids", [])
        report_msg_ids = record.get("report_msg_ids", [])
        if report_msg_ids and chat_ids:
            links = []
            for cid, mid in zip(chat_ids, report_msg_ids):
                cid_str = str(cid)
                if cid_str.startswith("-100"):
                    links.append(f"https://t.me/c/{cid_str[4:]}/{mid}")
                else:
                    links.append("Нет ссылки")
            link = " | ".join(links)
        else:
            link = "Нет ссылки"
        data.append({
            "User ID": record.get("user_id"),
            "Username": record.get("username"),
            "System": record.get("system"),
            "Дата выхода": record.get("date_up"),
            "Время выхода": record.get("time_up"),
            "Контроль": record.get("control"),
            "Заполнено (UTC)": record.get("filled_at"),
            "Не вышел уведомлено": record.get("not_exited_notified"),
            "Аларм уведомлено": record.get("alarm_notified"),
            "Отчёт": link
        })
    df = pd.DataFrame(data)
    
    excel_buffer = io.BytesIO()
    with pd.ExcelWriter(excel_buffer, engine="xlsxwriter") as writer:
        df.to_excel(writer, index=False, sheet_name="Журнал")
    excel_buffer.seek(0)
    
    await update.message.reply_document(document=excel_buffer, filename="journal.xlsx", caption="Журнал всех форм.")

async def monitor_underground_count(context: ContextTypes.DEFAULT_TYPE):
    """
    Периодическая отправка статистики по количеству активных форм.
    Если активных форм нет — не отправляем сообщение.
    """
    count = len(active_forms)
    if count == 0:
        # Если активных форм нет, ничего не отправляем
        return

    lines = [f"📊 Активных записей: {count}"]
    for uid, form in active_forms.items():
        username = form.get("username", str(uid))
        chat_ids = form.get("chat_ids", [])
        report_msg_ids = form.get("report_msg_ids", [])
        if report_msg_ids and chat_ids:
            links = []
            for cid, mid in zip(chat_ids, report_msg_ids):
                cid_str = str(cid)
                if cid_str.startswith("-100"):
                    links.append(f"https://t.me/c/{cid_str[4:]}/{mid}")
                else:
                    links.append("Нет ссылки")
            link = " | ".join(links)
        else:
            link = "Нет ссылки"
        lines.append(f"• {username}: {link}")
    summary_text = "\n".join(lines)
    await send_to_reports(context, summary_text, alarm_only=True)

async def monitor_exit_deadlines(context: ContextTypes.DEFAULT_TYPE):
    """
    Периодическая проверка: если время выхода прошло, а форма не закрыта — уведомляем.
    Если время контрольное (с датой!) прошло, уведомляем об аларме.
    Если в форме не передана дата для контроля, используем дату выхода.
    Если контрольное время (HH:MM) <= времени выхода, считаем, что контроль — это следующий день.
    """
    now = datetime.datetime.now(timezone.utc)
    logger.info(f"⏰ Мониторинг сроков: сейчас {now}")
    for uid, form in list(active_forms.items()):
        try:
            date_up_str = form.get("date_up")
            time_up_str = form.get("time_up")
            control_str = form.get("control")

            logger.info(f"Проверка для пользователя {uid}: date_up={date_up_str}, time_up={time_up_str}, control={control_str}")

            # Дата и время выхода
            local_exit_dt_local = datetime.datetime.strptime(
                f"{date_up_str} {time_up_str}",
                "%Y-%m-%d %H:%M"
            ).replace(tzinfo=TZ_LOCAL)
            local_exit_dt = local_exit_dt_local.astimezone(timezone.utc)

            # Проверяем контрольное время
            try:
                # Предполагаем, что control_str может быть "YYYY-MM-DD HH:MM"
                local_control_dt = datetime.datetime.strptime(control_str, "%Y-%m-%d %H:%M") \
                    .replace(tzinfo=TZ_LOCAL).astimezone(timezone.utc)
            except:
                # Значит, control_str — только "HH:MM", добавим дату выхода
                temp_local_control = datetime.datetime.strptime(
                    f"{date_up_str} {control_str}",
                    "%Y-%m-%d %H:%M"
                ).replace(tzinfo=TZ_LOCAL)

                # Если контрольный временной момент <= времени выхода — считаем, что контроль на следующий день
                if temp_local_control <= local_exit_dt_local:
                    temp_local_control += datetime.timedelta(days=1)

                local_control_dt = temp_local_control.astimezone(timezone.utc)

            reply_map = {}
            chat_ids = form.get("chat_ids", [])
            report_msg_ids = form.get("report_msg_ids", [])
            for cid, mid in zip(chat_ids, report_msg_ids):
                reply_map[cid] = mid

            # 1) Предупреждение: время выхода прошло, а not_exited_notified ещё нет
            if now > local_exit_dt and not form.get("not_exited_notified"):
                user_mention = f'<a href="tg://user?id={form.get("user_id")}">' \
                               f'{html.escape(form.get("username"))}</a>'
                msg = (f"🚨 {user_mention} не вышел к назначенному времени "
                       f"(было: {html.escape(date_up_str)} {html.escape(time_up_str)}).")
                # Отправляем предупреждение в чат для алармов
                await send_to_reports(context, msg, parse_mode=ParseMode.HTML, reply_to_map=reply_map, alarm_only=True)
                logger.info(msg)
                form["not_exited_notified"] = True
                save_forms()

            # 2) Аларм: время контрольное прошло, а alarm_notified ещё нет
            if now > local_control_dt and not form.get("alarm_notified"):
                user_mention = f'<a href="tg://user?id={form.get("user_id")}">' \
                               f'{html.escape(form.get("username"))}</a>'
                msg = (f"🔥 Аларм! {user_mention} задержался сверх контрольного времени "
                       f"(было: {html.escape(date_up_str)} {html.escape(control_str)}).")
                # Отправляем аларм в чат для алармов
                await send_to_reports(context, msg, parse_mode=ParseMode.HTML, reply_to_map=reply_map, alarm_only=True)
                logger.info(msg)
                form["alarm_notified"] = True
                save_forms()

        except Exception as e:
            logger.error(f"Ошибка при проверке формы пользователя {uid}: {e}")

async def my_chat_member_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Обработчик обновлений о смене статуса бота в чате.
    Если бот добавлен в группу, сохраняем информацию о чате.
    """
    chat = update.my_chat_member.chat
    # Сохраняем только групповые или супергрупповые чаты
    if chat.type in [Chat.GROUP, Chat.SUPERGROUP]:
        known_chats[str(chat.id)] = chat.title if chat.title else f"{chat.type} {chat.id}"
        save_known_chats()
        logger.info(f"Обновление членства в чате: {chat.id} - {known_chats[str(chat.id)]}")

def main():
    # Загружаем сохранённые формы, журнал и известные чаты при запуске
    load_forms()
    load_known_chats()
    load_journal()
    
    application = ApplicationBuilder().token(TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA, web_app_data_handler))
    application.add_handler(CommandHandler("test", test_handler))
    application.add_handler(CommandHandler("sendshraficheskie", send_shraficheskie_handler))
    application.add_handler(CommandHandler("count", count_handler))
    application.add_handler(CommandHandler("status", status_handler))
    application.add_handler(CommandHandler("info", info_handler))
    application.add_handler(CommandHandler("journal", journal_handler))
    application.add_handler(MessageHandler(filters.REPLY & filters.TEXT, group_reply_handler))
    # Обработчик обновлений о членстве бота в чате
    application.add_handler(ChatMemberHandler(my_chat_member_handler, ChatMemberHandler.MY_CHAT_MEMBER))

    application.add_error_handler(lambda update, context: logger.error("Exception while handling an update:", exc_info=context.error))

    job_queue = application.job_queue
    # Каждые 4 часа отправляем статистику (но только если есть активные формы)
    job_queue.run_repeating(monitor_underground_count, interval=14400, first=10)
    # Каждые 5 минут проверяем сроки выхода и контрольное время
    job_queue.run_repeating(monitor_exit_deadlines, interval=300, first=20)

    logger.info("Бот запущен. Ожидание обновлений... 🚀")
    application.run_polling()

if __name__ == '__main__':
    main()
