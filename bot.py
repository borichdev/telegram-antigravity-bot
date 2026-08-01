import os
import asyncio
import logging
import html
from typing import Dict, Optional

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.exceptions import TelegramBadRequest

import config
from antigravity_bridge import AntigravityRunner

# Configure logging
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

# Storage for user conversation IDs and state
# { user_id: { "conversation_id": str, "workspace": str } }
user_sessions: Dict[int, Dict[str, Any]] = {}

def get_session(user_id: int) -> Dict[str, Any]:
    if user_id not in user_sessions:
        user_sessions[user_id] = {
            "conversation_id": None,
            "workspace": config.WORKSPACE_DIR
        }
    return user_sessions[user_id]

# Security middleware / check
def is_allowed(user_id: int) -> bool:
    if not config.ALLOWED_USER_IDS:
        return True
    return user_id in config.ALLOWED_USER_IDS

@dp.message(F.from_user.id.func(lambda uid: not is_allowed(uid)))
async def handle_unauthorized(message: types.Message):
    logger.warning(f"Unauthorized access attempt by user_id={message.from_user.id} ({message.from_user.username})")
    await message.answer("⛔ Access Denied: You are not authorized to use this bot.")

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    session = get_session(message.from_user.id)
    text = (
        "👋 **Welcome to Antigravity Telegram Gateway!**\n\n"
        "I am connected to your local Antigravity AI Agent with full permissions.\n\n"
        f"📁 **Workspace**: `{session['workspace']}`\n"
        f"💬 **Active Session**: `{session['conversation_id'] or 'New Session'}`\n\n"
        "**Available Commands:**\n"
        "/new - Start a fresh conversation context\n"
        "/status - Check current session status & workspace\n"
        "/help - Help & usage instructions\n\n"
        "Just send me a message, code snippet, or command to begin!"
    )
    await message.answer(text, parse_mode="Markdown")

@dp.message(Command("new"))
async def cmd_new(message: types.Message):
    session = get_session(message.from_user.id)
    session["conversation_id"] = None
    await message.answer("🔄 **Session reset!** Starting a new conversation context.", parse_mode="Markdown")

@dp.message(Command("status"))
async def cmd_status(message: types.Message):
    session = get_session(message.from_user.id)
    text = (
        "📊 **Antigravity Status**\n\n"
        f"👤 **User ID**: `{message.from_user.id}`\n"
        f"🆔 **Conversation ID**: `{session['conversation_id'] or 'None (will auto-create on next prompt)'}`\n"
        f"📁 **Workspace**: `{session['workspace']}`\n"
        f"⚙️ **AGY Executable**: `{config.AGY_PATH}`\n"
    )
    await message.answer(text, parse_mode="Markdown")

@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    text = (
        "💡 **Antigravity Telegram Bot Help**\n\n"
        "• Send any text prompt to run code, terminal commands, or research.\n"
        "• Send files/photos/documents to analyze them.\n"
        "• Use `/new` to reset context if you want to start a new task.\n"
        "• Use the ⏹ **Stop** button under the progress message to cancel an execution."
    )
    await message.answer(text, parse_mode="Markdown")

@dp.callback_query(F.data == "stop_execution")
async def cb_stop(callback: types.CallbackQuery):
    await runner.cancel_for_user(callback.from_user.id)
    await callback.answer("Execution cancelled.")
    await callback.message.edit_text("🛑 *Task execution was cancelled by user.*", parse_mode="Markdown")

@dp.message(F.text)
async def handle_text_prompt(message: types.Message):
    user_id = message.from_user.id
    session = get_session(user_id)
    prompt = message.text.strip()

    if not prompt:
        return

    # Create status message
    stop_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏹ Stop Execution", callback_data="stop_execution")]
    ])
    
    status_msg = await message.answer("⚡ *Antigravity initializing...*", parse_mode="Markdown", reply_markup=stop_kb)

    full_text = ""
    status_header = "🧠 *Thinking...*\n\n"
    last_update_time = 0.0
    conversation_id = session.get("conversation_id")

    try:
        async for chunk in runner.run_prompt_stream(
            user_id=user_id,
            prompt=prompt,
            conversation_id=conversation_id,
            workspace_dir=session.get("workspace")
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

                elif step_type == "tool_call":
                    status_header = "🛠️ *Executing tools...*\n\n"

                # Rate limit message edits (max once per 1.2s)
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
                        pass  # Content hasn't changed or message uneditable

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

        # Final message update
        if full_text:
            # Telegram text limit is 4096 chars per message
            if len(full_text) <= 4000:
                try:
                    await status_msg.edit_text(full_text, parse_mode="Markdown")
                except Exception:
                    await status_msg.edit_text(full_text)
            else:
                # Split long text into chunks
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
    logger.info("Starting Antigravity Telegram Bot...")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
