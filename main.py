import os
import re
import urllib.parse
import requests
import subprocess
import asyncio
import pickle
from telethon import TelegramClient, events
from yt_dlp import YoutubeDL
from datetime import datetime
import traceback
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

# === CONFIG ===
API_ID = 15523035
API_HASH = '33a37e968712427c2e7971cb03f341b3'
BOT_TOKEN = '2049170894:AAEtQ6CFBPqhR4api99FqmO56xArWcE0H-o'

# === Google Drive Upload ===
def upload_to_drive(filepath):
    SCOPES = ['https://www.googleapis.com/auth/drive.file']
    creds = None

    if os.path.exists('token.pickle'):
        with open('token.pickle', 'rb') as token:
            creds = pickle.load(token)
    else:
        raise Exception("token.pickle not found. Send it using /token command.")

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            raise Exception("token.pickle is invalid or expired.")

    service = build('drive', 'v3', credentials=creds)
    file_metadata = {'name': os.path.basename(filepath)}
    media = MediaFileUpload(filepath, resumable=True)
    file = service.files().create(body=file_metadata, media_body=media, fields='id').execute()

    service.permissions().create(fileId=file['id'], body={'role': 'reader', 'type': 'anyone'}).execute()
    return f"https://drive.google.com/file/d/{file['id']}/view"

# === Safe message edit ===
async def safe_edit(msg, new_text):
    if msg.text != new_text:
        await msg.edit(new_text)

# === Get filename from URL or Content-Disposition ===
def get_filename(url):
    try:
        resp = requests.head(url, allow_redirects=True, timeout=10)
        cd = resp.headers.get("Content-Disposition", "")
        if 'filename=' in cd:
            fname = re.findall('filename="?([^\";]+)', cd)
            if fname:
                return fname[0]
        return os.path.basename(urllib.parse.urlparse(url).path)
    except:
        return os.path.basename(urllib.parse.urlparse(url).path)

def download_file(url, filepath, msg):
    try:
        with requests.get(url, stream=True, timeout=30) as r:
            r.raise_for_status()
            with open(filepath, 'wb') as f:
                for chunk in r.iter_content(1024 * 512):
                    if chunk:
                        f.write(chunk)
        return filepath
    except Exception:
        return None

def download_ytdl(url, custom_name=None):
    name_template = f"/tmp/{custom_name}.%(ext)s" if custom_name else '/tmp/%(title)s.%(ext)s'
    ydl_opts = {
        'format': 'bestvideo+bestaudio/best',
        'outtmpl': name_template,
        'noplaylist': True,
        'quiet': True,
        'no_warnings': True,
    }
    with YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        return ydl.prepare_filename(info)

