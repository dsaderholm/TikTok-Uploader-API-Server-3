from fastapi import FastAPI, UploadFile, File, HTTPException, Form
from fastapi.responses import JSONResponse
from typing import List, Optional
import os
import shutil
import tempfile
from tiktok_uploader.upload import upload_video
import logging
import asyncio
import json
from audio_processor import AudioProcessor
import uuid

app = FastAPI(title="TikTok Uploader API v3")

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

COOKIE_DIR = "/app/cookies"
SOUNDS_DIR = "/app/sounds"

def process_hashtags(hashtags: str) -> str:
    if not hashtags:
        return ""
    tags = hashtags.split(',')
    processed_tags = ' '.join([f'#{tag.lstrip("#").strip()}' for tag in tags if tag.strip()])
    return processed_tags

def clean_string(s):
    if isinstance(s, str):
        s = s.strip("'\"")
        s = s.replace('{', '').replace('}', '')
    return s

async def run_upload_in_thread(
    filename: str,
    description: str,
    accountname: str,
    hashtags: Optional[str] = None,
    sound_name: Optional[str] = None,
    sound_aud_vol: Optional[str] = 'mix',
    schedule: Optional[str] = None,
    headless: Optional[bool] = True,
):
    logger.info(f"Starting upload for account: {accountname}")
    
    description_with_tags = f"{description} {process_hashtags(hashtags)}" if hashtags else description
    cookie_file = os.path.join(COOKIE_DIR, f'{accountname}.txt')
    
    try:
        logger.debug(f"Using cookies from: {cookie_file}")
        
        # Use basic parameters, passing cookies file directly
        result = await asyncio.to_thread(
            upload_video,
            filename,
            description=description_with_tags,
            cookies=cookie_file,  # Pass cookie file path directly
            headless=headless,
            browser='chrome'
        )
        
        if result:
            logger.error('Error while uploading video')
            raise Exception('Error while uploading video')
        else:
            logger.info('Video uploaded successfully')
            
        return result
    except Exception as e:
        logger.error(f"Upload failed with error: {str(e)}")
        raise

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
    temp_files = []

    try:
        logger.info(f"Received upload request for account: {accountname}")
        
        description = clean_string(description)
        accountname = clean_string(accountname)
        hashtags = clean_string(hashtags) if hashtags else None
        sound_name = clean_string(sound_name) if sound_name else None
        sound_aud_vol = clean_string(sound_aud_vol) if sound_aud_vol else 'mix'
        
        cookie_file = os.path.join(COOKIE_DIR, f'{accountname}.txt')
        if not os.path.exists(cookie_file):
            raise HTTPException(status_code=400, detail=f"Cookie file not found for account {accountname}")
            
        # Verify cookie file is not empty and contains sessionid
        try:
            with open(cookie_file, 'r') as f:
                cookie_content = f.read()
                if not cookie_content.strip():
                    raise HTTPException(status_code=400, detail=f"Cookie file is empty for account {accountname}")
                if 'sessionid' not in cookie_content:
                    raise HTTPException(status_code=400, detail=f"Cookie file does not contain sessionid for account {accountname}")
        except Exception as e:
            logger.error(f"Error reading cookie file: {str(e)}")
            raise HTTPException(status_code=400, detail=f"Error reading cookie file for account {accountname}")
        
        with tempfile.NamedTemporaryFile(delete=False, suffix='.mp4') as temp_video:
            shutil.copyfileobj(video.file, temp_video)
            temp_video_path = temp_video.name
            temp_files.append(temp_video_path)
        
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
            await run_upload_in_thread(
                filename=final_video_path,
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