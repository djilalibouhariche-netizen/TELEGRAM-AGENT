import os
import logging
from duckduckgo_search import DDGS
from groq import Groq
from google import genai
from google.genai import types
from openai import OpenAI
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

# إعداد السجلات (Logging)
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# 1. إعداد عميل Groq
GROQ_KEY = os.environ.get("GROQ_API_KEY")
groq_client = Groq(api_key=GROQ_KEY) if GROQ_KEY else None

# 2. إعداد عميل OpenRouter
OPENROUTER_KEY = os.environ.get("OPENROUTER_API_KEY")
openrouter_client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=OPENROUTER_KEY
) if OPENROUTER_KEY else None

# 3. إعداد عميل Gemini
GEMINI_KEY = os.environ.get("GEMINI_API_KEY")
gemini_client = genai.Client(api_key=GEMINI_KEY) if GEMINI_KEY else None

# 4. إعداد عميل Mistral
MISTRAL_KEY = os.environ.get("MISTRAL_API_KEY")
mistral_client = OpenAI(
    base_url="https://api.mistral.ai/v1",
    api_key=MISTRAL_KEY
) if MISTRAL_KEY else None

SYSTEM_INSTRUCTION = """
أنت مساعد مكتبي وباحث ومبرمج احترافي صارم.
تلتزم بالقواعد التالية بدقة:
1. الموثوقية التامة: تقديم حقائق ومعلومات مؤكدة فقط بدون تخمين أو استنتاجات ظنية.
2. غياب المعلومة: إذا لم تتوفر لديك بيانات كافية، صرح فوراً: "لا تتوفر أدلة أو بيانات مؤكدة حول هذا الموضوع".
3. البرمجة والكودينغ: تقديم أكواد برمجية نظيفة، موثقة، وخالية من الأخطاء مع شرح خطوات التشغيل.
4. الأعمال الإدارية: صياغة الخطابات والمستندات بأسلوب رسمي واحترافي (عربي / فرنسي).
5. البحث والتحقق: الاعتماد على نتائج البحث المباشرة للإجابة بدقة وحياد.
"""

def free_web_search(query: str, max_results: int = 5) -> str:
    """بحث مجاني عبر DuckDuckGo"""
    try:
        results = []
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=max_results):
                title = r.get('title', '')
                href = r.get('href', '')
                body = r.get('body', '')
                results.append(f"المصدر: {title}\nالرابط: {href}\nالملخص: {body}\n")
        if not results:
            return "لم يتم العثور على نتائج بحث مباشرة."
        return "\n---\n".join(results)
    except Exception as e:
        logging.error(f"خطأ أثناء البحث: {e}")
        return f"تعذر إجراء البحث المباشر: {str(e)}"

# --- محركات الاستجابة الذكية ---

def ask_groq(prompt_text: str) -> str:
    if not GROQ_KEY or not groq_client: 
        return None
    # قائمة الموديلات المعتمدة الشغالة حالياً في Groq
    models = ["llama-3.1-8b-instant", "llama-3.3-70b-versatile", "qwen-2.5-coder-32b"]
    for m in models:
        try:
            res = groq_client.chat.completions.create(
                model=m,
                messages=[
                    {"role": "system", "content": SYSTEM_INSTRUCTION},
                    {"role": "user", "content": prompt_text}
                ],
                temperature=0.1
            )
            if res.choices and res.choices[0].message.content:
                logging.info(f"نجح Groq باستخدام الموديل: {m}")
                return res.choices[0].message.content.strip()
        except Exception as e:
            logging.warning(f"فشل Groq ({m}): {e}")
            continue
    return None

def ask_gemini(prompt_text: str) -> str:
    if not GEMINI_KEY or not gemini_client: 
        return None
    # الموديلات المعتمدة حسب التحديث الأخير لـ Google API
    models = ["gemini-3.8-flash", "gemini-2.5-flash", "gemini-1.5-flash"]
    for m in models:
        try:
            res = gemini_client.models.generate_content(
                model=m,
                contents=prompt_text,
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_INSTRUCTION,
                    temperature=0.1
                )
            )
            if res and res.text:
                logging.info(f"نجح Gemini باستخدام الموديل: {m}")
                return res.text.strip()
        except Exception as e:
            logging.warning(f"فشل Gemini ({m}): {e}")
            continue
    return None

