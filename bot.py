import os
import re
import json
import asyncio
import logging
import html
import subprocess
from datetime import datetime
from typing import Dict, Optional, Any, List

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, CommandObject
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile
from aiogram.exceptions import TelegramBadRequest

import config
from antigravity_bridge import AntigravityRunner
from telegram_formatter import md_to_telegram_html, split_telegram_html

# Configure dual logging: console + log file
log_dir = os.path.join(os.path.dirname(__file__), "logs")
os.makedirs(log_dir, exist_ok=True)
log_file_path = os.path.join(log_dir, "bot_activity.log")

logger = logging.getLogger("AntigravityBot")
logger.setLevel(logging.INFO)

formatter = logging.Formatter("%(asctime)s - [%(levelname)s] - %(message)s")

# Console Handler
stream_handler = logging.StreamHandler()
stream_handler.setFormatter(formatter)
logger.addHandler(stream_handler)

# File Handler
file_handler = logging.FileHandler(log_file_path, encoding="utf-8")
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)

if not config.BOT_TOKEN:
    raise ValueError("BOT_TOKEN is not set in environment or .env file!")

bot = Bot(token=config.BOT_TOKEN)
dp = Dispatcher()
runner = AntigravityRunner()

AVAILABLE_MODELS = [
    ("gemini-3.6-flash-high", "⚡ Gemini 3.6 Flash (High)"),
    ("gemini-3.6-flash-medium", "⚡ Gemini 3.6 Flash (Med)"),
    ("gemini-3.1-pro-high", "🧠 Gemini 3.1 Pro (High)"),
    ("claude-sonnet-4-6", "🎭 Claude Sonnet 4.6"),
    ("claude-opus-4-6-thinking", "🔥 Claude Opus 4.6 (Thinking)"),
    ("gpt-oss-120b-medium", "🤖 GPT-OSS 120B"),
]

AVAILABLE_EFFORTS = ["low", "medium", "high"]
AVAILABLE_MODES = [("accept-edits", "✏️ Normal (Accept Edits)"), ("plan", "📋 Plan Only")]

BRAIN_DIR = os.path.expanduser("~/.gemini/antigravity-cli/brain")
user_sessions: Dict[int, Dict[str, Any]] = {}

def get_session(user_id: int) -> Dict[str, Any]:
    if user_id not in user_sessions:
        user_sessions[user_id] = {
            "conversation_id": None,
            "workspace": config.WORKSPACE_DIR,
            "model": config.DEFAULT_MODEL or "gemini-3.6-flash-high",
            "effort": "high",
            "mode": "accept-edits",
            "sent_message_ids": [],
        }
    return user_sessions[user_id]

def is_allowed(user_id: int) -> bool:
    if not config.ALLOWED_USER_IDS:
        return True
    return user_id in config.ALLOWED_USER_IDS

def get_available_sessions(limit: int = 8) -> List[Dict[str, Any]]:
    sessions = []
    if not os.path.exists(BRAIN_DIR):
        return sessions

    try:
        dirs = [d for d in os.listdir(BRAIN_DIR) if os.path.isdir(os.path.join(BRAIN_DIR, d))]
        dirs.sort(key=lambda d: os.path.getmtime(os.path.join(BRAIN_DIR, d)), reverse=True)

        for conv_id in dirs[:limit]:
            log_file = os.path.join(BRAIN_DIR, conv_id, ".system_generated", "logs", "transcript.jsonl")
            title = "New / Empty session"
            if os.path.exists(log_file):
                try:
                    with open(log_file, "r", encoding="utf-8") as f:
                        for line in f:
                            data = json.loads(line)
                            if data.get("type") == "USER_INPUT":
                                raw = data.get("content", "")
                                match = re.search(r"<USER_REQUEST>\s*(.*?)\s*(?:</USER_REQUEST>|\n\n\[Telegram|\n<ADDITIONAL)", raw, re.DOTALL)
                                if match:
                                    title = match.group(1).strip()
                                else:
                                    title = raw[:60].strip()
                                break
                except Exception:
                    pass
            mtime = os.path.getmtime(os.path.join(BRAIN_DIR, conv_id))
            dt_str = datetime.fromtimestamp(mtime).strftime("%d.%m %H:%M")
            sessions.append({
                "conv_id": conv_id,
                "title": title[:35] or "Untitled",
                "mtime_str": dt_str
            })
    except Exception as e:
        logger.error(f"Error scanning brain sessions: {e}")

    return sessions

