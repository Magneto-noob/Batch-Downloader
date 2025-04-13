import os
import re
import time
import json
import asyncio
import urllib.parse
import subprocess
import threading
import requests
from pyrogram import Client, filters
from yt_dlp import YoutubeDL

API_ID = 12345678
API_HASH = "your_api_hash"
BOT_TOKEN = "your_bot_token"
DOWNLOAD_DIR = "downloads"
COOKIE_FILE = "cookies.txt"

app = Client("downloader_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# === Progress tracking ===
def downstatus(statusfile, message):
    while os.path.exists(statusfile):
        with open(statusfile) as f:
            txt = f.read()
        try:
            asyncio.run_coroutine_threadsafe(
                message.edit(f"__Downloaded__ : **{txt}**"), app.loop
            )
        except: pass
        time.sleep(10)

def upstatus(statusfile, message):
    while os.path.exists(statusfile):
        with open(statusfile) as f:
            txt = f.read()
        try:
            asyncio.run_coroutine_threadsafe(
                message.edit(f"__Uploaded__ : **{txt}**"), app.loop
            )
        except: pass
        time.sleep(10)

def progress(current, total, message, type):
    with open(f"{message.id}{type}status.txt", 'w') as f:
        f.write(f"{current * 100 / total:.1f}%")

def get_filename(url):
    try:
        resp = requests.head(url, allow_redirects=True, timeout=10, headers={'User-Agent': 'Mozilla/5.0'})
        cd = resp.headers.get("Content-Disposition", "")
        if 'filename=' in cd:
            fname = re.findall('filename="?([^\\\";]+)', cd)
            if fname:
                return fname[0]
        return os.path.basename(urllib.parse.urlparse(url).path)
    except:
        return os.path.basename(urllib.parse.urlparse(url).path)

def generate_thumbnail(video_path):
    thumb_path = video_path + "_thumb.jpg"
    subprocess.run(['ffmpeg', '-y', '-i', video_path, '-ss', '00:00:01.000',
                    '-vframes', '1', thumb_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return thumb_path if os.path.exists(thumb_path) else None

def get_video_metadata(path):
    try:
        result = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0',
            '-show_entries', 'stream=width,height,duration',
            '-of', 'default=noprint_wrappers=1:nokey=1', path],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        lines = result.stdout.strip().split('\n')
        return int(float(lines[2])), int(lines[0]), int(lines[1])
    except:
        return None, None, None

async def download_ytdl_interactive(client, message, url):
    try:
        ydl_opts = {'quiet': True, 'skip_download': True, 'noplaylist': True}
        if os.path.exists(COOKIE_FILE):
            ydl_opts['cookiefile'] = COOKIE_FILE

        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            formats = info.get('formats', [])

        text = "Available formats:\n"
        format_ids = []
        for f in formats:
            if f.get("format_id") and f.get("ext"):
                desc = f"{f['format_id']}: {f.get('format_note','')} {f.get('resolution','')} {f['ext']} - {f.get('filesize',0)//1048576}MB"
                format_ids.append(f['format_id'])
                text += desc + "\n"

        prompt = await message.reply(text + "\nReply with format code (e.g. 18, 137+140). Timeout in 15s...")
        try:
            reply = await app.listen(message.chat.id, filters.reply & filters.text, timeout=15)
            fmt = reply.text.strip()
            if fmt not in format_ids and '+' not in fmt:
                await reply.reply("Invalid format. Using best.")
                fmt = 'bestvideo+bestaudio/best'
        except asyncio.TimeoutError:
            await prompt.reply("Timeout. Using best format.")
            fmt = 'bestvideo+bestaudio/best'

        download_opts = {
            'format': fmt,
            'outtmpl': os.path.join(DOWNLOAD_DIR, '%(title)s.%(ext)s'),
            'noplaylist': True,
            'quiet': True
        }
        if os.path.exists(COOKIE_FILE):
            download_opts['cookiefile'] = COOKIE_FILE

        with YoutubeDL(download_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            return ydl.prepare_filename(info)
    except Exception as e:
        await message.reply(f"Download failed: {e}")
        return None

async def download_file_with_status(url, filepath, msg):
    dstatfile = f"{msg.id}downstatus.txt"
    open(dstatfile, 'w').close()
    dosta = threading.Thread(target=lambda: downstatus(dstatfile, msg), daemon=True)
    dosta.start()

    for attempt in range(3):
        try:
            with requests.get(url, stream=True, timeout=30, headers={'User-Agent': 'Mozilla/5.0'}) as r:
                r.raise_for_status()
                total = int(r.headers.get('content-length', 0))
                downloaded = 0
                with open(filepath, 'wb') as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)
                            downloaded += len(chunk)
                            percent = (downloaded / total) * 100 if total else 0
                            with open(dstatfile, 'w') as df:
                                df.write(f"{percent:.1f}%")
            break
        except:
            await msg.edit(f"Retrying... ({attempt + 1}/3)")
            await asyncio.sleep(3)
    else:
        os.remove(dstatfile)
        raise Exception("Download failed")

    os.remove(dstatfile)
    return filepath

async def upload_file(client, chat_id, filepath, caption, thumb_path=None, msg=None):
    upstatfile = f"{msg.id}upstatus.txt"
    open(upstatfile, 'w').close()
    upsta = threading.Thread(target=lambda: upstatus(upstatfile, msg), daemon=True)
    upsta.start()

    try:
        if filepath.lower().endswith(('.mp4', '.mkv', '.webm', '.mov')):
            duration, width, height = get_video_metadata(filepath)
            await client.send_video(
                chat_id, video=filepath, caption=caption,
                thumb=thumb_path if thumb_path and os.path.exists(thumb_path) else None,
                supports_streaming=True, duration=duration, width=width, height=height,
                progress=progress, progress_args=[msg, "up"]
            )
        else:
            await client.send_document(
                chat_id, document=filepath, caption=caption,
                thumb=thumb_path if thumb_path and os.path.exists(thumb_path) else None,
                progress=progress, progress_args=[msg, "up"]
            )
    finally:
        if os.path.exists(upstatfile):
            os.remove(upstatfile)

async def process_link(client, url, msg, chat_id, custom_name=None):
    try:
        if any(x in url for x in ['youtu', 'vimeo', 'dailymotion', '.m3u8']):
            filepath = await download_ytdl_interactive(client, msg, url)
        else:
            fname = custom_name if custom_name else get_filename(url)
            filepath = os.path.join(DOWNLOAD_DIR, fname)
            await msg.edit(f'Downloading: {os.path.basename(filepath)}')
            filepath = await download_file_with_status(url, filepath, msg)

        thumb_path = generate_thumbnail(filepath)
        await upload_file(client, chat_id, filepath, os.path.basename(filepath), thumb_path, msg)
        if thumb_path: os.remove(thumb_path)
        os.remove(filepath)
        await msg.edit("Upload complete!")
        return True
    except Exception as e:
        await msg.edit(f"Failed: {e}")
        return False

@app.on_message(filters.command('download'))
async def single_download(client, message):
    if len(message.command) < 2:
        return await message.reply("Usage: /download <url> or name.ext|url")
    input_line = message.text.split(' ', 1)[1].strip()
    custom_name, url = (input_line.split('|', 1) + [None])[:2] if '|' in input_line else (None, input_line)
    msg = await message.reply("Processing...")
    await process_link(client, url.strip(), msg, message.chat.id, custom_name.strip() if custom_name else None)

@app.on_message(filters.command('batch'))
async def batch_handler(client, message):
    if not message.reply_to_message or not message.reply_to_message.document:
        return await message.reply("Please reply to a `.txt` file with /batch.")
    file_path = await client.download_media(message.reply_to_message.document, file_name="/tmp/links.txt")
    with open(file_path, 'r') as f:
        lines = f.readlines()
    msg = await message.reply("Starting batch...")
    failed = []
    for line in lines:
        line = line.strip()
        if not line or line.startswith('#'): continue
        custom_name, url = (line.split('|', 1) + [None])[:2] if '|' in line else (None, line)
        sub_msg = await client.send_message(message.chat.id, f"Starting: {url}")
        success = await process_link(client, url.strip(), sub_msg, message.chat.id, custom_name.strip() if custom_name else None)
        if not success: failed.append(url)
    if failed:
        await client.send_message(message.chat.id, "Failed URLs:\n" + '\n'.join(failed))
    await msg.edit("Batch complete.")

@app.on_message(filters.document & filters.private)
async def upload_cookies(client, message):
    if message.document.file_name == "cookies.txt":
        await message.download(file_name=COOKIE_FILE)
        await message.reply("cookies.txt saved and will be used.")

@app.on_message(filters.command("delete_cookies"))
async def delete_cookies(client, message):
    if os.path.exists(COOKIE_FILE):
        os.remove(COOKIE_FILE)
        await message.reply("cookies.txt deleted.")
    else:
        await message.reply("No cookies.txt found.")

print("Bot is running...")
app.run()
                            
