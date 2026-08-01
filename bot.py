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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("AntigravityBot")

if not config.BOT_TOKEN:
    raise ValueError("BOT_TOKEN is not set in environment or .env file!")

bot = Bot(token=config.BOT_TOKEN)
dp = Dispatcher()
runner = AntigravityRunner()

# Available Models list
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

# Storage for user sessions
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
    logger.warning(f"Unauthorized access attempt by user_id={message.from_user.id} ({message.from_user.username})")
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

@dp.message(Command("start"))
@dp.message(Command("menu"))
async def cmd_menu(message: types.Message):
    session = get_session(message.from_user.id)
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

@dp.message(Command("model"), Command("models"))
async def cmd_model(message: types.Message):
    session = get_session(message.from_user.id)
    buttons = []
    for model_id, label in AVAILABLE_MODELS:
        prefix = "✅ " if session["model"] == model_id else ""
        buttons.append([InlineKeyboardButton(text=f"{prefix}{label}", callback_data=f"set_model:{model_id}")])
    buttons.append([InlineKeyboardButton(text="🔙 Back to Menu", callback_data="open_main_menu")])

    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await message.answer(f"🤖 **Select AI Model** (Current: `{session['model']}`):", parse_mode="Markdown", reply_markup=kb)

@dp.callback_query(F.data == "open_model_menu")
async def cb_open_model_menu(callback: types.CallbackQuery):
    session = get_session(callback.from_user.id)
    buttons = []
    for model_id, label in AVAILABLE_MODELS:
        prefix = "✅ " if session["model"] == model_id else ""
        buttons.append([InlineKeyboardButton(text=f"{prefix}{label}", callback_data=f"set_model:{model_id}")])
    buttons.append([InlineKeyboardButton(text="🔙 Back to Menu", callback_data="open_main_menu")])

    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await callback.message.edit_text(f"🤖 **Select AI Model** (Current: `{session['model']}`):", parse_mode="Markdown", reply_markup=kb)

@dp.callback_query(F.data.startswith("set_model:"))
async def cb_set_model(callback: types.CallbackQuery):
    model_id = callback.data.split("set_model:")[1]
    session = get_session(callback.from_user.id)
    session["model"] = model_id
    await callback.answer(f"Model set to {model_id}")
    await cb_open_model_menu(callback)

@dp.message(Command("effort"))
async def cmd_effort(message: types.Message):
    session = get_session(message.from_user.id)
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
    session = get_session(callback.from_user.id)
    buttons = []
    for eff in AVAILABLE_EFFORTS:
        prefix = "✅ " if session["effort"] == eff else ""
        buttons.append(InlineKeyboardButton(text=f"{prefix}{eff.capitalize()}", callback_data=f"set_effort:{eff}"))
    buttons_row = [buttons]
    buttons_row.append([InlineKeyboardButton(text="🔙 Back to Menu", callback_data="open_main_menu")])
    kb = InlineKeyboardMarkup(inline_keyboard=buttons_row)
    await callback.message.edit_text(f"⚙️ **Select Reasoning Effort** (Current: `{session['effort']}`):", parse_mode="Markdown", reply_markup=kb)

@dp.callback_query(F.data.startswith("set_effort:"))
async def cb_set_effort(callback: types.CallbackQuery):
    eff = callback.data.split("set_effort:")[1]
    session = get_session(callback.from_user.id)
    session["effort"] = eff
    await callback.answer(f"Effort set to {eff}")
    await cb_open_effort_menu(callback)

@dp.message(Command("mode"))
async def cmd_mode(message: types.Message):
    session = get_session(message.from_user.id)
    buttons = []
    for mode_id, label in AVAILABLE_MODES:
        prefix = "✅ " if session["mode"] == mode_id else ""
        buttons.append([InlineKeyboardButton(text=f"{prefix}{label}", callback_data=f"set_mode:{mode_id}")])
    buttons.append([InlineKeyboardButton(text="🔙 Back to Menu", callback_data="open_main_menu")])
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await message.answer(f"📋 **Select Execution Mode** (Current: `{session['mode']}`):", parse_mode="Markdown", reply_markup=kb)

@dp.callback_query(F.data == "open_mode_menu")
async def cb_open_mode_menu(callback: types.CallbackQuery):
    session = get_session(callback.from_user.id)
    buttons = []
    for mode_id, label in AVAILABLE_MODES:
        prefix = "✅ " if session["mode"] == mode_id else ""
        buttons.append([InlineKeyboardButton(text=f"{prefix}{label}", callback_data=f"set_mode:{mode_id}")])
    buttons.append([InlineKeyboardButton(text="🔙 Back to Menu", callback_data="open_main_menu")])
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await callback.message.edit_text(f"📋 **Select Execution Mode** (Current: `{session['mode']}`):", parse_mode="Markdown", reply_markup=kb)

@dp.callback_query(F.data.startswith("set_mode:"))
async def cb_set_mode(callback: types.CallbackQuery):
    mode_id = callback.data.split("set_mode:")[1]
    session = get_session(callback.from_user.id)
    session["mode"] = mode_id
    await callback.answer(f"Mode set to {mode_id}")
    await cb_open_mode_menu(callback)

