"""
Telegram Agent powered by Claude (Anthropic API)
--------------------------------------------------
A minimal but production-ready "agent" bot: every Telegram message is sent to
Claude along with a set of tools. Claude decides whether to answer directly
or call a tool (calculator, weather, web search, HR tools, PDF/Excel
create+read, email, reminders). The bot executes the tool, feeds the result
back to Claude, and keeps looping until Claude produces a final text answer.
Generated files (PDF/Excel) are sent back to the user as Telegram documents;
uploaded documents are downloaded and handed to Claude as a file path.

Deploy this on Railway / Render / any VPS. See README.md for instructions.
"""

import logging
import os

from anthropic import Anthropic
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from tools import TOOLS, execute_tool

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ---- Configuration -----------------------------------------------------
TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-5")
SYSTEM_PROMPT = (
    "You are a helpful assistant reachable over Telegram, also acting as an "
    "HR assistant for the team: answering policy questions, recording leave "
    "requests, and keeping simple employee records. You can also search the "
    "web, send emails, create/read PDF and Excel files, and set reminders. "
    "When a user uploads a file, its local path and original name are given "
    "to you in the conversation — use read_pdf or read_excel with that path. "
    "Be concise (Telegram messages should be short and readable on mobile). "
    "Reply in the same language the user writes in."
)
MAX_HISTORY_MESSAGES = 20  # keep the last N messages per chat to bound cost

client = Anthropic(api_key=ANTHROPIC_API_KEY)

# In-memory conversation store: {chat_id: [ {role, content}, ... ]}
# NOTE: this resets whenever the process restarts. For persistence across
# restarts, swap this dict for a small database (see README).
conversations: dict[int, list] = {}

# Set once in main(); tools (like set_reminder) use it to schedule jobs.
BOT_APP: Application | None = None

UPLOADS_DIR = "/tmp/bot_files/uploads"
os.makedirs(UPLOADS_DIR, exist_ok=True)


def run_agent_loop(chat_id: int, user_text: str):
    """Send the user's message to Claude, execute any tool calls, and
    return (final_text, attachments) where attachments is a list of local
    file paths to send back to the user on Telegram."""
    history = conversations.setdefault(chat_id, [])
    history.append({"role": "user", "content": user_text})

    if len(history) > MAX_HISTORY_MESSAGES:
        del history[: len(history) - MAX_HISTORY_MESSAGES]

    attachments: list[str] = []

    while True:
        response = client.messages.create(
            model=MODEL,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=history,
        )

        history.append({"role": "assistant", "content": response.content})

        if response.stop_reason != "tool_use":
            text = "".join(
                block.text for block in response.content if block.type == "text"
            ) or "..."
            return text, attachments

        tool_results = []
        for block in response.content:
            if block.type == "tool_use":
                result = execute_tool(block.name, block.input, chat_id=chat_id, app=BOT_APP)
                if isinstance(result, dict):
                    if result.get("attachment"):
                        attachments.append(result["attachment"])
                    content = result.get("text", "Done.")
                else:
                    content = str(result)
                tool_results.append(
                    {"type": "tool_result", "tool_use_id": block.id, "content": content}
                )

        history.append({"role": "user", "content": tool_results})
        # loop again so Claude can use the tool result


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "أهلاً! أنا مساعدك الذكي المبني على Claude. أرسل لي أي رسالة، أو ملف PDF/Excel، وسأساعدك.\n"
        "أرسل /reset لمسح ذاكرة المحادثة."
    )


async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    conversations.pop(update.effective_chat.id, None)
    await update.message.reply_text("تم مسح ذاكرة المحادثة. ابدأ من جديد!")


async def _reply_with_attachments(update: Update, text: str, attachments: list[str]) -> None:
    await update.message.reply_text(text)
    for path in attachments:
        try:
            with open(path, "rb") as f:
                await update.message.reply_document(document=f)
        except Exception:
            logger.exception("Failed to send attachment %s", path)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    user_text = update.message.text

    await context.bot.send_chat_action(chat_id=chat_id, action="typing")

    try:
        reply, attachments = run_agent_loop(chat_id, user_text)
    except Exception:
        logger.exception("Agent loop failed")
        reply, attachments = "عذرًا، حدث خطأ أثناء معالجة طلبك. حاول مرة أخرى.", []

    await _reply_with_attachments(update, reply, attachments)


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    doc = update.message.document

    await context.bot.send_chat_action(chat_id=chat_id, action="typing")

    file = await doc.get_file()
    local_path = os.path.join(UPLOADS_DIR, f"{chat_id}_{doc.file_name}")
    await file.download_to_drive(local_path)

    synthetic_message = (
        f"[The user uploaded a file. Original name: '{doc.file_name}'. "
        f"Local path you can use with read_pdf or read_excel: {local_path}]\n"
        f"{update.message.caption or 'Please look at this file.'}"
    )

    try:
        reply, attachments = run_agent_loop(chat_id, synthetic_message)
    except Exception:
        logger.exception("Agent loop failed on document")
        reply, attachments = "عذرًا، حدث خطأ أثناء معالجة الملف.", []

    await _reply_with_attachments(update, reply, attachments)


def main() -> None:
    global BOT_APP
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    BOT_APP = app

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Bot starting (model=%s)...", MODEL)
    app.run_polling()


if __name__ == "__main__":
    main()
