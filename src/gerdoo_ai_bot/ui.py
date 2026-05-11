from __future__ import annotations

BTN_NEW_CHAT = "🧹 چت جدید"
BTN_MODEL = "🧠 انتخاب مدل"
BTN_STATUS = "📊 وضعیت"
BTN_HELP = "❓ راهنما"


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
            [BTN_NEW_CHAT, BTN_MODEL],
            [BTN_STATUS, BTN_HELP],
        ]
    )


def model_selection_menu(models: list[str], current_model: str) -> dict:
    rows: list[list[dict]] = []
    for index, model in enumerate(models):
        prefix = "✅ " if model == current_model else ""
        rows.append(
            [
                {
                    "text": f"{prefix}{model}",
                    "callback_data": f"mdl:set:{index}",
                }
            ]
        )
    rows.append([{"text": "🔁 بازگشت به منوی اصلی", "callback_data": "mdl:close"}])
    return inline_keyboard(rows)


def welcome_text(default_model: str, history_limit: int) -> str:
    return (
        "به ربات چت هوش مصنوعی خوش آمدید.\n\n"
        "هر پیامی ارسال کنید تا پاسخ هوش مصنوعی را بگیرید.\n"
        f"مدل پیش‌فرض: {default_model}\n"
        f"حافظه گفتگو: {history_limit} پیام اخیر"
    )


def help_text() -> str:
    return (
        "راهنما:\n"
        "- متن عادی: ارسال به هوش مصنوعی\n"
        "- دکمه «چت جدید»: پاک کردن تاریخچه همین گفتگو\n"
        "- دکمه «انتخاب مدل»: تغییر مدل فعال شما\n"
        "- دکمه «وضعیت»: نمایش تنظیمات فعلی شما"
    )