def extract_session_messages(conv_id: str, max_dialogues: int = 3) -> List[Dict[str, str]]:
    log_file = os.path.join(BRAIN_DIR, conv_id, ".system_generated", "logs", "transcript.jsonl")
    if not os.path.exists(log_file):
        return []

    dialogues = []
    current_user_text = None

    try:
        with open(log_file, "r", encoding="utf-8") as f:
            for line in f:
                data = json.loads(line)
                stype = data.get("type")
                source = data.get("source")

                if stype == "USER_INPUT":
                    raw = data.get("content", "")
                    match = re.search(r"<USER_REQUEST>\s*(.*?)\s*(?:</USER_REQUEST>|\n\n\[Telegram|\n<ADDITIONAL)", raw, re.DOTALL)
                    if match:
                        current_user_text = match.group(1).strip()
                    else:
                        clean_raw = re.sub(r"<[^>]+>", "", raw).strip()
                        current_user_text = clean_raw[:300]
                elif stype == "PLANNER_RESPONSE" and source == "MODEL":
                    model_text = data.get("content", "").strip()
                    if current_user_text and model_text:
                        dialogues.append({
                            "user": current_user_text,
                            "model": model_text
                        })
                        current_user_text = None
    except Exception as e:
        logger.error(f"Error reading transcript for {conv_id}: {e}")

    return dialogues[-max_dialogues:]

async def track_and_answer(chat_id: int, user_id: int, text: str, parse_mode: Optional[str] = "HTML", reply_markup: Optional[InlineKeyboardMarkup] = None) -> types.Message:
    sent = await bot.send_message(chat_id, text, parse_mode=parse_mode, reply_markup=reply_markup)
    session = get_session(user_id)
    session["sent_message_ids"].append(sent.message_id)
    if len(session["sent_message_ids"]) > 50:
        session["sent_message_ids"] = session["sent_message_ids"][-50:]
    return sent

async def clear_chat_messages(chat_id: int, user_id: int):
    session = get_session(user_id)
    msg_ids = list(session["sent_message_ids"])
    session["sent_message_ids"] = []
    for mid in reversed(msg_ids):
        try:
            await bot.delete_message(chat_id, mid)
        except Exception:
            pass

@dp.message(F.from_user.id.func(lambda uid: not is_allowed(uid)))
async def handle_unauthorized(message: types.Message):
    logger.warning(f"⛔ [SECURITY] Unauthorized access attempt by user_id={message.from_user.id} (@{message.from_user.username})")
    await message.answer("⛔ Access Denied: You are not authorized to use this bot.")

def get_main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🤖 Model", callback_data="open_model_menu"),
            InlineKeyboardButton(text="⚙️ Effort", callback_data="open_effort_menu"),
            InlineKeyboardButton(text="📋 Mode", callback_data="open_mode_menu")
        ],
        [
            InlineKeyboardButton(text="📜 Resume Session", callback_data="open_resume_menu"),
            InlineKeyboardButton(text="✨ New Session", callback_data="menu_new_session"),
            InlineKeyboardButton(text="📊 Status", callback_data="menu_status")
        ]
    ])

async def safe_edit_text(message: types.Message, text: str, reply_markup: Optional[InlineKeyboardMarkup] = None, parse_mode: Optional[str] = None):
    try:
        await message.edit_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            pass
        else:
            logger.warning(f"TelegramBadRequest in safe_edit_text: {e}")
    except Exception as e:
        logger.error(f"Unexpected error in safe_edit_text: {e}")

