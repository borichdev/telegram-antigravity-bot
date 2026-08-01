import os
import asyncio
import logging
import html
import subprocess
from typing import Dict, Optional, Any

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, CommandObject
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.exceptions import TelegramBadRequest

import config
from antigravity_bridge import AntigravityRunner

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

user_sessions: Dict[int, Dict[str, Any]] = {}

def get_session(user_id: int) -> Dict[str, Any]:
    if user_id not in user_sessions:
        user_sessions[user_id] = {
            "conversation_id": None,
            "workspace": config.WORKSPACE_DIR,
            "model": config.DEFAULT_MODEL or "gemini-3.6-flash-high",
            "effort": "high",
            "mode": "accept-edits"
        }
    return user_sessions[user_id]

def is_allowed(user_id: int) -> bool:
    if not config.ALLOWED_USER_IDS:
        return True
    return user_id in config.ALLOWED_USER_IDS

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
            InlineKeyboardButton(text="🔄 New Session", callback_data="menu_new_session"),
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
        "👋 **Antigravity AI Control Panel**\n\n"
        f"🤖 **Model**: `{session['model']}`\n"
        f"⚙️ **Effort**: `{session['effort']}`\n"
        f"📋 **Mode**: `{session['mode']}`\n"
        f"📁 **Workspace**: `{session['workspace']}`\n"
        f"🆔 **Active Session**: `{session['conversation_id'] or 'New Session'}`\n\n"
        "Use the buttons below or quick commands to configure:"
    )
    await message.answer(text, parse_mode="Markdown", reply_markup=get_main_menu_keyboard())

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
    await message.answer(f"🤖 **Select AI Model** (Current: `{session['model']}`):", parse_mode="Markdown", reply_markup=kb)

@dp.callback_query(F.data == "open_model_menu")
async def cb_open_model_menu(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    logger.info(f"🖱️ [CALLBACK] User {user_id} opened model menu")
    session = get_session(user_id)
    buttons = []
    for model_id, label in AVAILABLE_MODELS:
        prefix = "✅ " if session["model"] == model_id else ""
        buttons.append([InlineKeyboardButton(text=f"{prefix}{label}", callback_data=f"set_model:{model_id}")])
    buttons.append([InlineKeyboardButton(text="🔙 Back to Menu", callback_data="open_main_menu")])

    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await safe_edit_text(callback.message, f"🤖 **Select AI Model** (Current: `{session['model']}`):", reply_markup=kb, parse_mode="Markdown")

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
    logger.info(f"📥 [COMMAND] User {user_id} requested effort menu")
    session = get_session(user_id)
    buttons = []
    for eff in AVAILABLE_EFFORTS:
        prefix = "✅ " if session["effort"] == eff else ""
        buttons.append(InlineKeyboardButton(text=f"{prefix}{eff.capitalize()}", callback_data=f"set_effort:{eff}"))
    buttons_row = [buttons]
    buttons_row.append([InlineKeyboardButton(text="🔙 Back to Menu", callback_data="open_main_menu")])
    kb = InlineKeyboardMarkup(inline_keyboard=buttons_row)
    await message.answer(f"⚙️ **Select Reasoning Effort** (Current: `{session['effort']}`):", parse_mode="Markdown", reply_markup=kb)

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
    await safe_edit_text(callback.message, f"⚙️ **Select Reasoning Effort** (Current: `{session['effort']}`):", reply_markup=kb, parse_mode="Markdown")

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
    logger.info(f"📥 [COMMAND] User {user_id} requested mode menu")
    session = get_session(user_id)
    buttons = []
    for mode_id, label in AVAILABLE_MODES:
        prefix = "✅ " if session["mode"] == mode_id else ""
        buttons.append([InlineKeyboardButton(text=f"{prefix}{label}", callback_data=f"set_mode:{mode_id}")])
    buttons.append([InlineKeyboardButton(text="🔙 Back to Menu", callback_data="open_main_menu")])
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await message.answer(f"📋 **Select Execution Mode** (Current: `{session['mode']}`):", parse_mode="Markdown", reply_markup=kb)

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
    await safe_edit_text(callback.message, f"📋 **Select Execution Mode** (Current: `{session['mode']}`):", reply_markup=kb, parse_mode="Markdown")

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
        "👋 **Antigravity AI Control Panel**\n\n"
        f"🤖 **Model**: `{session['model']}`\n"
        f"⚙️ **Effort**: `{session['effort']}`\n"
        f"📋 **Mode**: `{session['mode']}`\n"
        f"📁 **Workspace**: `{session['workspace']}`\n"
        f"🆔 **Active Session**: `{session['conversation_id'] or 'New Session'}`\n\n"
        "Use the buttons below or quick commands to configure:"
    )
    await safe_edit_text(callback.message, text, reply_markup=get_main_menu_keyboard(), parse_mode="Markdown")

