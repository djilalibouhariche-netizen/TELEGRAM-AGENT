"""
Telegram Agent powered by multiple AI providers (multi-provider, auto-fallback)
--------------------------------------------------------------------------------
Every Telegram message is sent to an AI model along with a set of tools. The
bot tries providers in order (e.g. Claude -> Gemini -> Groq -> Grok) and
automatically falls back to the next one if a provider fails (out of credit,
rate limited, down, etc). Only providers whose API key is set in the
environment are used — you can enable just the free ones (Gemini + Groq) and
skip the paid ones entirely.

Uses litellm (https://github.com/BerriAI/litellm) so every provider is called
through the same unified, OpenAI-style interface — including tool calling.

Deploy this on Railway / Render / any VPS. See README.md for instructions.
"""

import json
import logging
import os

import litellm
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
litellm.suppress_debug_info = True

# ---- Configuration -------------------------------------------------------
TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]

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

# ---- Providers, tried in this order. Only providers whose API key is set
# ---- are actually used — you don't need to fill all of them in, just set
# ---- the ones you actually created a key for. Free-tier terms change
# ---- often, so double-check each provider's console before relying on it.
# ---- Roughly ordered: free-without-card first, then free-trial, then paid.
_ALL_PROVIDERS = [
    # --- Free tier, historically no card required ---
    {"name": "gemini", "model": os.environ.get("GEMINI_MODEL", "gemini/gemini-2.0-flash"), "key_env": "GEMINI_API_KEY"},
    {"name": "groq", "model": os.environ.get("GROQ_MODEL", "groq/llama-3.3-70b-versatile"), "key_env": "GROQ_API_KEY"},
    {"name": "cerebras", "model": os.environ.get("CEREBRAS_MODEL", "cerebras/llama-3.3-70b"), "key_env": "CEREBRAS_API_KEY"},
    {"name": "openrouter", "model": os.environ.get("OPENROUTER_MODEL", "openrouter/meta-llama/llama-3.3-70b-instruct:free"), "key_env": "OPENROUTER_API_KEY"},
    {"name": "mistral", "model": os.environ.get("MISTRAL_MODEL", "mistral/mistral-small-latest"), "key_env": "MISTRAL_API_KEY"},
    {"name": "huggingface", "model": os.environ.get("HF_MODEL", "huggingface/meta-llama/Llama-3.3-70B-Instruct"), "key_env": "HUGGINGFACE_API_KEY"},
    {"name": "cohere", "model": os.environ.get("COHERE_MODEL", "command-r-plus"), "key_env": "COHERE_API_KEY"},
    # --- Free trial credits at signup (may ask for a card to verify) ---
    {"name": "together", "model": os.environ.get("TOGETHER_MODEL", "together_ai/meta-llama/Llama-3.3-70B-Instruct-Turbo"), "key_env": "TOGETHER_API_KEY"},
    {"name": "deepseek", "model": os.environ.get("DEEPSEEK_MODEL", "deepseek/deepseek-chat"), "key_env": "DEEPSEEK_API_KEY"},
    {"name": "fireworks", "model": os.environ.get("FIREWORKS_MODEL", "fireworks_ai/llama-v3p3-70b-instruct"), "key_env": "FIREWORKS_API_KEY"},
    # --- Paid, require a funded/billed account ---
    {"name": "claude", "model": os.environ.get("CLAUDE_MODEL", "claude-sonnet-5"), "key_env": "ANTHROPIC_API_KEY"},
    {"name": "openai", "model": os.environ.get("OPENAI_MODEL", "gpt-4o-mini"), "key_env": "OPENAI_API_KEY"},
    {"name": "grok", "model": os.environ.get("GROK_MODEL", "xai/grok-4"), "key_env": "XAI_API_KEY"},
]
PROVIDERS = [p for p in _ALL_PROVIDERS if os.environ.get(p["key_env"])]

if not PROVIDERS:
    raise RuntimeError(
        "No AI provider configured. Set at least one of: "
        "GEMINI_API_KEY, GROQ_API_KEY, ANTHROPIC_API_KEY, XAI_API_KEY"
    )

logger.info("Active providers (in fallback order): %s", [p["name"] for p in PROVIDERS])

# In-memory conversation store: {chat_id: [ {role, content, ...}, ... ]}
# NOTE: this resets whenever the process restarts. For persistence across
# restarts, swap this dict for a small database (see README).
conversations: dict[int, list] = {}

# Set once in main(); tools (like set_reminder) use it to schedule jobs.
BOT_APP: Application | None = None