@dp.message(Command("start"))
@dp.message(Command("menu"))
async def cmd_menu(message: types.Message):
    user_id = message.from_user.id
    logger.info(f"📥 [COMMAND] User {user_id} issued /menu or /start")
    session = get_session(user_id)
    text = (
        "👋 <b>Antigravity AI Control Panel</b>\n\n"
        f"🤖 <b>Model</b>: <code>{session['model']}</code>\n"
        f"⚙️ <b>Effort</b>: <code>{session['effort']}</code>\n"
        f"📋 <b>Mode</b>: <code>{session['mode']}</code>\n"
        f"📁 <b>Workspace</b>: <code>{session['workspace']}</code>\n"
        f"🆔 <b>Active Session</b>: <code>{session['conversation_id'] or 'New Context'}</code>\n\n"
        "Quick commands: /resume (select past session), /new (clear chat & new session)"
    )
    await track_and_answer(message.chat.id, user_id, text, parse_mode="HTML", reply_markup=get_main_menu_keyboard())

@dp.message(Command("resume"))
@dp.callback_query(F.data == "open_resume_menu")
async def cmd_resume(event: types.Message | types.CallbackQuery, command: Optional[CommandObject] = None):
    user_id = event.from_user.id
    chat_id = event.chat.id if isinstance(event, types.Message) else event.message.chat.id
    session = get_session(user_id)

    # Check if a specific conv_id was provided as command argument
    target_conv_id = None
    if isinstance(event, types.Message) and command and command.args:
        target_conv_id = command.args.strip()

    if target_conv_id:
        await activate_and_populate_session(chat_id, user_id, target_conv_id)
        return

    sessions = get_available_sessions(limit=8)
    if not sessions:
        text = "ℹ️ No past sessions found in agy brain storage."
        if isinstance(event, types.CallbackQuery):
            await event.answer()
            await event.message.answer(text)
        else:
            await track_and_answer(chat_id, user_id, text)
        return

    buttons = []
    for s in sessions:
        active_mark = "✅ " if session["conversation_id"] == s["conv_id"] else ""
        btn_text = f"{active_mark}[{s['mtime_str']}] {s['title']}"
        buttons.append([InlineKeyboardButton(text=btn_text, callback_data=f"resume_conv:{s['conv_id']}")])

    buttons.append([InlineKeyboardButton(text="🔙 Back to Menu", callback_data="open_main_menu")])
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)

    text = "📜 <b>Select a previous session to resume (/resume):</b>"

    if isinstance(event, types.CallbackQuery):
        await event.answer()
        await safe_edit_text(event.message, text, reply_markup=kb, parse_mode="HTML")
    else:
        await track_and_answer(chat_id, user_id, text, parse_mode="HTML", reply_markup=kb)

@dp.callback_query(F.data.startswith("resume_conv:"))
async def cb_resume_conv(callback: types.CallbackQuery):
    conv_id = callback.data.split("resume_conv:")[1]
    user_id = callback.from_user.id
    chat_id = callback.message.chat.id
    await callback.answer(f"Resuming session...")
    await activate_and_populate_session(chat_id, user_id, conv_id)

async def activate_and_populate_session(chat_id: int, user_id: int, conv_id: str):
    session = get_session(user_id)
    session["conversation_id"] = conv_id

    # 1. Clear previous bot messages in chat
    await clear_chat_messages(chat_id, user_id)

    # 2. Extract session dialogues from transcript
    dialogues = extract_session_messages(conv_id, max_dialogues=3)

    header_text = (
        f"🔄 <b>Session Resumed</b>: <code>{conv_id}</code>\n"
        f"───────────────"
    )
    await track_and_answer(chat_id, user_id, header_text, parse_mode="HTML")

    if dialogues:
        for d in dialogues:
            user_msg = f"💬 <b>User</b>:\n{html.escape(d['user'])}"
            await track_and_answer(chat_id, user_id, user_msg, parse_mode="HTML")

            model_html = md_to_telegram_html(d['model'])
            chunks = split_telegram_html(model_html, max_length=4000)
            for c in chunks:
                try:
                    await track_and_answer(chat_id, user_id, c, parse_mode="HTML")
                except Exception:
                    await track_and_answer(chat_id, user_id, d['model'][:4000], parse_mode=None)

    footer_text = "✨ <b>Session history restored!</b> You can now continue the conversation."
    await track_and_answer(chat_id, user_id, footer_text, parse_mode="HTML", reply_markup=get_main_menu_keyboard())