def generate_thumbnail(video_path):
    thumb_path = video_path + "_thumb.jpg"
    try:
        subprocess.run([
            'ffmpeg', '-y', '-i', video_path, '-ss', '00:00:01.000',
            '-vframes', '1', thumb_path
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return thumb_path if os.path.exists(thumb_path) else None
    except:
        return None

async def process_link(client, url, msg, chat_id, custom_name=None, suppress_success=False, force_ytdl=False):
    try:
        if force_ytdl or any(x in url for x in ['youtu', 'vimeo', 'dailymotion']):
            await safe_edit(msg, 'Downloading via yt-dlp...')
            filepath = download_ytdl(url, custom_name)
        else:
            fname = custom_name if custom_name else get_filename(url)
            filepath = os.path.join('/tmp', fname)
            await safe_edit(msg, f'Downloading file: {os.path.basename(filepath)}')
            for attempt in range(3):
                result = download_file(url, filepath, msg)
                if result:
                    break
                await safe_edit(msg, f"Retrying... ({attempt + 1}/3). Waiting 10 seconds...")
                await asyncio.sleep(10)
            else:
                raise Exception("Download failed")

        is_video = filepath.lower().endswith(('.mp4', '.mkv', '.webm', '.mov'))
        thumb_path = generate_thumbnail(filepath) if is_video else None

        file_size = os.path.getsize(filepath)
        if file_size > 2 * 1024 * 1024 * 1024:
            await safe_edit(msg, "Uploading to Google Drive (file >2GB)...")
            drive_link = upload_to_drive(filepath)
            await safe_edit(msg, f"File uploaded to Google Drive:\n{drive_link}")
        else:
            await safe_edit(msg, "Uploading to Telegram...")
            await client.send_file(
                chat_id,
                filepath,
                caption=os.path.basename(filepath),
                thumb=thumb_path if thumb_path else None,
                supports_streaming=is_video and not filepath.endswith('.webm')
            )
            if not suppress_success:
                await safe_edit(msg, "Upload complete!")

        if thumb_path and os.path.exists(thumb_path):
            os.remove(thumb_path)
        os.remove(filepath)
        return True
    except Exception as e:
        await safe_edit(msg, f"Failed: {e}")
        print(traceback.format_exc())
        return False

async def handle_batch(client, file_bytes, msg, chat_id):
    text = file_bytes.decode()
    lines = text.strip().splitlines()
    failed = []
    await safe_edit(msg, f"Processing {len(lines)} links...")
    for line in lines:
        if '|' in line:
            url, custom_name = map(str.strip, line.split('|', 1))
        else:
            url, custom_name = line.strip(), None
        if not url.startswith("http"):
            continue
        sub_msg = await client.send_message(chat_id, f"Starting: {url}")
        success = await process_link(client, url, sub_msg, chat_id, custom_name, suppress_success=True)
        if not success:
            failed.append(url)
    if failed:
        await client.send_message(chat_id, "Failed URLs:\n" + '\n'.join(failed))
    await client.send_message(chat_id, "Upload complete!")

# === Initialize bot ===
bot = TelegramClient('bot', API_ID, API_HASH).start(bot_token=BOT_TOKEN)

@bot.on(events.NewMessage(pattern='/start'))
async def start(event):
    await event.reply(
        "Send:\n"
        "`/download <URL | optional_name>` — Direct file download\n"
        "`/ytdl <YouTube/Vimeo URL | optional_name>` — Video sites (yt-dlp)\n"
        "`/token` with a `token.pickle` file to set up Google Drive upload\n"
        "Or reply to a `.txt` file and send `/batch`.",
        parse_mode='md'
    )

@bot.on(events.NewMessage(pattern='/token'))
async def receive_token(event):
    if not event.is_reply:
        return await event.reply("Reply to your `token.pickle` file with `/token`.")

    replied = await event.get_reply_message()

    if not replied.document:
        return await event.reply("That doesn't look like a valid `token.pickle` file.")

    file_name = replied.file.name if replied.file else None
    if not file_name or not file_name.endswith('.pickle'):
        return await event.reply("That doesn't look like a valid `token.pickle` file.")

    temp_path = await bot.download_media(replied.document)
    if not temp_path:
        return await event.reply("❌ Failed to download the file.")

    try:
        os.rename(temp_path, "token.pickle")
    except Exception as e:
        return await event.reply(f"❌ Error renaming: {e}")

    return await event.reply("✅ token.pickle received and saved!")

@bot.on(events.NewMessage(pattern='/ytdl$'))
async def empty_ytdl(event):
    await event.reply("Usage:\n`/ytdl <YouTube/Vimeo URL | optional_name>`", parse_mode='md')

@bot.on(events.NewMessage(pattern='/download$'))
async def empty_download(event):
    await event.reply("Usage:\n`/download <Direct URL | optional_name>`", parse_mode='md')

@bot.on(events.NewMessage(pattern='/ytdl (.+)'))
async def yt_download(event):
    raw = event.pattern_match.group(1).strip()
    if '|' in raw:
        url, custom_name = map(str.strip, raw.split('|', 1))
    else:
        url, custom_name = raw, None
    if not url.startswith("http"):
        return await event.reply("Invalid URL")
    msg = await event.reply("Processing YouTube/Vimeo link...")
    await process_link(bot, url, msg, event.chat_id, custom_name, force_ytdl=True)

@bot.on(events.NewMessage(pattern='/download (.+)'))
async def single_download(event):
    raw = event.pattern_match.group(1).strip()
    if '|' in raw:
        url, custom_name = map(str.strip, raw.split('|', 1))
    else:
        url, custom_name = raw, None
    if not url.startswith("http"):
        return await event.reply("Invalid URL")
    msg = await event.reply("Processing link...")
    await process_link(bot, url, msg, event.chat_id, custom_name)

@bot.on(events.NewMessage(pattern='/batch'))
async def batch_handler(event):
    if not event.is_reply:
        return await event.reply("Please reply to a `.txt` file with `/batch`.")
    replied = await event.get_reply_message()
    if not replied or not replied.document:
        return await event.reply("Please reply to a `.txt` file with `/batch`.")
    if replied.document.mime_type != "text/plain":
        return await event.reply("Only `.txt` files are supported.")
    file_path = await bot.download_media(replied.document, file='/tmp/links.txt')
    with open(file_path, 'rb') as f:
        content = f.read()
    msg = await event.reply("Starting batch download...")
    await handle_batch(bot, content, msg, event.chat_id)

print("Bot is running...")
bot.run_until_disconnected()
    