@dp.message(Command("cd"))
@dp.message(Command("workspace"))
async def cmd_cd(message: types.Message, command: CommandObject):
    user_id = message.from_user.id
    session = get_session(user_id)
    if not command.args:
        await message.answer(f"📁 Current Workspace: `{session['workspace']}`\nUsage: `/cd /path/to/directory`", parse_mode="Markdown")
        return

    new_path = os.path.abspath(command.args.strip())
    if not os.path.exists(new_path) or not os.path.isdir(new_path):
        logger.warning(f"📁 [WORKSPACE CHANGE FAILED] Directory does not exist: {new_path}")
        await message.answer(f"❌ Directory does not exist: `{new_path}`", parse_mode="Markdown")
        return

    old_ws = session["workspace"]
    session["workspace"] = new_path
    logger.info(f"📁 [WORKSPACE CHANGE] User {user_id} changed workspace: {old_ws} -> {new_path}")
    await message.answer(f"✅ **Workspace updated to**: `{new_path}`", parse_mode="Markdown")

@dp.message(Command("new"))
@dp.callback_query(F.data == "menu_new_session")
async def cmd_new(event: types.Message | types.CallbackQuery):
    user_id = event.from_user.id
    session = get_session(user_id)
    old_conv = session["conversation_id"]
    session["conversation_id"] = None
    logger.info(f"🔄 [SESSION RESET] User {user_id} reset conversation session (was: {old_conv})")

    if isinstance(event, types.CallbackQuery):
        await event.answer("Session reset!")
        await safe_edit_text(event.message, "🔄 **Session reset!** Starting a new conversation context.", parse_mode="Markdown")
    else:
        await event.answer("🔄 **Session reset!** Starting a new conversation context.", parse_mode="Markdown")

@dp.message(Command("status"))
@dp.callback_query(F.data == "menu_status")
async def cmd_status(event: types.Message | types.CallbackQuery):
    user_id = event.from_user.id
    session = get_session(user_id)
    logger.info(f"📊 [STATUS QUERY] User {user_id} checked status")
    text = (
        "📊 **Antigravity Status**\n\n"
        f"👤 **User ID**: `{user_id}`\n"
        f"🤖 **Model**: `{session['model']}`\n"
        f"⚙️ **Effort**: `{session['effort']}`\n"
        f"📋 **Mode**: `{session['mode']}`\n"
        f"📁 **Workspace**: `{session['workspace']}`\n"
        f"🆔 **Conversation ID**: `{session['conversation_id'] or 'None (new context)'}`\n"
        f"⚙️ **AGY Binary**: `{config.AGY_PATH}`\n"
    )
    if isinstance(event, types.CallbackQuery):
        await event.answer()
        await event.message.answer(text, parse_mode="Markdown")
    else:
        await event.answer(text, parse_mode="Markdown")

@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    user_id = message.from_user.id
    logger.info(f"📥 [COMMAND] User {user_id} requested /help")
    text = (
        "💡 **Antigravity Telegram Bot Quick Reference**\n\n"
        "• `/menu` - Control Panel & Settings\n"
        "• `/model` - Switch AI model\n"
        "• `/effort` - Set reasoning level (low/medium/high)\n"
        "• `/mode` - Switch mode (accept-edits / plan)\n"
        "• `/cd /path` - Change workspace directory\n"
        "• `/new` - Reset conversation history\n"
        "• `/status` - View current settings\n"
        "• **Voice Messages** & **Files/Images**: Sent natively to the agent!"
    )
    await message.answer(text, parse_mode="Markdown")

@dp.callback_query(F.data == "stop_execution")
async def cb_stop(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    logger.info(f"🛑 [EXECUTION STOPPED] User {user_id} clicked Stop Execution")
    await runner.cancel_for_user(user_id)
    await callback.answer("Execution cancelled.")
    await safe_edit_text(callback.message, "🛑 *Task execution was cancelled by user.*", parse_mode="Markdown")

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
    
    status_msg = await message.answer("⚡ *Antigravity initializing...*", parse_mode="Markdown", reply_markup=stop_kb)

    full_text = ""
    last_update_time = 0.0

    try:
        async for chunk in runner.run_prompt_stream(
            user_id=user_id,
            prompt=prompt,
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
                    if len(display_content) > 3800:
                        display_content = "..." + display_content[-3800:]
                    await safe_edit_text(status_msg, display_content, reply_markup=stop_kb)

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
                full_text += f"\n\n❌ **Error**: {err_text}"

        logger.info(f"📤 [AGENT RESPONSE FINISHED] User {user_id} | Response length: {len(full_text)} chars")

        if full_text:
            if len(full_text) <= 4000:
                await safe_edit_text(status_msg, full_text, parse_mode="Markdown")
            else:
                chunks = [full_text[i:i+4000] for i in range(0, len(full_text), 4000)]
                await safe_edit_text(status_msg, chunks[0])
                for c in chunks[1:]:
                    await message.answer(c)
        else:
            await safe_edit_text(status_msg, "✅ *Done (no text output)*", parse_mode="Markdown")

    except Exception as e:
        logger.error(f"❌ [UNHANDLED ERROR] Exception during prompt handling: {e}", exc_info=True)
        await safe_edit_text(status_msg, f"❌ *An unexpected error occurred*: `{html.escape(str(e))}`", parse_mode="Markdown")

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
    logger.info("🚀 Starting Antigravity Telegram Bot Gateway with Full Audit Logging...")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
