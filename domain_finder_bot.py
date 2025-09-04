import os
import re
import io
import threading
import requests
import telebot
from flask import Flask
from telebot import types

# --- Telegram Bot Setup ---
BOT_TOKEN = os.getenv('BOT_TOKEN')
if not BOT_TOKEN:
    print("Error: BOT_TOKEN environment variable is not set.")
    exit(1)

bot = telebot.TeleBot(BOT_TOKEN)

# --- Flask App for Koyeb Health Check ---
app = Flask(__name__)

@app.route('/')
def health():
    return "OK", 200

def run_flask():
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port)

# --- Bot State ---
user_states = {}
user_data = {}

def reset_user(chat_id):
    user_states[chat_id] = None
    user_data[chat_id] = {'links': {}, 'temp_url': None}

# --- Main Menu ---
def send_main_menu(chat_id):
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("📤 Add Link", callback_data="upload_file"),
        types.InlineKeyboardButton("🔍 Search", callback_data="search"),
        types.InlineKeyboardButton("🗑 Delete", callback_data="delete")
    )
    bot.send_message(chat_id, "📌 Choose an action:", reply_markup=markup)

# --- Start Command ---
@bot.message_handler(commands=['start'])
def handle_start(message):
    reset_user(message.chat.id)
    send_main_menu(message.chat.id)

# --- Callback Handler ---
@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    chat_id = call.message.chat.id

    if call.data == "upload_file":
        user_states[chat_id] = 'awaiting_url'
        bot.send_message(chat_id, "📤 Send me the file URL.")

    elif call.data == "search":
        links = user_data.get(chat_id, {}).get('links', {})
        if not links:
            bot.send_message(chat_id, "⚠️ No links added yet.")
            send_main_menu(chat_id)
            return
        markup = types.InlineKeyboardMarkup()
        markup.add(
            types.InlineKeyboardButton("🔍 Search one file", callback_data="search_one"),
            types.InlineKeyboardButton("🔎 Search all files", callback_data="search_all")
        )
        bot.send_message(chat_id, "Choose search mode:", reply_markup=markup)

    elif call.data == "search_one":
        choose_file_for_search(chat_id)

    elif call.data == "search_all":
        user_states[chat_id] = "awaiting_domain_all"
        bot.send_message(chat_id, "🔎 Send me the domain to search across all files.")

    elif call.data == "delete":
        links = user_data.get(chat_id, {}).get('links', {})
        if not links:
            bot.send_message(chat_id, "⚠️ No links to delete.")
            send_main_menu(chat_id)
        else:
            markup = types.InlineKeyboardMarkup()
            for fname in links.keys():
                markup.add(types.InlineKeyboardButton(f"🗑 {fname}", callback_data=f"delete_file:{fname}"))
            bot.send_message(chat_id, "Select a link to delete:", reply_markup=markup)

    elif call.data.startswith("delete_file:"):
        fname = call.data.split("delete_file:")[1]
        links = user_data.get(chat_id, {}).get('links', {})
        if fname in links:
            del links[fname]
            bot.send_message(chat_id, f"✅ Link `{fname}` removed.", parse_mode="Markdown")
        else:
            bot.send_message(chat_id, "⚠️ Link not found.")
        send_main_menu(chat_id)

    elif call.data.startswith("search_file:"):
        fname = call.data.split("search_file:")[1]
        if fname in user_data[chat_id]['links']:
            user_states[chat_id] = f"awaiting_domain:{fname}"
            bot.send_message(chat_id, f"🔍 Send me the domain to search in `{fname}`", parse_mode="Markdown")
        else:
            bot.send_message(chat_id, "⚠️ Link not found.")
            send_main_menu(chat_id)

# --- Upload Flow ---
@bot.message_handler(func=lambda m: user_states.get(m.chat.id) == 'awaiting_url')
def handle_url(message):
    chat_id = message.chat.id
    url = message.text.strip()
    if not url.startswith(('http://', 'https://')):
        bot.send_message(chat_id, "⚠️ Invalid URL. Must start with http:// or https://")
        return
    user_data[chat_id]['temp_url'] = url
    user_states[chat_id] = 'awaiting_filename'
    bot.send_message(chat_id, "✏️ What name do you want to give this file?")

@bot.message_handler(func=lambda m: user_states.get(m.chat.id) == 'awaiting_filename')
def handle_filename(message):
    chat_id = message.chat.id
    file_name = message.text.strip()
    if not file_name:
        bot.send_message(chat_id, "⚠️ Name cannot be empty.")
        return
    url = user_data[chat_id].pop('temp_url', None)
    if not url:
        bot.send_message(chat_id, "⚠️ No URL found.")
        send_main_menu(chat_id)
        return
    user_data[chat_id]['links'][file_name] = url
    bot.send_message(chat_id, f"✅ Link saved as `{file_name}`", parse_mode="Markdown")
    send_main_menu(chat_id)

