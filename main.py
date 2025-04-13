import os
import time
import mimetypes
import asyncio
import logging
import requests
from pyrogram import Client, filters
from pyrogram.types import Message
from yt_dlp import YoutubeDL
import subprocess

API_ID = 15523035  # replace with your API ID
API_HASH = "33a37e968712427c2e7971cb03f341b3"
BOT_TOKEN = "2049170894:AAEtQ6CFBPqhR4api99FqmO56xArWcE0H-o"
DOWNLOAD_DIR = "downloads"
COOKIES_FILE = "cookies.txt"

os.makedirs(DOWNLOAD_DIR, exist_ok=True)
logging.basicConfig(level=logging.INFO)
bot = Client("downloader_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)


def get_filename_from_headers_or_url(url):
    try:
        response = requests.head(url, allow_redirects=True, timeout=10)
        if "content-disposition" in response.headers:
            fname = response.headers["content-disposition"].split("filename=")[-1].strip("\"'")
        else:
            fname = url.split("/")[-1].split("?")[0]
        ext = mimetypes.guess_extension(response.headers.get("content-type", "").split(";")[0].strip()) or ""
        return fname if fname.endswith(ext) else fname + ext
    except Exception:
        return url.split("/")[-1].split("?")[0]


def download_file(url, filepath, msg=None):
    try:
        with requests.get(url, stream=True, timeout=(10, 30)) as r:
            r.raise_for_status()
            total = int(r.headers.get('content-length', 0))
            with open(filepath, "wb") as f:
                downloaded = 0
                start = time.time()
                for chunk in r.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        if msg and total:
                            percent = downloaded * 100 / total
                            speed = downloaded / (time.time() - start + 0.1)
                            await_msg = f"{percent:.2f}% | {downloaded/1024**2:.2f}MB | {speed/1024:.2f}KB/s"
                            try:
                                asyncio.create_task(msg.edit(await_msg))
                            except: pass
        return True
    except Exception:
        return False


def get_video_metadata(filepath):
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
             "default=noprint_wrappers=1:nokey=1", filepath],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True
        )
        duration = float(result.stdout.strip())
        return int(duration)
    except:
        return None


async def upload_to_telegram(filepath, message: Message):
    caption = os.path.basename(filepath)
    duration = get_video_metadata(filepath) if filepath.lower().endswith(('.mp4', '.mkv')) else None
    try:
        if filepath.lower().endswith(('.mp4', '.mkv')):
            await message.reply_video(video=filepath, caption=caption, duration=duration)
        else:
            await message.reply_document(document=filepath, caption=caption)
    except Exception as e:
        await message.reply_text(f"Failed to upload: {e}")


@bot.on_message(filters.command("start"))
async def start_handler(client, message: Message):
    await message.reply_text("Send a URL with /download or a .txt with /batch.")


@bot.on_message(filters.command("download") & filters.private)
async def handle_download(client, message: Message):
    if len(message.command) < 2:
        await message.reply_text("Usage: /download URL or /download filename|URL")
        return

    input_text = message.text.split(" ", 1)[1]
    if "|" in input_text:
        custom_name, url = map(str.strip, input_text.split("|", 1))
    else:
        url = input_text.strip()
        custom_name = get_filename_from_headers_or_url(url)

    filepath = os.path.join(DOWNLOAD_DIR, custom_name)
    msg = await message.reply_text(f"Downloading file: {custom_name}")
    for attempt in range(3):
        success = await asyncio.to_thread(download_file, url, filepath, msg)
        if success:
            break
        await msg.edit(f"Retrying... ({attempt + 1}/3)")
        await asyncio.sleep(3)
    else:
        await msg.edit("Failed: Download failed")
        return

    await msg.edit("Download complete.")
    await upload_to_telegram(filepath, message)
    os.remove(filepath)


@bot.on_message(filters.command("batch") & filters.private)
async def handle_batch(client, message: Message):
    if not message.reply_to_message or not message.reply_to_message.document:
        await message.reply_text("Please reply to a `.txt` file with `/batch`.")
        return

    file = await message.reply_to_message.download(file_name="batch_urls.txt")
    await message.reply_text("Processing links...")

    with open(file, "r") as f:
        lines = [line.strip() for line in f if line.strip()]

    for line in lines:
        if "|" in line:
            custom_name, url = map(str.strip, line.split("|", 1))
        else:
            url = line.strip()
            custom_name = get_filename_from_headers_or_url(url)
        filepath = os.path.join(DOWNLOAD_DIR, custom_name)
        temp_msg = await message.reply_text(f"Downloading: {custom_name}")
        for attempt in range(3):
            success = await asyncio.to_thread(download_file, url, filepath, temp_msg)
            if success:
                break
            await temp_msg.edit(f"Retrying... ({attempt + 1}/3)")
            await asyncio.sleep(3)
        else:
            await temp_msg.edit("Failed: Download failed")
            continue

        await upload_to_telegram(filepath, message)
        os.remove(filepath)

    await message.reply_text("Upload complete!")


@bot.on_message(filters.document & filters.private & filters.caption == "cookies.txt")
async def save_cookies_file(client, message: Message):
    file = await message.download(file_name=COOKIES_FILE)
    await message.reply_text("Cookies saved!")


@bot.on_message(filters.command("delete_cookies"))
async def delete_cookies(_, message: Message):
    if os.path.exists(COOKIES_FILE):
        os.remove(COOKIES_FILE)
        await message.reply_text("Cookies deleted.")
    else:
        await message.reply_text("No cookies file found.")


def yt_download(url, custom_name):
    ydl_opts = {
        'outtmpl': os.path.join(DOWNLOAD_DIR, custom_name),
        'format': 'bestvideo+bestaudio/best',
    }
    if os.path.exists(COOKIES_FILE):
        ydl_opts['cookiefile'] = COOKIES_FILE
    with YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])


@bot.on_message(filters.command("yt"))
async def youtube_download(client, message: Message):
    if len(message.command) < 2:
        await message.reply_text("Usage: /yt URL")
        return

    url = message.text.split(" ", 1)[1].strip()
    filename = "yt_video.%(ext)s"
    msg = await message.reply_text("Downloading YouTube video...")

    try:
        await asyncio.to_thread(yt_download, url, filename)
        files = [os.path.join(DOWNLOAD_DIR, f) for f in os.listdir(DOWNLOAD_DIR)]
        for file in files:
            await upload_to_telegram(file, message)
            os.remove(file)
        await msg.edit("Upload complete!")
    except Exception as e:
        await msg.edit(f"Failed: {e}")


bot.run()
                                              