@dp.message(Command("new"))
@dp.callback_query(F.data == "menu_new_session")
async def cmd_new(event: types.Message | types.CallbackQuery):
    user_id = event.from_user.id
    chat_id = event.chat.id if isinstance(event, types.Message) else event.message.chat.id
    session = get_session(user_id)
    old_conv = session["conversation_id"]
    session["conversation_id"] = None

    logger.info(f"✨ [SESSION RESET] User {user_id} reset conversation (was: {old_conv})")

    # 1. Clear chat messages
    await clear_chat_messages(chat_id, user_id)

    text = "✨ <b>New session started!</b> Chat cleared and conversation context reset."

    if isinstance(event, types.CallbackQuery):
        await event.answer("New session started!")
        await track_and_answer(chat_id, user_id, text, parse_mode="HTML", reply_markup=get_main_menu_keyboard())
    else:
        await track_and_answer(chat_id, user_id, text, parse_mode="HTML", reply_markup=get_main_menu_keyboard())

@dp.message(Command("model"))
@dp.message(Command("models"))
async def cmd_model(message: types.Message):
    user_id = message.from_user.id
    logger.info(f"📥 [COMMAND] User {user_id} requested model menu via /model")
    session = get_session(user_id)
    buttons = []
    for model_id, label in AVAILABLE_MODELS:
        prefix = "✅ " if session["model"] == model_id else ""
        buttons.append([InlineKeyboardButton(text=f"{prefix}{label}", callback_data=f"set_model:{model_id}")])
    buttons.append([InlineKeyboardButton(text="🔙 Back to Menu", callback_data="open_main_menu")])

    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await track_and_answer(message.chat.id, user_id, f"🤖 <b>Select AI Model</b> (Current: <code>{session['model']}</code>):", parse_mode="HTML", reply_markup=kb)

