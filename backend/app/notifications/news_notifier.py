import os

import requests
from dotenv import load_dotenv


load_dotenv()


class NewsNotifier:
    def __init__(self):
        self.bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID")

        if not self.bot_token:
            raise ValueError("TELEGRAM_BOT_TOKEN is missing from .env")

        if not self.chat_id:
            raise ValueError("TELEGRAM_CHAT_ID is missing from .env")

        self.url = (
            f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        )

    def send_message(self, message: str) -> None:
        try:
            response = requests.post(
                self.url,
                json={
                    "chat_id": self.chat_id,
                    "text": message,
                },
                timeout=10,
            )

            response.raise_for_status()
        except requests.RequestException:
            raise RuntimeError("Telegram notification failed") from None

    def send_morning_summary(self, message: str) -> None:
        self.send_message(message)

    def send_news_alert(self, message: str) -> None:
        self.send_message(message)

    def send_calendar_update(self, message: str) -> None:
        self.send_message(message)


if __name__ == "__main__":
    notifier = NewsNotifier()

    notifier.send_message(
        "✅ TradeMind AI Telegram connection successful!"
    )

    print("Telegram test notification sent successfully.")