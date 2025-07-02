from flask import Flask, request, render_template, send_file
import os
import requests
from hyperbrowser import Hyperbrowser
from hyperbrowser.models import (
    ScrapeOptions,
    StartScrapeJobParams,
    CreateSessionParams,
)
from dotenv import load_dotenv
import uuid
import logging
import time
from cachetools import TTLCache

app = Flask(__name__)

# Configure logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()
api_key = os.getenv("HYPERBROWSER_API_KEY")
if not api_key:
    logger.error("HYPERBROWSER_API_KEY not found in .env file")
    raise ValueError("HYPERBROWSER_API_KEY is required")

client = Hyperbrowser(api_key=api_key)

# Initialize cache (TTL of 1 hour, max 100 items)
cache = TTLCache(maxsize=100, ttl=3600)

# Store screenshots in static/screenshots
UPLOAD_FOLDER = os.path.join('static', 'screenshots')
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)
    logger.info(f"Created directory: {UPLOAD_FOLDER}")

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# Viewport configurations for different devices
VIEWPORT_CONFIGS = {
    'desktop': {'width': 1920, 'height': 1080, 'deviceScaleFactor': 1},
    'laptop': {'width': 1366, 'height': 768, 'deviceScaleFactor': 1},
    'tablet': {'width': 768, 'height': 1024, 'deviceScaleFactor': 2},
    'mobile': {'width': 375, 'height': 667, 'deviceScaleFactor': 2}
}

def cleanup_old_screenshots(max_age_seconds=3600):
    """Remove screenshots older than max_age_seconds"""
    current_time = time.time()
    for filename in os.listdir(UPLOAD_FOLDER):
        file_path = os.path.join(UPLOAD_FOLDER, filename)
        if os.path.isfile(file_path):
            file_age = current_time - os.path.getmtime(file_path)
            if file_age > max_age_seconds:
                try:
                    os.remove(file_path)
                    logger.info(f"Removed old screenshot: {file_path}")
                except Exception as e:
                    logger.error(f"Failed to remove old screenshot {file_path}: {str(e)}")

def normalize_url(url):
    """Ensure URL has a protocol (default to https if none provided)"""
    url = url.strip()
    if not url.startswith(('http://', 'https://')):
        url = f"https://{url}"
    return url

@app.route('/', methods=['GET', 'POST'])
def index():
    screenshot_path = None
    error = None
    filename = None
    cached = False
    device_type = request.form.get('device_type', 'desktop')  # Store device_type for template
    
    # Clean up old screenshots on each request
    cleanup_old_screenshots()
    
    if request.method == 'POST':
        url = request.form.get('url')
        if not url:
            error = "URL is required"
            logger.error("No URL provided in form submission")
            return render_template('index.html',
                                 screenshot_path=screenshot_path,
                                 error=error,
                                 filename=filename,
                                 device_types=VIEWPORT_CONFIGS.keys(),
                                 cache_buster=str(uuid.uuid4()),
                                 cached=cached,
                                 device_type=device_type)

        # Normalize URL to ensure it has a protocol
        url = normalize_url(url)
        device_type = request.form.get('device_type', 'desktop')
        cache_key = f"{url}_{device_type}"
        logger.info(f"Received POST request: URL={url}, Device Type={device_type}")
        
        # Check cache first
        if cache_key in cache:
            logger.info(f"Cache hit for {cache_key}")
            screenshot_path, filename = cache[cache_key]
            cached = True
            if os.path.exists(os.path.join(app.config['UPLOAD_FOLDER'], filename)):
                return render_template('index.html', 
                                    screenshot_path=screenshot_path,
                                    error=None,
                                    filename=filename,
                                    device_types=VIEWPORT_CONFIGS.keys(),
                                    cache_buster=str(uuid.uuid4()),
                                    cached=cached,
                                    device_type=device_type)

        try:
            # Get viewport configuration
            viewport = VIEWPORT_CONFIGS.get(device_type, VIEWPORT_CONFIGS['desktop'])
            logger.debug(f"Using {device_type} viewport: {viewport['width']}x{viewport['height']}")
            
            # Start scraping with wait for full page load
            logger.info(f"Starting scrape for URL: {url}")
            scrape_result = client.scrape.start_and_wait(
                StartScrapeJobParams(
                    url=url,
                    session_options=CreateSessionParams(
                        accept_cookies=True,
                        use_stealth=True,
                        use_proxy=False,
                        solve_captchas=False,
                        wait_for_load=True,
                        wait_timeout=30000,
                    ),
                    scrape_options=ScrapeOptions(
                        formats=["screenshot"],
                        only_main_content=True,
                        exclude_tags=[],
                        include_tags=[],
                        viewport_width=viewport['width'],
                        viewport_height=viewport['height'],
                        device_scale_factor=viewport['deviceScaleFactor'],
                        wait_for_network_idle=True,
                        delay_after_load=2000,
                    ),
                )
            )
            logger.debug(f"Scrape result received")
            
            # Handle screenshot
            screenshot_url = getattr(scrape_result.data, "screenshot", None)
            if screenshot_url:
                logger.info(f"Downloading screenshot from: {screenshot_url}")
                response = requests.get(screenshot_url)
                response.raise_for_status()
                
                ext = os.path.splitext(screenshot_url)[1] or ".webp"
                filename = f"hypercap_shot_{uuid.uuid4()}{ext}"
                
                # Save path (absolute path for server)
                file_save_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                
                # Display path (relative to static folder for browser)
                screenshot_path = f"screenshots/{filename}"
                
                with open(file_save_path, "wb") as f:
                    f.write(response.content)
                logger.info(f"Screenshot saved to: {file_save_path}")
                
                # Verify file exists
                if not os.path.exists(file_save_path):
                    logger.error(f"File not found after saving: {file_save_path}")
                    error = "Failed to save screenshot file"
                    screenshot_path = None
                    filename = None
                else:
                    # Store in cache
                    cache[cache_key] = (screenshot_path, filename)
                
            else:
                logger.error("No screenshot URL found in scrape result")
                error = "No screenshot URL found in the scrape result"
                
        except requests.RequestException as e:
            logger.error(f"Failed to download screenshot: {str(e)}")
            error = f"Failed to download screenshot: {str(e)}"
        except Exception as e:
            logger.exception(f"Error during scrape: {str(e)}")
            error = f"Error occurred: {str(e)}"
    
    return render_template('index.html', 
                         screenshot_path=screenshot_path,
                         error=error,
                         filename=filename,
                         device_types=VIEWPORT_CONFIGS.keys(),
                         cache_buster=str(uuid.uuid4()),
                         cached=cached,
                         device_type=device_type)

@app.route('/download/<filename>')
def download_screenshot(filename):
    file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    logger.info(f"Attempting to download file: {file_path}")
    if os.path.exists(file_path):
        return send_file(file_path, as_attachment=True, download_name=filename)
    else:
        logger.error(f"Download failed: File not found: {file_path}")
        return "File not found", 404

if __name__ == '__main__':
    logger.info("Starting Flask application")
    app.run(host='0.0.0.0', port=5000, debug=True)