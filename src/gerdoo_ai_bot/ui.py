from __future__ import annotations

BTN_NEW_CHAT = "💬 گفتگوی جدید"
BTN_CHAT = "💬 چت هوشمند"
BTN_IMAGE_GENERATION = "🖼️ تولید تصویر"
BTN_TRANSCRIBE = "🎙️ تبدیل ویس به متن"
BTN_CANCEL = "❌ لغو عملیات"
BTN_HELP = "❓ راهنما"
BTN_SKIP_AUDIO_TOPIC = "⏭️ بدون موضوع"


def reply_keyboard(rows: list[list[str]], resize: bool = True) -> dict:
    keyboard = [[{"text": text} for text in row] for row in rows]
    return {
        "keyboard": keyboard,
        "resize_keyboard": resize,
        "one_time_keyboard": False,
    }


def inline_keyboard(rows: list[list[dict]]) -> dict:
    return {"inline_keyboard": rows}


def main_menu() -> dict:
    return reply_keyboard(
        [
            [BTN_CHAT, BTN_IMAGE_GENERATION],
            [BTN_TRANSCRIBE, BTN_NEW_CHAT],
            [BTN_HELP],
        ]
    )


def cancel_only_menu() -> dict:
    return reply_keyboard([[BTN_CANCEL]])


def audio_topic_menu() -> dict:
    return cancel_only_menu()


def image_result_inline(generation_id: str) -> dict:
    gid = (generation_id or "").strip()
    return inline_keyboard(
        [
            [
                {"text": "🔁 تولید مجدد", "callback_data": f"img:regen:{gid}"},
                {"text": "🧾 پرامپت بهبود‌یافته", "callback_data": f"img:prompt:{gid}"},
            ],
            [
                {"text": "👍", "callback_data": f"img:fb:like:{gid}"},
                {"text": "👎", "callback_data": f"img:fb:dislike:{gid}"},
            ],
        ]
    )


def welcome_text(history_limit: int) -> str:
    return (
        "🌱 گفتگوی تازه شروع شد.\n"
        "از اینجا یک مسیر جدید باز می‌شود و روی پاسخ‌های بعدی تمرکز می‌کنم.\n\n"
        "برای شروع می‌تونی یکی از این ایده‌ها را امتحان کنی:\n"
        "• 😌 چند روش عملی برای آرام‌تر شدن در روزهای شلوغ پیشنهاد بده\n"
        "• 📗 یک موضوع درسی را ساده، قدم‌به‌قدم و با مثال توضیح بده\n\n"
        "مسیرهای آماده:\n"
        "• 💬 چت هوشمند\n"
        "• 🖼️ تولید تصویر\n"
        "• 🎙️ تبدیل ویس به متن\n\n"
        f"حافظه گفتگو: {history_limit} پیام اخیر"
    )


def help_text(*, max_voice_minutes: int = 5) -> str:
    return (
        "راهنما:\n"
        "- 💬 گفتگوی جدید: یک شروع تازه با تاریخچه‌ی پاک.\n"
        "- 💬 چت هوشمند: متن عادی بفرست تا پاسخ بگیری.\n"
        "- 🖼️ تولید تصویر: دکمه را بزن، سپس پرامپت عکس را بفرست.\n"
        "- در چت عادی، اگر عکس بفرستی تحلیل می‌کنم.\n"
        "- در چت عادی، اگر ویس بفرستی متنش را تحلیل می‌کنم.\n"
        "- 🎙️ تبدیل ویس به متن: ویس را فقط به متن تبدیل می‌کنم.\n"
        f"- محدودیت ویس: حداکثر {max_voice_minutes} دقیقه.\n"
        "- ❌ لغو عملیات: خروج از مسیرهای مرحله‌ای."
    )
