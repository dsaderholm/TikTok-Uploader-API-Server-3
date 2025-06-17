from fastapi import FastAPI, UploadFile, File, HTTPException, Form
from typing import Optional
import os
import shutil
import tempfile
from tiktok_uploader.upload import upload_video
from selenium.webdriver.chrome.options import Options
import logging
import asyncio
import uuid
import threading

app = FastAPI(title="TikTok Uploader API v3")

# Global lock to prevent concurrent uploads
upload_lock = threading.Lock()
upload_in_progress = False

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

COOKIE_DIR = "/app/cookies"
CHROME_TMP_DIR = "/tmp/chrome-data"

# Ensure Chrome temporary directory exists
os.makedirs(CHROME_TMP_DIR, exist_ok=True)

def create_chrome_options(user_data_dir: str) -> Options:
    """Create Chrome options with necessary settings"""
    options = Options()
    options.add_argument('--no-sandbox')
    options.add_argument('--headless=new')  # New headless mode
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--disable-gpu')
    options.add_argument(f'--user-data-dir={user_data_dir}')
    options.add_argument('--window-size=1920,1080')
    options.add_argument('--disable-blink-features=AutomationControlled')
    options.add_experimental_option('excludeSwitches', ['enable-automation'])
    options.add_experimental_option('useAutomationExtension', False)
    return options

async def run_upload_in_thread(
    filename: str,
    description: str,
    accountname: str,
    schedule: Optional[str] = None,
    headless: Optional[bool] = True,
):
    logger.info(f"Starting upload for account: {accountname}")
    
    # Create a unique temporary directory for this session
    session_id = str(uuid.uuid4())
    chrome_user_dir = os.path.join(CHROME_TMP_DIR, session_id)
    os.makedirs(chrome_user_dir, exist_ok=True)
    
    # Use description directly
    final_description = description
    cookie_file = os.path.join(COOKIE_DIR, f'{accountname}.txt')
    
    try:
        logger.debug(f"Using cookies from: {cookie_file}")
        logger.debug(f"Using Chrome user directory: {chrome_user_dir}")
        
        # Create Chrome options
        chrome_options = create_chrome_options(chrome_user_dir)
        
        result = await asyncio.to_thread(
            upload_video,
            filename,
            description=final_description,
            cookies=cookie_file,
            options=chrome_options,  # Pass the Chrome options directly
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
    finally:
        # Clean up the temporary Chrome directory
        try:
            if os.path.exists(chrome_user_dir):
                shutil.rmtree(chrome_user_dir)
        except Exception as e:
            logger.error(f"Failed to cleanup Chrome directory: {str(e)}")

@app.post("/upload")
async def upload_video_endpoint(
    video: UploadFile = File(...),
    description: str = Form(...),
    accountname: str = Form(...),
    schedule: Optional[str] = Form(None),
    headless: Optional[bool] = Form(True),
):
    global upload_in_progress
    
    # Check if upload is already in progress
    with upload_lock:
        if upload_in_progress:
            logger.warning("Upload already in progress, rejecting request")
            raise HTTPException(status_code=429, detail="Upload already in progress. Please wait.")
        upload_in_progress = True
    
    temp_files = []

    try:
        logger.info(f"Received upload request for account: {accountname}")
        
        cookie_file = os.path.join(COOKIE_DIR, f'{accountname}.txt')
        if not os.path.exists(cookie_file):
            raise HTTPException(status_code=400, detail=f"Cookie file not found for account {accountname}")
            
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
        
        # Use the original video without audio processing
        final_video_path = temp_video_path
        
        try:
            await run_upload_in_thread(
                filename=final_video_path,
                description=description,
                accountname=accountname,
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
        # Always reset the upload flag
        with upload_lock:
            upload_in_progress = False
            
        for temp_file in temp_files:
            if temp_file and os.path.exists(temp_file):
                try:
                    os.unlink(temp_file)
                except Exception as cleanup_error:
                    logger.error(f"Cleanup failed for {temp_file}: {str(cleanup_error)}")

@app.get("/health")
async def health_check():
    return {"status": "healthy"}

@app.get("/status")
async def get_status():
    """Get current upload status"""
    return {
        "upload_in_progress": upload_in_progress,
        "service": "TikTok Uploader API v3"
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8048)
