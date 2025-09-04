import telebot
import requests
import os
import re
import sys
import io
import tempfile

# Get the bot token from environment variables
BOT_TOKEN = os.getenv('BOT_TOKEN')

if not BOT_TOKEN:
    print("Error: BOT_TOKEN environment variable is not set.")
    sys.exit(1)

bot = telebot.TeleBot(BOT_TOKEN)

# User states and data
user_states = {}
user_data = {}

@bot.message_handler(commands=['start'])
def handle_start(message):
    """
    Handles the /start command and resets the session.
    """
    chat_id = message.chat.id
    reset_user(chat_id)
    bot.send_message(
        chat_id,
        "👋 Welcome!\n\n"
        "Please send me the URL of the large file you want to search. "
        "This file will be used for all subsequent domain searches until you type /start or /reset again."
    )

@bot.message_handler(commands=['reset'])
def handle_reset(message):
    """
    Handles the /reset command and clears stored file.
    """
    chat_id = message.chat.id
    reset_user(chat_id)
    bot.send_message(
        chat_id,
        "🔄 Session has been reset!\n\n"
        "Please send me a new file URL to continue."
    )

def reset_user(chat_id):
    """
    Clears the state and deletes any stored temp file.
    """
    # Delete old file if it exists
    if chat_id in user_data and 'file_path' in user_data[chat_id]:
        try:
            os.remove(user_data[chat_id]['file_path'])
        except Exception:
            pass

    user_states[chat_id] = 'awaiting_url'
    user_data[chat_id] = {}

@bot.message_handler(func=lambda message: user_states.get(message.chat.id) == 'awaiting_url')
def handle_url(message):
    """
    Downloads the file once and stores it locally.
    """
    chat_id = message.chat.id
    url = message.text.strip()

    if not url.startswith(('http://', 'https://')):
        bot.send_message(chat_id, "⚠️ Please enter a valid URL starting with http:// or https://")
        return

    try:
        bot.send_message(chat_id, "⏳ Downloading file... Please wait, this may take a while for large files.")

        response = requests.get(url, stream=True, timeout=600)
        response.raise_for_status()

        # Save file to a temporary location
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".txt")
        for chunk in response.iter_content(chunk_size=1024*1024):  # 1 MB chunks
            if chunk:
                temp_file.write(chunk)
        temp_file.close()

        # Store file path and change state
        user_data[chat_id]['file_path'] = temp_file.name
        user_states[chat_id] = 'awaiting_domain'

        bot.send_message(
            chat_id,
            "✅ File downloaded and saved!\n\n"
            "🔍 Now send me a domain (e.g., example.com) to search."
        )

    except Exception as e:
        bot.send_message(chat_id, f"❌ Error downloading file: {e}")

@bot.message_handler(func=lambda message: user_states.get(message.chat.id) == 'awaiting_domain')
def handle_domain_and_search(message):
    """
    Searches the local file for the given domain and sends results.
    """
    chat_id = message.chat.id
    target_domain = message.text.strip()
    file_path = user_data[chat_id].get('file_path')

    if not file_path:
        bot.send_message(chat_id, "⚠️ No file loaded. Please use /start or /reset.")
        return

    # Notify user search is starting
    bot.send_message(chat_id, f"🔍 Searching for `{target_domain}` in your file... Please wait ⏳",
                     parse_mode="Markdown")

    found_lines_stream = io.BytesIO()
    found_lines_count = 0

    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                if re.search(r'\b' + re.escape(target_domain) + r'\b', line, re.IGNORECASE):
                    found_lines_stream.write(line.encode("utf-8"))
                    found_lines_count += 1

        if found_lines_count > 0:
            found_lines_stream.seek(0)
            bot.send_document(
                chat_id,
                found_lines_stream,
                visible_file_name=f"search_results_{target_domain}.txt",
                caption=f"✅ Found {found_lines_count} lines for *{target_domain}*.\n\n"
                        "👉 You can type another domain to search again.",
                parse_mode="Markdown"
            )
        else:
            bot.send_message(chat_id, f"❌ No results for `{target_domain}`.\n\n"
                                      "👉 Try another domain.", parse_mode="Markdown")

        # Keep state in search mode
        user_states[chat_id] = 'awaiting_domain'

    except Exception as e:
        bot.send_message(chat_id, f"⚠️ Error while searching: {e}")

    finally:
        found_lines_stream.close()

# Start the bot
if __name__ == '__main__':
    print("🤖 Bot is running...")
    bot.polling(none_stop=True)