UPLOADS_DIR = "/tmp/bot_files/uploads"
os.makedirs(UPLOADS_DIR, exist_ok=True)

# Stores the full breakdown of the last provider failure so /debug can show
# it directly in Telegram (no need to dig through Railway logs).
LAST_ERROR: str | None = None


def _openai_tools():
    """Convert the existing Anthropic-style TOOLS list to the OpenAI-style
    'tools' format that litellm expects for every provider."""
    converted = []
    for t in TOOLS:
        converted.append(
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t.get("description", ""),
                    "parameters": t.get("input_schema", {"type": "object", "properties": {}}),
                },
            }
        )
    return converted


OPENAI_TOOLS = _openai_tools()


def _call_model_with_fallback(messages: list):
    """Try each configured provider in order; return the first successful
    litellm response. Raises the last error if every provider fails."""
    global LAST_ERROR
    errors = []
    for provider in PROVIDERS:
        try:
            response = litellm.completion(
                model=provider["model"],
                messages=messages,
                tools=OPENAI_TOOLS,
                max_tokens=1024,
            )
            LAST_ERROR = None  # a provider succeeded, clear any previous failure
            return response
        except Exception as exc:  # noqa: BLE001 - we want to fall back on *any* error
            err_text = f"❌ {provider['name']} ({provider['model']}): {type(exc).__name__}: {exc}"
            logger.warning("Provider '%s' failed (%s) — trying next provider.", provider["name"], exc)
            errors.append(err_text)

    LAST_ERROR = "\n\n".join(errors) if errors else "No provider configured."
    raise RuntimeError(LAST_ERROR)


def run_agent_loop(chat_id: int, user_text: str):
    """Send the user's message to the AI, execute any tool calls, and
    return (final_text, attachments) where attachments is a list of local
    file paths to send back to the user on Telegram."""
    history = conversations.setdefault(chat_id, [])
    if not history:
        history.append({"role": "system", "content": SYSTEM_PROMPT})

    history.append({"role": "user", "content": user_text})

    if len(history) > MAX_HISTORY_MESSAGES:
        # Always keep the system message (index 0) plus the most recent turns.
        history[:] = [history[0]] + history[-(MAX_HISTORY_MESSAGES - 1):]

    attachments: list[str] = []

    while True:
        response = _call_model_with_fallback(history)
        message = response.choices[0].message

        tool_calls = getattr(message, "tool_calls", None)

        if not tool_calls:
            text = message.content or "..."
            history.append({"role": "assistant", "content": text})
            return text, attachments

        serialized_calls = [
            {
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.function.name, "arguments": tc.function.arguments},
            }
            for tc in tool_calls
        ]
        history.append({"role": "assistant", "content": message.content, "tool_calls": serialized_calls})

        for tc in tool_calls:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}

            result = execute_tool(tc.function.name, args, chat_id=chat_id, app=BOT_APP)
            if isinstance(result, dict):
                if result.get("attachment"):
                    attachments.append(result["attachment"])
                content = result.get("text", "Done.")
            else:
                content = str(result)

            history.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "name": tc.function.name,
                    "content": content,
                }
            )
        # loop again so the model can use the tool result


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "أهلاً! أنا مساعدك الذكي. أرسل لي أي رسالة، أو ملف PDF/Excel، وسأساعدك.\n"
        "أرسل /reset لمسح ذاكرة المحادثة."
    )


async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    conversations.pop(update.effective_chat.id, None)
    await update.message.reply_text("تم مسح ذاكرة المحادثة. ابدأ من جديد!")


async def debug_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Shows the full breakdown of the last provider failure, straight in
    Telegram, so you don't need to dig through Railway/Render logs."""
    active = ", ".join(p["name"] for p in PROVIDERS) or "(none configured)"
    header = f"المزودون المفعّلون حاليًا: {active}\n\n"
    if LAST_ERROR:
        await update.message.reply_text(header + LAST_ERROR[:3800])
    else:
        await update.message.reply_text(header + "لا يوجد خطأ مسجل حتى الآن. أرسل رسالة عادية أولاً حتى يفشل، ثم أعد /debug.")


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
        reply, attachments = "عذرًا، حدث خطأ أثناء معالجة طلبك. أرسل /debug لمعرفة السبب.", []

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
        reply, attachments = "عذرًا، حدث خطأ أثناء معالجة الملف. أرسل /debug لمعرفة السبب.", []

    await _reply_with_attachments(update, reply, attachments)


def main() -> None:
    global BOT_APP
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    BOT_APP = app

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(CommandHandler("debug", debug_cmd))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Bot starting...")
    app.run_polling()


if __name__ == "__main__":
    main()
