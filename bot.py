import os
import logging
from duckduckgo_search import DDGS
from groq import Groq
from google import genai
from google.genai import types
from openai import OpenAI
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

# إعداد السجلات
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# 1. Groq Client
GROQ_KEY = os.environ.get("GROQ_API_KEY")
groq_client = Groq(api_key=GROQ_KEY) if GROQ_KEY else None

# 2. OpenRouter Client
OPENROUTER_KEY = os.environ.get("OPENROUTER_API_KEY")
openrouter_client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=OPENROUTER_KEY
) if OPENROUTER_KEY else None

# 3. Gemini Client
GEMINI_KEY = os.environ.get("GEMINI_API_KEY")
gemini_client = genai.Client(api_key=GEMINI_KEY) if GEMINI_KEY else None

# 4. Mistral Client
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
                results.append(f"المصدر: {r['title']}\nالرابط: {r['href']}\nالملخص: {r['body']}\n")
        if not results:
            return "لم يتم العثور على نتائج بحث مباشرة."
        return "\n---\n".join(results)
    except Exception as e:
        return f"حدث خطأ أثناء البحث: {str(e)}"

# --- محركات الاستجابة مع دعم المحاولات الاحتياطية ---

def ask_groq(prompt_text: str) -> str:
    if not groq_client: return None
    # تجربة أحدث النماذج المتاحة مجاناً في Groq
    groq_models = ["llama-3.3-70b-versatile", "llama3-8b-8192", "mixtral-8x7b-32768"]
    for model_name in groq_models:
        try:
            res = groq_client.chat.completions.create(
                model=model_name,
                messages=[
                    {"role": "system", "content": SYSTEM_INSTRUCTION},
                    {"role": "user", "content": prompt_text}
                ],
                temperature=0.0
            )
            logging.info(f"نجح Groq باستخدام النموذج: {model_name}")
            return res.choices[0].message.content
        except Exception as e:
            logging.warning(f"فشل Groq ({model_name}): {e}")
            continue
    return None

def ask_gemini(prompt_text: str) -> str:
    if not gemini_client: return None
    # تجربة نماذج Gemini المدعومة في مكتبة google-genai
    gemini_models = ['gemini-2.0-flash', 'gemini-1.5-flash-latest', 'gemini-2.5-flash']
    for model_name in gemini_models:
        try:
            res = gemini_client.models.generate_content(
                model=model_name,
                contents=prompt_text,
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_INSTRUCTION,
                    temperature=0.0
                )
            )
            logging.info(f"نجح Gemini باستخدام النموذج: {model_name}")
            return res.text
        except Exception as e:
            logging.warning(f"فشل Gemini ({model_name}): {e}")
            continue
    return None

def ask_openrouter(prompt_text: str) -> str:
    if not openrouter_client: return None
    models = ["google/gemini-2.0-flash-001:free", "meta-llama/llama-3.3-70b-instruct:free", "openrouter/auto"]
    for m in models:
        try:
            res = openrouter_client.chat.completions.create(
                model=m,
                messages=[
                    {"role": "system", "content": SYSTEM_INSTRUCTION},
                    {"role": "user", "content": prompt_text}
                ],
                temperature=0.0
            )
            return res.choices[0].message.content
        except Exception as e:
            logging.warning(f"فشل OpenRouter ({m}): {e}")
            continue
    return None

def ask_mistral(prompt_text: str) -> str:
    if not mistral_client: return None
    try:
        res = mistral_client.chat.completions.create(
            model="mistral-small-latest",
            messages=[
                {"role": "system", "content": SYSTEM_INSTRUCTION},
                {"role": "user", "content": prompt_text}
            ],
            temperature=0.0
        )
        return res.choices[0].message.content
    except Exception as e:
        logging.warning(f"فشل Mistral: {e}")
        return None

def generate_multi_engine_response(query: str, search_context: str = "") -> str:
    if search_context:
        full_prompt = f"نتائج البحث المباشر:\n{search_context}\n\nطلب المستخدم:\n{query}\n\nأجب بدقة وبناءً على الحقائق المتاحة فقط."
    else:
        full_prompt = query

    engines = [
        ("Groq", ask_groq),
        ("Google Gemini", ask_gemini),
        ("OpenRouter Free", ask_openrouter),
        ("Mistral AI", ask_mistral),
    ]

    for name, engine_func in engines:
        try:
            answer = engine_func(full_prompt)
            if answer:
                logging.info(f"تمت الاستجابة بنجاح عبر: {name}")
                return answer
        except Exception as e:
            logging.warning(f"خطأ غير متوقع في {name}: {e}")
            continue

    return "عذراً، تعذر الحصول على إجابة من المحركات المتاحة. يرجى التأكد من إضافة مفاتيح API الصحيحة في متغيرات البيئة."

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome = (
        "مرحباً بك! أنا مساعد العمل الذكي المتعدد المحركات.\n\n"
        "🌐 **المحركات المدمجة:**\n"
        "• Groq (Llama 3.3 / Llama 3)\n"
        "• Google Gemini (2.0 Flash)\n"
        "• OpenRouter Free\n"
        "• Mistral AI\n\n"
        "📌 **للبحث الميداني في النت:** اكتب قبل سؤالك كلمة **بحث** أو **search**."
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
    await update.message.reply_text(response_text)

if __name__ == '__main__':
    bot_token = os.environ.get("BOT_TOKEN")
    if not bot_token:
        raise ValueError("يرجى ضبط متغير البيئة BOT_TOKEN")

    app = ApplicationBuilder().token(bot_token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("البوت المتعدد المحركات يعمل الآن بنجاح...")
    app.run_polling()