@dp.callback_query(F.data == "open_main_menu")
async def cb_open_main_menu(callback: types.CallbackQuery):
    session = get_session(callback.from_user.id)
    text = (
        "👋 **Antigravity AI Control Panel**\n\n"
        f"🤖 **Model**: `{session['model']}`\n"
        f"⚙️ **Effort**: `{session['effort']}`\n"
        f"📋 **Mode**: `{session['mode']}`\n"
        f"📁 **Workspace**: `{session['workspace']}`\n"
        f"🆔 **Active Session**: `{session['conversation_id'] or 'New Session'}`\n\n"
        "Use the buttons below or quick commands to configure:"
    )
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=get_main_menu_keyboard())

@dp.message(Command("cd"), Command("workspace"))
async def cmd_cd(message: types.Message, command: CommandObject):
    session = get_session(message.from_user.id)
    if not command.args:
        await message.answer(f"📁 Current Workspace: `{session['workspace']}`\nUsage: `/cd /path/to/directory`", parse_mode="Markdown")
        return

    new_path = os.path.abspath(command.args.strip())
    if not os.path.exists(new_path) or not os.path.isdir(new_path):
        await message.answer(f"❌ Directory does not exist: `{new_path}`", parse_mode="Markdown")
        return

    session["workspace"] = new_path
    await message.answer(f"✅ **Workspace updated to**: `{new_path}`", parse_mode="Markdown")

@dp.message(Command("new"))
@dp.callback_query(F.data == "menu_new_session")
async def cmd_new(event: types.Message | types.CallbackQuery):
    user_id = event.from_user.id
    session = get_session(user_id)
    session["conversation_id"] = None

    if isinstance(event, types.CallbackQuery):
        await event.answer("Session reset!")
        await event.message.answer("🔄 **Session reset!** Starting a new conversation context.", parse_mode="Markdown")
    else:
        await event.answer("🔄 **Session reset!** Starting a new conversation context.", parse_mode="Markdown")

@dp.message(Command("status"))
@dp.callback_query(F.data == "menu_status")
async def cmd_status(event: types.Message | types.CallbackQuery):
    user_id = event.from_user.id
    session = get_session(user_id)
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
    text = (
        "💡 **Antigravity Telegram Bot Quick Reference**\n\n"
        "• `/menu` - Control Panel & Settings\n"
        "• `/model` - Switch AI model (Gemini 3.6, Claude, GPT-OSS)\n"
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
    await runner.cancel_for_user(callback.from_user.id)
    await callback.answer("Execution cancelled.")
    await callback.message.edit_text("🛑 *Task execution was cancelled by user.*", parse_mode="Markdown")

@dp.message(F.voice)
async def handle_voice_message(message: types.Message):
    user_id = message.from_user.id
    session = get_session(user_id)

    downloads_dir = os.path.join(session["workspace"], "downloads")
    os.makedirs(downloads_dir, exist_ok=True)

    ogg_path = os.path.join(downloads_dir, f"voice_{message.message_id}.ogg")
    wav_path = os.path.join(downloads_dir, f"voice_{message.message_id}.wav")

    file_info = await bot.get_file(message.voice.file_id)
    await bot.download_file(file_info.file_path, ogg_path)

    # Convert ogg to wav using ffmpeg
    try:
        subprocess.run(["ffmpeg", "-y", "-i", ogg_path, wav_path], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        audio_file = wav_path
    except Exception:
        audio_file = ogg_path

    prompt = f"[User sent a voice message saved at: {audio_file}]. Please listen to or process this audio input and carry out the user's instructions."
    message.text = prompt
    await handle_text_prompt(message)

@dp.message(F.text)
async def handle_text_prompt(message: types.Message):
    user_id = message.from_user.id
    session = get_session(user_id)
    prompt = message.text.strip()

    if not prompt:
        return

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
                    try:
                        await status_msg.edit_text(
                            display_content,
                            reply_markup=stop_kb
                        )
                    except TelegramBadRequest:
                        pass

            elif event_type == "result":
                final_res = chunk.get("response", "").strip()
                conv_id = chunk.get("conversation_id")
                if conv_id:
                    session["conversation_id"] = conv_id

                if final_res:
                    full_text = final_res

            elif event_type == "error":
                err_text = chunk.get("error", "Unknown error")
                full_text += f"\n\n❌ **Error**: {err_text}"

        if full_text:
            if len(full_text) <= 4000:
                try:
                    await status_msg.edit_text(full_text, parse_mode="Markdown")
                except Exception:
                    await status_msg.edit_text(full_text)
            else:
                chunks = [full_text[i:i+4000] for i in range(0, len(full_text), 4000)]
                await status_msg.edit_text(chunks[0])
                for c in chunks[1:]:
                    await message.answer(c)
        else:
            await status_msg.edit_text("✅ *Done (no text output)*", parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Error handling user prompt: {e}", exc_info=True)
        await status_msg.edit_text(f"❌ *An unexpected error occurred*: `{html.escape(str(e))}`", parse_mode="Markdown")

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

    file_info = await bot.get_file(file_id)
    save_path = os.path.join(downloads_dir, file_name)
    await bot.download_file(file_info.file_path, save_path)

    caption = message.caption or f"I sent you a file saved at `{save_path}`. Please inspect and process it."
    prompt = f"[User attached file: {save_path}]\nCaption/Task: {caption}"

    message.text = prompt
    await handle_text_prompt(message)

async def main():
    logger.info("Starting Enhanced Antigravity Telegram Bot...")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