# --- Search One File ---
def choose_file_for_search(chat_id):
    markup = types.InlineKeyboardMarkup()
    for fname in user_data[chat_id]['links'].keys():
        markup.add(types.InlineKeyboardButton(f"🔍 {fname}", callback_data=f"search_file:{fname}"))
    bot.send_message(chat_id, "Select a link to search:", reply_markup=markup)

@bot.message_handler(func=lambda m: user_states.get(m.chat.id, "").startswith('awaiting_domain:'))
def handle_domain_and_search(message):
    chat_id = message.chat.id
    state = user_states[chat_id]
    fname = state.split("awaiting_domain:")[1]
    url = user_data[chat_id]['links'].get(fname)
    if not url:
        bot.send_message(chat_id, "⚠️ Link not found.")
        send_main_menu(chat_id)
        return
    target_domain = message.text.strip()
    stream_search_with_live_progress(chat_id, url, target_domain, fname)

# --- Search All Files ---
@bot.message_handler(func=lambda m: user_states.get(m.chat.id) == "awaiting_domain_all")
def handle_search_all(message):
    chat_id = message.chat.id
    target_domain = message.text.strip()
    links = user_data.get(chat_id, {}).get('links', {})
    if not links:
        bot.send_message(chat_id, "⚠️ No files to search.")
        send_main_menu(chat_id)
        return

    bot.send_message(chat_id, f"🔎 Searching for `{target_domain}` across {len(links)} files...", parse_mode="Markdown")
    found_lines_stream = io.BytesIO()
    total_matches = 0
    pattern = re.compile(r'\b' + re.escape(target_domain) + r'\b', re.IGNORECASE)

    for fname, url in links.items():
        try:
            response = requests.get(url, stream=True, timeout=(10, 60))
            response.raise_for_status()
            for line in response.iter_lines(decode_unicode=True):
                if line and pattern.search(line):
                    found_lines_stream.write(f"[{fname}] {line}\n".encode("utf-8"))
                    total_matches += 1
        except Exception as e:
            bot.send_message(chat_id, f"⚠️ Error searching `{fname}`: {e}")

    if total_matches > 0:
        found_lines_stream.seek(0)
        bot.send_document(
            chat_id,
            found_lines_stream,
            visible_file_name=f"search_all_{target_domain}.txt",
            caption=f"✅ Found {total_matches} matches for `{target_domain}` across all files",
            parse_mode="Markdown"
        )
    else:
        bot.send_message(chat_id, f"❌ No results for `{target_domain}` in any file.", parse_mode="Markdown")

    send_main_menu(chat_id)

# --- Streaming Search with Progress ---
def stream_search_with_live_progress(chat_id, url, target_domain, fname):
    try:
        progress_msg = bot.send_message(chat_id, "⏳ Starting search...")
        response = requests.get(url, stream=True, timeout=(10, 60))
        response.raise_for_status()
        total_bytes = int(response.headers.get('Content-Length', 0))
        bytes_read = 0
        found_lines_count = 0
        pattern = re.compile(r'\b' + re.escape(target_domain) + r'\b', re.IGNORECASE)
        last_percent = 0

        for chunk in response.iter_lines(decode_unicode=True):
            if not chunk:
                continue
            lines_processed += 1
            bytes_read += len(chunk.encode('utf-8')) + 1

            if pattern.search(chunk):
                found_lines_stream.write((chunk + "\n").encode("utf-8"))
                found_lines_count += 1

            if total_bytes > 0:
                percent = int((bytes_read / total_bytes) * 100)
                if percent >= last_percent + 5:
                    bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=progress_msg.message_id,
                        text=f"📊 {percent}% done — found {found_lines_count}"
                    )
                    last_percent = percent
            else:
                if lines_processed % 5000 == 0:
                    bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=progress_msg.message_id,
                        text=f"📊 Processed {lines_processed:,} lines — found {found_lines_count}"
                    )

        # Final update
        bot.edit_message_text(
            chat_id=chat_id,
            message_id=progress_msg.message_id,
            text=f"✅ Search complete — found {found_lines_count} matches"
        )

        if found_lines_count > 0:
            found_lines_stream.seek(0)
            bot.send_document(
                chat_id,
                found_lines_stream,
                visible_file_name=f"search_results_{target_domain}.txt",
                caption=f"✅ Found {found_lines_count} matches for `{target_domain}` in `{fname}`",
                parse_mode="Markdown"
            )
        else:
            bot.send_message(chat_id, f"❌ No results for `{target_domain}` in `{fname}`", parse_mode="Markdown")

    except Exception as e:
        bot.send_message(chat_id, f"⚠️ Error: {e}")

    finally:
        send_main_menu(chat_id)

# --- Run Flask + Bot ---
if __name__ == '__main__':
    print("🤖 Bot is running with Flask health check...")
    threading.Thread(target=run_flask).start()
    bot.polling(none_stop=True)
