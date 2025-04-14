import os
import re
import urllib.parse
import requests
import subprocess
import asyncio
from telethon import TelegramClient, events
from yt_dlp import YoutubeDL
from datetime import datetime
import traceback

# === CONFIG ===
API_ID = 15523035
API_HASH = '33a37e968712427c2e7971cb03f341b3'
BOT_TOKEN = '2049170894:AAEtQ6CFBPqhR4api99FqmO56xArWcE0H-o'

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
    if custom_name:
        name_template = f"/tmp/{custom_name}.%(ext)s"
    else:
        name_template = '/tmp/%(title)s.%(ext)s'
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

def convert_webm_to_mp4(input_path):
    if not input_path.endswith('.webm'):
        return input_path
    output_path = input_path.rsplit('.', 1)[0] + '_converted.mp4'
    try:
        subprocess.run([
            'ffmpeg', '-y', '-i', input_path,
            '-c:v', 'libx264', '-c:a', 'aac',
            output_path
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if os.path.exists(output_path):
            os.remove(input_path)
            return output_path
    except Exception as e:
        print(f"Conversion failed: {e}")
    return input_path

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

async def process_link(client, url, msg, chat_id, custom_name=None, suppress_success=False):
    try:
        fname = custom_name if custom_name else get_filename(url)
        filepath = os.path.join('/tmp', fname)
        await msg.edit(f'Downloading file: {os.path.basename(filepath)}')
        for attempt in range(3):
            result = download_file(url, filepath, msg)
            if result:
                break
            await msg.edit(f"Retrying... ({attempt + 1}/3). Waiting 10 seconds...")
            await asyncio.sleep(10)
        else:
            raise Exception("Download failed")

        is_video = filepath.lower().endswith(('.mp4', '.mkv', '.webm', '.mov'))
        thumb_path = generate_thumbnail(filepath) if is_video else None

        await msg.edit('Uploading to Telegram...')
        await client.send_file(
            chat_id,
            filepath,
            caption=os.path.basename(filepath),
            thumb=thumb_path if thumb_path else None,
            supports_streaming=True if is_video else None
        )

        if thumb_path and os.path.exists(thumb_path):
            os.remove(thumb_path)
        os.remove(filepath)
        if not suppress_success:
            await msg.edit('Upload complete!')
        return True
    except Exception as e:
        await msg.edit(f"Failed: {e}")
        print(traceback.format_exc())
        return False

async def handle_batch(client, file_bytes, msg, chat_id):
    text = file_bytes.decode()
    lines = text.strip().splitlines()
    failed = []
    await msg.edit(f"Processing {len(lines)} links...")
    for line in lines:
        if '|' in line:
            url, custom_name = map(str.strip, line.split('|', 1))
        else:
            url, custom_name = line.strip(), None
        sub_msg = await client.send_message(chat_id, f"Starting: {url}")
        success = await process_link(client, url, sub_msg, chat_id, custom_name, suppress_success=True)
        if not success:
            failed.append(url)
    if failed:
        await client.send_message(chat_id, "Failed URLs:\n" + '\n'.join(failed))
    await client.send_message(chat_id, "Upload complete!")

# === Init ===
bot = TelegramClient('bot', API_ID, API_HASH).start(bot_token=BOT_TOKEN)

@bot.on(events.NewMessage(pattern='/start'))
async def start(event):
    await event.reply(
        "Send:\n"
        "`/download <Direct URL | optional_name>` — For direct file downloads\n"
        "`/ytdl <YouTube/Vimeo URL | optional_name>` — For video platforms\n"
        "Or reply to a `.txt` file and send `/batch`.",
        parse_mode='md'
    )

@bot.on(events.NewMessage(pattern='/download$'))
async def download_empty(event):
    await event.reply("Usage:\n`/download <Direct URL | optional_name>`", parse_mode='md')

@bot.on(events.NewMessage(pattern='/download (.+)'))
async def single_download(event):
    raw = event.pattern_match.group(1).strip()
    if '|' in raw:
        url, custom_name = map(str.strip, raw.split('|', 1))
    else:
        url, custom_name = raw, None
    msg = await event.reply("Processing link...")
    await process_link(bot, url, msg, event.chat_id, custom_name)

@bot.on(events.NewMessage(pattern='/ytdl$'))
async def ytdl_empty(event):
    await event.reply("Usage:\n`/ytdl <YouTube/Vimeo URL | optional_name>`", parse_mode='md')

@bot.on(events.NewMessage(pattern='/ytdl (.+)'))
async def ytdl_download(event):
    raw = event.pattern_match.group(1).strip()
    if '|' in raw:
        url, custom_name = map(str.strip, raw.split('|', 1))
    else:
        url, custom_name = raw, None
    msg = await event.reply("Downloading via yt-dlp...")
    try:
        filepath = download_ytdl(url, custom_name)
        if filepath.endswith('.webm'):
            filepath = convert_webm_to_mp4(filepath)

        is_video = filepath.lower().endswith(('.mp4', '.mkv', '.webm', '.mov'))
        thumb_path = generate_thumbnail(filepath) if is_video else None

        await msg.edit("Uploading to Telegram...")
        await bot.send_file(
            event.chat_id,
            filepath,
            caption=os.path.basename(filepath),
            thumb=thumb_path if thumb_path else None,
            supports_streaming=True if is_video else None
        )

        if thumb_path and os.path.exists(thumb_path):
            os.remove(thumb_path)
        os.remove(filepath)
        await msg.edit("Upload complete!")
    except Exception as e:
        await msg.edit(f"Failed: {e}")
        print(traceback.format_exc())

@bot.on(events.NewMessage(pattern='/batch$'))
async def batch_empty(event):
    await event.reply("Usage:\nReply to a `.txt` file and send `/batch`.")

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
