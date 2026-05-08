import os
import json
import time
import shutil
import logging
import requests
import subprocess
from pathlib import Path
from logging.handlers import RotatingFileHandler
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

# Local paths for config and logs
# Sticking these in home so they don't clutter the NAS
configPath = Path.home() / ".config" / "aeRenderManager" / "config.json"
logPath = Path.home() / ".local" / "state" / "aeRenderManager" / "daemon.log"

def loadConfig():
    """ Load user settings or create defaults if missing """
    if not configPath.exists():
        os.makedirs(configPath.parent, exist_ok=True)
        # Default paths - usually NAS based
        defaults = {
            "sourceDir": "/srv/nas/work/renderManagerTempOutput/",
            "baseWorkDir": "/srv/nas/work/",
            "ollamaModel": "qwen3.5:4b",
            "cleanupDays": 14
        }
        with open(configPath, "w") as f:
            json.dump(defaults, f, indent=4)
        return defaults
    
    with open(configPath, "r") as f:
        return json.load(f)

# Load config globally
userConfig = loadConfig()

# Setup logging - rotate at 1MB so we don't fill up the disk
os.makedirs(logPath.parent, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[RotatingFileHandler(logPath, maxBytes=1024*1024, backupCount=2)]
)

def sendNotify(title, msg):
    """ Quick wrapper for system notifications """
    try:
        subprocess.run(["notify-send", title, msg], check=True)
    except:
        logging.error("Could not send system notification")

def getMatchFromLLM(target, folders, level):
    """ 
    Ask Ollama to help match folder names. 
    Handles my typos and weird abbreviations like 'wrk' for 'Work'.
    """
    if not folders:
        return None
    
    # Simple check first - saves an API call
    for f in folders:
        if target.lower() == f.lower():
            return f

    # If simple check fails, bring in the big guns (LLM)
    apiUrl = "http://localhost:11434/api/generate"
    
    # Prompt is tailored for Qwen 3.5 reasoning
    promptText = f"""
    Match "{target}" to one of these folders: {folders}.
    This is for a {level} folder.
    - If it's a typo or abbreviation, match it.
    - If it's totally different, return null.
    - Response MUST be JSON: {{"match": "name" or null}}
    """

    payload = {
        "model": userConfig["ollamaModel"],
        "prompt": promptText,
        "format": "json",
        "stream": False,
        "keep_alive": 0, # Flush VRAM immediately after call
        "options": {"temperature": 0}
    }

    try:
        r = requests.post(apiUrl, json=payload, timeout=25).json()
        # Check both fields because Qwen sometimes puts results in 'thinking'
        data = r.get("response") or r.get("thinking")
        if data:
            return json.loads(data.strip()).get("match")
    except Exception as err:
        logging.error(f"LLM Match failed: {err}")
    
    return None

def getFinalPath(workType, brand, project):
    """ Find or create the directory structure on the NAS """
    root = Path(userConfig["baseWorkDir"])
    
    parts = [("workType", workType), ("brand", brand), ("project", project)]
    current = root

    for key, val in parts:
        if not current.exists():
            current.mkdir(parents=True, exist_ok=True)
            
        existing = [d.name for d in current.iterdir() if d.is_dir()]
        match = getMatchFromLLM(val, existing, key)
        
        if match:
            current = current / match
        else:
            # If LLM is unsure, put it in a check folder so I can fix it later
            newFolder = f"NEW_CHECK_{val}"
            current = current / newFolder
            current.mkdir(parents=True, exist_ok=True)
            logging.info(f"Anomaly: created {current}")
            sendNotify("Manual Check!", f"Sorting failed, check: {newFolder}")

    return current

def processFile(fPath):
    """ Main logic for moving the render file """
    logging.info(f"Detected: {fPath.name}")
    sendNotify("New Render", f"Processing {fPath.name}...")
    
    # Expected format: name_type_brand_project.ext
    slugs = fPath.stem.split("_")
    if len(slugs) < 4:
        logging.warning(f"File {fPath.name} doesn't follow naming rules. Ignoring.")
        return

    wType = slugs[1]
    brand = slugs[2]
    projId = "_".join(slugs[3:])

    targetDir = getFinalPath(wType, brand, projId)
    finalPath = targetDir / fPath.name

    # Simple versioning so we don't overwrite old renders
    v = 1
    while finalPath.exists():
        finalPath = targetDir / fPath.with_name(f"{fPath.stem}_v{v:02d}{fPath.suffix}").name
        v += 1

    try:
        shutil.move(str(fPath), str(finalPath))
        logging.info(f"Done: {fPath.name} -> {targetDir.name}")
        sendNotify("Organized!", f"Moved to {targetDir.name}")
    except Exception as e:
        logging.error(f"Move failed: {e}")

class WatcherHandler(FileSystemEventHandler):
    def on_closed(self, event):
        if not event.is_directory: processFile(Path(event.src_path))
    
    def on_created(self, event):
        if not event.is_directory:
            time.sleep(1) # Wait a sec for the file to settle
            processFile(Path(event.src_path))

    def on_moved(self, event):
        if not event.is_directory: processFile(Path(event.dest_path))

def startCleanup():
    """ Deletes old files from temp render folder """
    now = time.time()
    src = Path(userConfig["sourceDir"])
    for item in src.iterdir():
        if item.is_file() and (now - item.stat().st_ctime > (userConfig["cleanupDays"] * 86400)):
            try:
                item.unlink()
                logging.info(f"Cleaned: {item.name}")
            except:
                pass

if __name__ == "__main__":
    logging.info("Daemon active...")
    
    sDir = Path(userConfig["sourceDir"])
    sDir.mkdir(parents=True, exist_ok=True)
    Path(userConfig["baseWorkDir"]).mkdir(parents=True, exist_ok=True)

    # Initial scan on startup
    for item in sDir.iterdir():
        if item.is_file(): processFile(item)

    handler = WatcherHandler()
    obs = Observer()
    obs.schedule(handler, str(sDir), recursive=False)
    obs.start()

    lastRun = 0
    try:
        while True:
            # Daily cleanup check
            if time.time() - lastRun > 86400:
                startCleanup()
                lastRun = time.time()
            time.sleep(5)
    except KeyboardInterrupt:
        obs.stop()
    obs.join()