def ask_openrouter(prompt_text: str) -> str:
    if not OPENROUTER_KEY or not openrouter_client: 
        return None
    models = [
        "meta-llama/llama-3.3-70b-instruct:free",
        "qwen/qwen-2.5-72b-instruct:free",
        "google/gemini-2.0-flash-001:free",
        "openrouter/auto"
    ]
    for m in models:
        try:
            res = openrouter_client.chat.completions.create(
                model=m,
                messages=[
                    {"role": "system", "content": SYSTEM_INSTRUCTION},
                    {"role": "user", "content": prompt_text}
                ],
                temperature=0.1
            )
            if res.choices and res.choices[0].message.content:
                logging.info(f"نجح OpenRouter باستخدام الموديل: {m}")
                return res.choices[0].message.content.strip()
        except Exception as e:
            logging.warning(f"فشل OpenRouter ({m}): {e}")
            continue
    return None

def ask_mistral(prompt_text: str) -> str:
    if not MISTRAL_KEY or not mistral_client: 
        return None
    models = ["mistral-small-latest", "open-mistral-7b"]
    for m in models:
        try:
            res = mistral_client.chat.completions.create(
                model=m,
                messages=[
                    {"role": "system", "content": SYSTEM_INSTRUCTION},
                    {"role": "user", "content": prompt_text}
                ],
                temperature=0.1
            )
            if res.choices and res.choices[0].message.content:
                logging.info(f"نجح Mistral باستخدام الموديل: {m}")
                return res.choices[0].message.content.strip()
        except Exception as e:
            logging.warning(f"فشل Mistral ({m}): {e}")
            continue
    return None

def generate_multi_engine_response(query: str, search_context: str = "") -> str:
    if search_context:
        full_prompt = (
            f"نتائج البحث المباشر من الإنترنت:\n{search_context}\n\n"
            f"طلب المستخدم:\n{query}\n\n"
            f"قم بالإجابة بدقة وبناءً على الحقائق الواردة في نتائج البحث أعلاه."
        )
    else:
        full_prompt = query

    engines = [
        ("Groq", ask_groq),
        ("Google Gemini", ask_gemini),
        ("OpenRouter", ask_openrouter),
        ("Mistral AI", ask_mistral),
    ]

    for name, engine_func in engines:
        try:
            answer = engine_func(full_prompt)
            if answer and answer.strip():
                logging.info(f"تمت الاستجابة عبر المحرك: {name}")
                return answer
        except Exception as e:
            logging.warning(f"خطأ في محرك {name}: {e}")
            continue

    if search_context and search_context != "لم يتم العثور على نتائج بحث مباشرة.":
        return f"نتائج البحث المباشر:\n\n{search_context}"

    return "عذراً، لم تنجح الاستجابة من المحركات المتاحة. يرجى التحقق من مفاتيح الـ API في متغيرات البيئة."

async def send_response(update: Update, text: str):
    """تجزئة الرسائل الطويلة وحماية البوت من أخطاء تنسيق Markdown"""
    max_length = 4000
    if not text:
        text = "لم يتم الحصول على إجابة."
        
    chunks = [text[i:i + max_length] for i in range(0, len(text), max_length)]
    for chunk in chunks:
        try:
            await update.message.reply_text(chunk, parse_mode="Markdown")
        except Exception:
            await update.message.reply_text(chunk)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome = (
        "مرحباً بك! أنا مساعد العمل الذكي المتعدد المحركات.\n\n"
        "🌐 **المحركات المدمجة:**\n"
        "• Groq (Llama 3.1 / Llama 3.3)\n"
        "• Google Gemini (3.8 Flash)\n"
        "• OpenRouter Free\n"
        "• Mistral AI\n\n"
        "📌 **للبحث الميداني:** اكتب قبل سؤالك كلمة **بحث** أو **search**."
    )
    await update.message.reply_text(welcome, parse_mode="Markdown")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text
    if not user_text:
        return

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    is_search = user_text.strip().lower().startswith(("بحث", "search", "ابحث"))
    search_context = ""
    clean_query = user_text

    if is_search:
        clean_query = user_text.replace("بحث", "").replace("search", "").replace("ابحث", "").strip()
        await update.message.reply_text(f"🔍 جاري البحث الميداني عن: `{clean_query}`...", parse_mode="Markdown")
        search_context = free_web_search(clean_query)

    response_text = generate_multi_engine_response(clean_query, search_context)
    await send_response(update, response_text)

if __name__ == '__main__':
    bot_token = os.environ.get("BOT_TOKEN")
    if not bot_token:
        raise ValueError("يرجى ضبط متغير البيئة BOT_TOKEN")

    app = ApplicationBuilder().token(bot_token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("البوت المتعدد المحركات يعمل الآن بنجاح...")
    app.run_polling(drop_pending_updates=True)
