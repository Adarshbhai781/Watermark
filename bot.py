import os
import time
import json
import random
import asyncio
import aiohttp
from hachoir.metadata import extractMetadata
from hachoir.parser import createParser
from PIL import Image
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from pyrogram.errors import FloodWait, UserNotParticipant, MessageNotModified

# Local imports
from core.ffmpeg import vidmark
from core.clean import delete_all, delete_trash
from config import config
from core.handlers.main_db_handler import db
from core.display_progress import progress_for_pyrogram, humanbytes
from core.handlers.force_sub_handler import handle_force_subscribe
from core.handlers.upload_video_handler import send_video_handler
from core.handlers.broadcast_handlers import broadcast_handler

# Constants
WATERMARK_POSITIONS = {
    "5:5": "Top Left",
    "main_w-overlay_w-5:5": "Top Right",
    "5:main_h-overlay_h": "Bottom Left",
    "main_w-overlay_w-5:main_h-overlay_h-5": "Bottom Right"
}

WATERMARK_SIZES = {
    "5": "5%",
    "7": "7%",
    "10": "10%",
    "15": "15%",
    "20": "20%",
    "25": "25%",
    "30": "30%",
    "35": "35%",
    "40": "40%",
    "45": "45%"
}

DEFAULT_POSITION = "5:5"
DEFAULT_SIZE = "7"

app = Client(
    name=":watermark:",
    api_id=config.API_ID,
    api_hash=config.API_HASH,
    bot_token=config.BOT_TOKEN
)

async def check_user(bot, cmd):
    """Check if user exists and force subscription if required."""
    if not await db.is_user_exist(cmd.from_user.id):
        await db.add_user(cmd.from_user.id)
    
    if config.UPDATES_CHANNEL:
        fsub = await handle_force_subscribe(bot, cmd)
        if fsub == 400:
            return False
    return True

async def get_watermark_settings(user_id):
    """Get current watermark settings with proper tags."""
    position = await db.get_position(user_id) or DEFAULT_POSITION
    size = await db.get_size(user_id) or DEFAULT_SIZE
    
    position_tag = WATERMARK_POSITIONS.get(position, "Top Left")
    size_tag = WATERMARK_SIZES.get(size, "7%")
    
    return position, position_tag, size, size_tag

async def generate_thumbnail(video_path, output_path, duration, width, height):
    """Generate thumbnail from video."""
    try:
        ttl = random.randint(0, int(duration) - 1)
        cmd = [
            "ffmpeg",
            "-ss", str(ttl),
            "-i", video_path,
            "-vframes", "1",
            output_path
        ]
        process = await asyncio.create_subprocess_exec(*cmd)
        await process.communicate()
        
        img = Image.open(output_path).convert("RGB")
        img.resize((width, height))
        img.save(output_path, "JPEG")
        return True
    except Exception as e:
        print(f"Thumbnail generation error: {e}")
        return False

@app.on_message(filters.command(["start"]) & filters.private)
async def start_handler(bot, cmd):
    if not await check_user(bot, cmd):
        return
    
    await cmd.reply_text(
        text=config.USAGE_WATERMARK_ADDER,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("Developer", url="https://t.me/anondeveloper"),
            InlineKeyboardButton("Support Group", url="https://t.me/DevsZone")],
            [InlineKeyboardButton("Bots Channel", url="https://t.me/Discovery_Updates")],
            [InlineKeyboardButton("Source Code", url="https://github.com/AbirHasan2005/Watermark-Bot")]
        ]),
        disable_web_page_preview=True
    )

@app.on_message(filters.command(["reset"]) & filters.private)
async def reset_handler(bot, update):
    await db.delete_user(update.from_user.id)
    await db.add_user(update.from_user.id)
    await update.reply_text("Settings reset successfully")

@app.on_message(filters.command("settings") & filters.private)
async def settings_handler(bot, cmd):
    if not await check_user(bot, cmd):
        return
    
    _, position_tag, _, size_tag = await get_watermark_settings(cmd.from_user.id)
    
    await cmd.reply_text(
        text="Here you can set your Watermark Settings:",
        disable_web_page_preview=True,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(f"Watermark Position - {position_tag}", callback_data="lol")],
            [
                InlineKeyboardButton("Set Top Left", callback_data="position_5:5"),
                InlineKeyboardButton("Set Top Right", callback_data="position_main_w-overlay_w-5:5")
            ],
            [
                InlineKeyboardButton("Set Bottom Left", callback_data="position_5:main_h-overlay_h"),
                InlineKeyboardButton("Set Bottom Right", callback_data="position_main_w-overlay_w-5:main_h-overlay_h-5")
            ],
            [InlineKeyboardButton(f"Watermark Size - {size_tag}", callback_data="lel")],
            [
                InlineKeyboardButton("5%", callback_data="size_5"),
                InlineKeyboardButton("7%", callback_data="size_7"),
                InlineKeyboardButton("10%", callback_data="size_10"),
                InlineKeyboardButton("15%", callback_data="size_15"),
                InlineKeyboardButton("20%", callback_data="size_20")
            ],
            [
                InlineKeyboardButton("25%", callback_data="size_25"),
                InlineKeyboardButton("30%", callback_data="size_30"),
                InlineKeyboardButton("35%", callback_data="size_35"),
                InlineKeyboardButton("40%", callback_data="size_40"),
                InlineKeyboardButton("45%", callback_data="size_45")
            ],
            [InlineKeyboardButton("Reset Settings To Default", callback_data="reset")]
        ])
    )

