import httpx
import os

class NotificationService:
    def __init__(self):
        self.telegram_bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID")

    async def send_telegram_alert(self, message: str):
        if not self.telegram_bot_token or not self.chat_id:
            print(f"NOTIFICATION [MOCK]: {message}")
            return
        
        url = f"https://api.telegram.org/bot{self.telegram_bot_token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": message,
            "parse_mode": "Markdown"
        }
        async with httpx.AsyncClient() as client:
            await client.post(url, json=payload)

    async def notify_new_recommendation(self, rec: dict):
        message = (
            f"🚀 *New Trade Recommendation*\n\n"
            f"Symbol: {rec['symbol']}\n"
            f"Side: {rec['side'].value}\n"
            f"Entry: {rec['entry_price']}\n"
            f"Target: {rec['target_price']}\n"
            f"SL: {rec['stop_loss']}\n"
            f"Confidence: {rec['confidence_score']:.2f}\n\n"
            f"Reasoning: {rec['reasoning']}\n\n"
            f"Please approve or reject on the dashboard."
        )
        await self.send_telegram_alert(message)

notification_service = NotificationService()
