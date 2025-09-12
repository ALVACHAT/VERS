from telegram.ext import ApplicationBuilder, CommandHandler
import os
from VERS119 import start_command, check_command, status_command, charts_command

TOKEN = os.environ.get("BOT_TOKEN", "TWÓJ_TOKEN")       # najlepiej ustawić jako zmienną środowiskową
PORT = int(os.environ.get("PORT", 8443))
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "https://TWÓJ_RAILWAY_URL.com/")

app = ApplicationBuilder().token(TOKEN).build()

# Dodanie komend
app.add_handler(CommandHandler("start", start_command))
app.add_handler(CommandHandler("check", check_command))
app.add_handler(CommandHandler("status", status_command))
app.add_handler(CommandHandler("charts", charts_command))

# Uruchomienie webhook
app.run_webhook(
    listen="0.0.0.0",
    port=PORT,
    webhook_url=WEBHOOK_URL
)