@dp.callback_query(F.data == "open_model_menu")
async def cb_open_model_menu(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    session = get_session(user_id)
    buttons = []
    for model_id, label in AVAILABLE_MODELS:
        prefix = "✅ " if session["model"] == model_id else ""
        buttons.append([InlineKeyboardButton(text=f"{prefix}{label}", callback_data=f"set_model:{model_id}")])
    buttons.append([InlineKeyboardButton(text="🔙 Back to Menu", callback_data="open_main_menu")])

    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await safe_edit_text(callback.message, f"🤖 <b>Select AI Model</b> (Current: <code>{session['model']}</code>):", reply_markup=kb, parse_mode="HTML")

@dp.callback_query(F.data.startswith("set_model:"))
async def cb_set_model(callback: types.CallbackQuery):
    model_id = callback.data.split("set_model:")[1]
    user_id = callback.from_user.id
    session = get_session(user_id)
    old_model = session["model"]
    session["model"] = model_id
    logger.info(f"⚙️ [MODEL CHANGE] User {user_id} switched model: {old_model} -> {model_id}")
    await callback.answer(f"Model set to {model_id}")
    await cb_open_model_menu(callback)

@dp.message(Command("effort"))
async def cmd_effort(message: types.Message):
    user_id = message.from_user.id
    session = get_session(user_id)
    buttons = []
    for eff in AVAILABLE_EFFORTS:
        prefix = "✅ " if session["effort"] == eff else ""
        buttons.append(InlineKeyboardButton(text=f"{prefix}{eff.capitalize()}", callback_data=f"set_effort:{eff}"))
    buttons_row = [buttons]
    buttons_row.append([InlineKeyboardButton(text="🔙 Back to Menu", callback_data="open_main_menu")])
    kb = InlineKeyboardMarkup(inline_keyboard=buttons_row)
    await track_and_answer(message.chat.id, user_id, f"⚙️ <b>Select Reasoning Effort</b> (Current: <code>{session['effort']}</code>):", parse_mode="HTML", reply_markup=kb)

@dp.callback_query(F.data == "open_effort_menu")
async def cb_open_effort_menu(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    session = get_session(user_id)
    buttons = []
    for eff in AVAILABLE_EFFORTS:
        prefix = "✅ " if session["effort"] == eff else ""
        buttons.append(InlineKeyboardButton(text=f"{prefix}{eff.capitalize()}", callback_data=f"set_effort:{eff}"))
    buttons_row = [buttons]
    buttons_row.append([InlineKeyboardButton(text="🔙 Back to Menu", callback_data="open_main_menu")])
    kb = InlineKeyboardMarkup(inline_keyboard=buttons_row)
    await safe_edit_text(callback.message, f"⚙️ <b>Select Reasoning Effort</b> (Current: <code>{session['effort']}</code>):", reply_markup=kb, parse_mode="HTML")

@dp.callback_query(F.data.startswith("set_effort:"))
async def cb_set_effort(callback: types.CallbackQuery):
    eff = callback.data.split("set_effort:")[1]
    user_id = callback.from_user.id
    session = get_session(user_id)
    old_eff = session["effort"]
    session["effort"] = eff
    logger.info(f"⚙️ [EFFORT CHANGE] User {user_id} switched effort: {old_eff} -> {eff}")
    await callback.answer(f"Effort set to {eff}")
    await cb_open_effort_menu(callback)

@dp.message(Command("mode"))
async def cmd_mode(message: types.Message):
    user_id = message.from_user.id
    session = get_session(user_id)
    buttons = []
    for mode_id, label in AVAILABLE_MODES:
        prefix = "✅ " if session["mode"] == mode_id else ""
        buttons.append([InlineKeyboardButton(text=f"{prefix}{label}", callback_data=f"set_mode:{mode_id}")])
    buttons.append([InlineKeyboardButton(text="🔙 Back to Menu", callback_data="open_main_menu")])
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await track_and_answer(message.chat.id, user_id, f"📋 <b>Select Execution Mode</b> (Current: <code>{session['mode']}</code>):", parse_mode="HTML", reply_markup=kb)

@dp.callback_query(F.data == "open_mode_menu")
async def cb_open_mode_menu(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    session = get_session(user_id)
    buttons = []
    for mode_id, label in AVAILABLE_MODES:
        prefix = "✅ " if session["mode"] == mode_id else ""
        buttons.append([InlineKeyboardButton(text=f"{prefix}{label}", callback_data=f"set_mode:{mode_id}")])
    buttons.append([InlineKeyboardButton(text="🔙 Back to Menu", callback_data="open_main_menu")])
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await safe_edit_text(callback.message, f"📋 <b>Select Execution Mode</b> (Current: <code>{session['mode']}</code>):", reply_markup=kb, parse_mode="HTML")

@dp.callback_query(F.data.startswith("set_mode:"))
async def cb_set_mode(callback: types.CallbackQuery):
    mode_id = callback.data.split("set_mode:")[1]
    user_id = callback.from_user.id
    session = get_session(user_id)
    old_mode = session["mode"]
    session["mode"] = mode_id
    logger.info(f"⚙️ [MODE CHANGE] User {user_id} switched mode: {old_mode} -> {mode_id}")
    await callback.answer(f"Mode set to {mode_id}")
    await cb_open_mode_menu(callback)

@dp.callback_query(F.data == "open_main_menu")
async def cb_open_main_menu(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    session = get_session(user_id)
    text = (
        "👋 <b>Antigravity AI Control Panel</b>\n\n"
        f"🤖 <b>Model</b>: <code>{session['model']}</code>\n"
        f"⚙️ <b>Effort</b>: <code>{session['effort']}</code>\n"
        f"📋 <b>Mode</b>: <code>{session['mode']}</code>\n"
        f"📁 <b>Workspace</b>: <code>{session['workspace']}</code>\n"
        f"🆔 <b>Active Session</b>: <code>{session['conversation_id'] or 'New Context'}</code>\n\n"
        "Quick commands: /resume (select past session), /new (clear chat & new session)"
    )
    await safe_edit_text(callback.message, text, reply_markup=get_main_menu_keyboard(), parse_mode="HTML")

@dp.message(Command("cd"))
@dp.message(Command("workspace"))
async def cmd_cd(message: types.Message, command: CommandObject):
    user_id = message.from_user.id
    session = get_session(user_id)

    if not command.args:
        await track_and_answer(message.chat.id, user_id, f"📁 Current Workspace: <code>{session['workspace']}</code>\nUsage: <code>/cd /path/to/directory</code>", parse_mode="HTML")
        return

    new_path = os.path.abspath(command.args.strip())
    if not os.path.exists(new_path) or not os.path.isdir(new_path):
        await track_and_answer(message.chat.id, user_id, f"❌ Directory does not exist: <code>{new_path}</code>", parse_mode="HTML")
        return

    old_ws = session["workspace"]
    session["workspace"] = new_path
    logger.info(f"📁 [WORKSPACE CHANGE] User {user_id} changed workspace: {old_ws} -> {new_path}")
    await track_and_answer(message.chat.id, user_id, f"✅ <b>Workspace updated to</b>: <code>{new_path}</code>", parse_mode="HTML")

@dp.message(Command("status"))
@dp.callback_query(F.data == "menu_status")
async def cmd_status(event: types.Message | types.CallbackQuery):
    user_id = event.from_user.id
    session = get_session(user_id)
    logger.info(f"📊 [STATUS QUERY] User {user_id} checked status")
    text = (
        "📊 <b>Antigravity Status</b>\n\n"
        f"👤 <b>User ID</b>: <code>{user_id}</code>\n"
        f"🤖 <b>Model</b>: <code>{session['model']}</code>\n"
        f"⚙️ <b>Effort</b>: <code>{session['effort']}</code>\n"
        f"📋 <b>Mode</b>: <code>{session['mode']}</code>\n"
        f"📁 <b>Workspace</b>: <code>{session['workspace']}</code>\n"
        f"🆔 <b>Conversation ID</b>: <code>{session['conversation_id'] or 'None (new context)'}</code>\n"
        f"⚙️ <b>AGY Binary</b>: <code>{config.AGY_PATH}</code>\n"
    )

    if isinstance(event, types.CallbackQuery):
        await event.answer()
        await safe_edit_text(event.message, text, reply_markup=get_main_menu_keyboard(), parse_mode="HTML")
    else:
        await track_and_answer(event.chat.id, user_id, text, parse_mode="HTML")

@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    user_id = message.from_user.id
    logger.info(f"📥 [COMMAND] User {user_id} requested /help")
    text = (
        "💡 <b>Antigravity Telegram Bot Quick Reference</b>\n\n"
        "• <code>/resume</code> - Pick & restore previous session history into chat\n"
        "• <code>/new</code> - Clear chat & start a fresh conversation session\n"
        "• <code>/menu</code> - Control Panel & Settings\n"
        "• <code>/model</code> - Switch AI model\n"
        "• <code>/effort</code> - Set reasoning level (low/medium/high)\n"
        "• <code>/mode</code> - Switch mode (accept-edits / plan)\n"
        "• <code>/cd /path</code> - Change workspace directory\n"
        "• <code>/status</code> - View current settings\n"
        "• <b>Voice Messages</b> & <b>Files/Images</b>: Sent natively to the agent!"
    )
    await track_and_answer(message.chat.id, user_id, text, parse_mode="HTML")

@dp.callback_query(F.data == "stop_execution")
async def cb_stop(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    logger.info(f"🛑 [EXECUTION STOPPED] User {user_id} clicked Stop Execution")
    await runner.cancel_for_user(user_id)
    await callback.answer("Execution cancelled.")
    await safe_edit_text(callback.message, "🛑 <b>Task execution was cancelled by user.</b>", parse_mode="HTML")

@dp.message(F.voice)
async def handle_voice_message(message: types.Message):
    user_id = message.from_user.id
    session = get_session(user_id)

    downloads_dir = os.path.join(session["workspace"], "downloads")
    os.makedirs(downloads_dir, exist_ok=True)

    ogg_path = os.path.join(downloads_dir, f"voice_{message.message_id}.ogg")
    wav_path = os.path.join(downloads_dir, f"voice_{message.message_id}.wav")

    logger.info(f"🎙️ [VOICE MESSAGE] User {user_id} sent voice message (file_id={message.voice.file_id})")

    file_info = await bot.get_file(message.voice.file_id)
    await bot.download_file(file_info.file_path, ogg_path)

    try:
        subprocess.run(["ffmpeg", "-y", "-i", ogg_path, wav_path], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        audio_file = wav_path
    except Exception as e:
        logger.warning(f"ffmpeg conversion failed, using raw ogg: {e}")
        audio_file = ogg_path

    prompt = f"[User sent a voice message saved at: {audio_file}]. Please listen to or process this audio input and carry out the user's instructions."
    message.text = prompt
    await handle_text_prompt(message)

@dp.message(F.text & ~F.text.startswith("/"))
async def handle_text_prompt(message: types.Message):
    user_id = message.from_user.id
    session = get_session(user_id)
    prompt = message.text.strip()

    if not prompt:
        return

    logger.info(f"📥 [USER PROMPT] User {user_id} prompt ({len(prompt)} chars): '{prompt[:100]}...'")

    stop_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏹ Stop Execution", callback_data="stop_execution")]
    ])

    status_msg = await track_and_answer(message.chat.id, user_id, "⚡ <i>Antigravity initializing...</i>", parse_mode="HTML", reply_markup=stop_kb)

    full_text = ""
    last_update_time = 0.0

    prompt_with_hint = prompt + (
        "\n\n[Telegram System Note: Format your response beautifully for Telegram chat.\n"
        "- Use clean sections with emojis in bold headers (📌, 🔹, 🛠️, 💡, etc.).\n"
        "- Use short paragraphs and concise bullet points (•).\n"
        "- Use standard fenced code blocks for any code or bash commands (e.g. ```python ... ```).\n"
        "- Use blockquotes (`> text`) for quotes, notes, or highlights.\n"
        "- Avoid markdown tables (use code blocks or bullet lists for tabular data).\n"
        "- If you generate image files, output their full path on a separate line.]"
    )

    try:
        async for chunk in runner.run_prompt_stream(
            user_id=user_id,
            prompt=prompt_with_hint,
            conversation_id=session.get("conversation_id"),
            workspace_dir=session.get("workspace"),
            model=session.get("model"),
            effort=session.get("effort"),
            mode=session.get("mode")
        ):
            event_type = chunk.get("type")

            if event_type == "init":
                conv_id = chunk.get("conversation_id")
                if conv_id:
                    session["conversation_id"] = conv_id
                    logger.info(f"🆔 [SESSION CONV ID] Assigned conversation ID: {conv_id}")

            elif event_type == "update":
                step_type = chunk.get("step_type")
                delta = chunk.get("text_delta", "")

                if step_type in ("thinking", "agent_response") and delta:
                    full_text += delta

                now = asyncio.get_event_loop().time()
                if now - last_update_time >= 1.2 and full_text:
                    last_update_time = now
                    display_content = full_text
                    if len(display_content) > 3500:
                        display_content = "..." + display_content[-3500:]
                    formatted_stream = md_to_telegram_html(display_content)
                    try:
                        await safe_edit_text(status_msg, formatted_stream, reply_markup=stop_kb, parse_mode="HTML")
                    except Exception:
                        await safe_edit_text(status_msg, display_content, reply_markup=stop_kb, parse_mode=None)

            elif event_type == "result":
                final_res = chunk.get("response", "").strip()
                conv_id = chunk.get("conversation_id")
                if conv_id:
                    session["conversation_id"] = conv_id

                if final_res:
                    full_text = final_res

            elif event_type == "error":
                err_text = chunk.get("error", "Unknown error")
                logger.error(f"❌ [AGY EXECUTION ERROR] {err_text}")
                full_text += f"\n\n❌ <b>Error</b>: {html.escape(err_text)}"

        logger.info(f"📤 [AGENT RESPONSE FINISHED] User {user_id} | Response length: {len(full_text)} chars")

        if full_text:
            formatted_html = md_to_telegram_html(full_text)
            chunks = split_telegram_html(formatted_html, max_length=4000)

            try:
                await safe_edit_text(status_msg, chunks[0], parse_mode="HTML")
            except Exception as format_err:
                logger.warning(f"Failed to send HTML formatted response, falling back to plain text: {format_err}")
                plain_chunks = [full_text[i:i+4000] for i in range(0, len(full_text), 4000)]
                await safe_edit_text(status_msg, plain_chunks[0], parse_mode=None)

            for c in chunks[1:]:
                try:
                    await track_and_answer(message.chat.id, user_id, c, parse_mode="HTML")
                except Exception as chunk_err:
                    logger.warning(f"Failed to send chunk HTML: {chunk_err}")
                    await track_and_answer(message.chat.id, user_id, c, parse_mode=None)

            # Auto-detect and send image files mentioned in response
            found_paths = set(re.findall(r'(/[\w\.\-/]+\.(?:png|jpg|jpeg|webp|gif))', full_text, re.IGNORECASE))
            for img_path in found_paths:
                if os.path.exists(img_path) and os.path.isfile(img_path):
                    try:
                        logger.info(f"📷 [AUTO-SEND IMAGE] Sending image found in response: {img_path}")
                        img_msg = await bot.send_photo(message.chat.id, FSInputFile(img_path), caption=f"📷 {os.path.basename(img_path)}")
                        session["sent_message_ids"].append(img_msg.message_id)
                    except Exception as img_err:
                        logger.error(f"Failed to auto-send image {img_path}: {img_err}")
        else:
            await safe_edit_text(status_msg, "✅ <i>Done (no text output)</i>", parse_mode="HTML")

    except Exception as e:
        logger.error(f"❌ [UNHANDLED ERROR] Exception during prompt handling: {e}", exc_info=True)
        await safe_edit_text(status_msg, f"❌ <i>An unexpected error occurred</i>: <code>{html.escape(str(e))}</code>", parse_mode="HTML")

@dp.message(F.document | F.photo)
async def handle_file_message(message: types.Message):
    user_id = message.from_user.id
    session = get_session(user_id)

    downloads_dir = os.path.join(session["workspace"], "downloads")
    os.makedirs(downloads_dir, exist_ok=True)

    file_name = "received_file"
    file_id = None

    if message.document:
        file_id = message.document.file_id
        file_name = message.document.file_name or "document"
    elif message.photo:
        file_id = message.photo[-1].file_id
        file_name = "photo.jpg"

    logger.info(f"📁 [FILE RECEIVED] User {user_id} sent file: {file_name}")

    file_info = await bot.get_file(file_id)
    save_path = os.path.join(downloads_dir, file_name)
    await bot.download_file(file_info.file_path, save_path)

    caption = message.caption or f"I sent you a file saved at `{save_path}`. Please inspect and process it."
    prompt = f"[User attached file: {save_path}]\nCaption/Task: {caption}"

    message.text = prompt
    await handle_text_prompt(message)

async def main():
    logger.info("🚀 Starting Antigravity Telegram Bot Gateway (v0.1)...")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