@app.on_message((filters.document | filters.video | filters.photo) & filters.private)
async def media_handler(bot, cmd):
    if not await check_user(bot, cmd):
        return
    
    # Handle image watermark
    if cmd.photo or (cmd.document and cmd.document.mime_type.startswith("image/")):
        editable = await cmd.reply_text("Downloading Image...")
        watermark_path = os.path.join(config.DOWN_PATH, str(cmd.from_user.id), "thumb.jpg")
        
        try:
            await bot.download_media(
                message=cmd,
                file_name=watermark_path
            )
            await editable.edit("This image will be used as watermark!\n\nNow send any video to add watermark.")
        except Exception as e:
            await editable.edit(f"Failed to download image: {e}")
        return
    
    # Handle video processing
    working_dir = os.path.join(config.DOWN_PATH, "WatermarkAdder")
    os.makedirs(working_dir, exist_ok=True)
    
    watermark_path = os.path.join(config.DOWN_PATH, str(cmd.from_user.id), "thumb.jpg")
    if not os.path.exists(watermark_path):
        await cmd.reply_text("You haven't set a watermark yet!\n\nPlease send a JPG/PNG image first.")
        return
    
    status_file = os.path.join(working_dir, "status.json")
    if os.path.exists(status_file):
        await cmd.reply_text("I'm busy with another task right now. Please try again later!")
        return
    
    # Download video
    editable = await cmd.reply_text("Downloading Video...")
    with open(status_file, "w") as f:
        json.dump({
            'chat_id': cmd.from_user.id,
            'message': editable.message_id
        }, f)
    
    dl_loc = os.path.join(working_dir, str(cmd.from_user.id))
    os.makedirs(dl_loc, exist_ok=True)
    
    try:
        c_time = time.time()
        the_media = await bot.download_media(
            message=cmd,
            file_name=dl_loc,
            progress=progress_for_pyrogram,
            progress_args=(
                "Downloading...",
                editable,
                None,
                c_time
            )
        )
        
        if not the_media:
            raise Exception("Download failed")
        
        # Process video
        position, _, size, _ = await get_watermark_settings(cmd.from_user.id)
        
        metadata = extractMetadata(createParser(the_media))
        duration = metadata.get('duration').seconds if metadata.has("duration") else 0
        
        output_name = f"{os.path.splitext(os.path.basename(the_media))[0]}_watermarked.mp4"
        progress_file = os.path.join(dl_loc, "progress.txt")
        
        output_vid = await vidmark(
            input_path=the_media,
            message=editable,
            progress_file=progress_file,
            watermark_path=watermark_path,
            output_name=output_name,
            duration=duration,
            log_message=None,
            status_file=status_file,
            preset=config.PRESET,
            position=position,
            size=size
        )
        
        if not output_vid:
            raise Exception("Watermarking failed")
        
        # Prepare for upload
        await editable.edit("Watermark added successfully!\n\nPreparing to upload...")
        
        metadata = extractMetadata(createParser(output_vid))
        duration = metadata.get('duration').seconds if metadata.has("duration") else 0
        width = metadata.get("width") if metadata.has("width") else 1280
        height = metadata.get("height") if metadata.has("height") else 720
        
        thumbnail_path = os.path.join(dl_loc, f"thumb_{time.time()}.jpg")
        await generate_thumbnail(output_vid, thumbnail_path, duration, width, height)
        
        # Upload video
        file_size = os.path.getsize(output_vid)
        
        if file_size > 2097152000 and config.ALLOW_UPLOAD_TO_STREAMTAPE:
            # Streamtape upload logic here
            pass
        else:
            await send_video_handler(
                bot=bot,
                message=cmd,
                video_path=output_vid,
                thumbnail_path=thumbnail_path,
                duration=duration,
                width=width,
                height=height,
                progress_message=editable,
                log_message=None,
                file_size=file_size
            )
        
    except Exception as e:
        await editable.edit(f"Error: {str(e)}")
        if os.path.exists(status_file):
            os.remove(status_file)
    finally:
        await delete_all()

# Add other handlers (cancel, broadcast, status, callback_query) following the same improved pattern

if __name__ == "__main__":
    print("Bot started successfully!")
    app.run()
