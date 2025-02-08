from fastapi import FastAPI, UploadFile, File, HTTPException, Form
from fastapi.responses import JSONResponse
from typing import List, Optional
import os
import shutil
import tempfile
from tiktok_uploader.upload import upload_video
import logging
import asyncio
from functools import partial
import json
from audio_processor import AudioProcessor
import uuid
import time

app = FastAPI(title="TikTok Uploader API v3")

# Configure detailed logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

COOKIE_DIR = "/app/cookies"
SOUNDS_DIR = "/app/sounds"
CHROME_DATA_DIR = "/tmp/chrome-data"

# Ensure Chrome data directory exists
os.makedirs(CHROME_DATA_DIR, exist_ok=True)

def process_hashtags(hashtags: str) -> str:
    """Process hashtags string into proper format."""
    if not hashtags:
        return ""
    
    tags = hashtags.split(',')
    processed_tags = ' '.join([f'#{tag.lstrip("#").strip()}' for tag in tags if tag.strip()])
    return processed_tags

def clean_string(s):
    """Remove surrounding quotes and curly braces from string"""
    if isinstance(s, str):
        s = s.strip("'\"")
        s = s.replace('{', '').replace('}', '')
    return s

def cleanup_chrome_data():
    """Clean up old Chrome data directories."""
    try:
        if os.path.exists(CHROME_DATA_DIR):
            for item in os.listdir(CHROME_DATA_DIR):
                item_path = os.path.join(CHROME_DATA_DIR, item)
                if os.path.isdir(item_path):
                    try:
                        shutil.rmtree(item_path)
                    except Exception as e:
                        logger.error(f"Failed to remove directory {item_path}: {str(e)}")
    except Exception as e:
        logger.error(f"Error during Chrome data cleanup: {str(e)}")

async def run_upload_in_thread(
    video_path: str,
    description: str,
    accountname: str,
    hashtags: Optional[str] = None,
    sound_name: Optional[str] = None,
    sound_aud_vol: Optional[str] = 'mix',
    schedule: Optional[str] = None,
    headless: Optional[bool] = True,
):
    """Run the upload_tiktok function in a thread pool with detailed logging."""
    logger.info(f"Starting upload for account: {accountname}")
    
    # Process hashtags
    description_with_tags = f"{description} {process_hashtags(hashtags)}" if hashtags else description
    
    # Load cookies from file
    cookie_file = os.path.join(COOKIE_DIR, f'{accountname}.txt')
    
    # Create unique user data directory for this session
    timestamp = int(time.time())
    session_id = f"{uuid.uuid4()}_{timestamp}"
    session_data_dir = os.path.join(CHROME_DATA_DIR, session_id)
    
    # Clean up old directories first
    cleanup_chrome_data()
    
    # Create new directory
    os.makedirs(session_data_dir, exist_ok=True)
    
    try:
        # Configure browser settings
        browser_config = {
            'headless': headless,
            'user_data_dir': session_data_dir,
            'options': [
                '--no-sandbox',
                '--disable-dev-shm-usage',
                '--disable-gpu',
                '--disable-software-rasterizer',
                f'--user-data-dir={session_data_dir}'
            ]
        }
        
        # Run upload in thread to not block
        upload_func = partial(
            upload_video,
            video_path,
            description=description_with_tags,
            cookies=cookie_file,
            browser_config=browser_config
        )
        
        return await asyncio.to_thread(upload_func)
    except Exception as e:
        logger.error(f"Upload failed with error: {str(e)}")
        raise
    finally:
        # Cleanup Chrome user data directory
        try:
            if os.path.exists(session_data_dir):
                shutil.rmtree(session_data_dir, ignore_errors=True)
        except Exception as e:
            logger.error(f"Failed to cleanup Chrome data directory: {str(e)}")

@app.post("/upload")
async def upload_video_endpoint(
    video: UploadFile = File(...),
    description: str = Form(...),
    accountname: str = Form(...),
    hashtags: Optional[str] = Form(None),
    sound_name: Optional[str] = Form(None),
    sound_aud_vol: Optional[str] = Form('mix'),
    schedule: Optional[str] = Form(None),
    headless: Optional[bool] = Form(True),
):
    temp_files = []  # Keep track of temporary files to clean up

    try:
        logger.info(f"Received upload request for account: {accountname}")
        
        # Clean input strings
        description = clean_string(description)
        accountname = clean_string(accountname)
        hashtags = clean_string(hashtags) if hashtags else None
        sound_name = clean_string(sound_name) if sound_name else None
        sound_aud_vol = clean_string(sound_aud_vol) if sound_aud_vol else 'mix'
        
        # Validate account and cookie
        cookie_file = os.path.join(COOKIE_DIR, f'{accountname}.txt')
        if not os.path.exists(cookie_file):
            raise HTTPException(status_code=400, detail=f"Cookie file not found for account {accountname}")
        
        # Handle video file
        with tempfile.NamedTemporaryFile(delete=False, suffix='.mp4') as temp_video:
            shutil.copyfileobj(video.file, temp_video)
            temp_video_path = temp_video.name
            temp_files.append(temp_video_path)
        
        # Process audio if sound is specified
        final_video_path = temp_video_path
        if sound_name:
            processor = AudioProcessor()
            sound_path = os.path.join(SOUNDS_DIR, f'{sound_name}.mp3')
            logger.info(f"Looking for sound file at: {sound_path}")
            
            if not os.path.exists(sound_path):
                raise HTTPException(status_code=404, detail=f"Sound file not found: {sound_name}")
            
            try:
                final_video_path = processor.mix_audio(
                    temp_video_path,
                    sound_path,
                    sound_aud_vol
                )
                temp_files.append(final_video_path)
                logger.info(f"Audio processed, new video path: {final_video_path}")
            except Exception as e:
                logger.error(f"Error processing audio: {str(e)}")
                raise HTTPException(status_code=500, detail=f"Error processing audio: {str(e)}")
        
        try:
            # Attempt upload
            await run_upload_in_thread(
                video_path=final_video_path,
                description=description,
                accountname=accountname,
                hashtags=hashtags,
                sound_name=sound_name,
                sound_aud_vol=sound_aud_vol,
                schedule=schedule,
                headless=headless,
            )
            
            return {"success": True, "message": "Video uploaded successfully"}
            
        except Exception as e:
            error_msg = str(e)
            logger.error(f"Upload error: {error_msg}")
            raise HTTPException(status_code=500, detail=error_msg)

    except Exception as e:
        logger.error(f"Upload failed: {str(e)}")
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(status_code=500, detail=str(e))
        
    finally:
        # Cleanup
        for temp_file in temp_files:
            if temp_file and os.path.exists(temp_file):
                try:
                    os.unlink(temp_file)
                except Exception as cleanup_error:
                    logger.error(f"Cleanup failed for {temp_file}: {str(cleanup_error)}")

@app.get("/health")
async def health_check():
    return {"status": "healthy"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8048)