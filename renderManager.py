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

def sendNotification(title, message):
    """ Quick wrapper for system notifications """
    try:
        subprocess.run(["notify-send", title, message], check=True)
    except:
        logging.error("Could not send system notification")

def getMatchFromLLM(target, folderList, levelName):
    """ 
    Ask Ollama to help match folder names. 
    Handles my typos and weird abbreviations like 'wrk' for 'Work'.
    """
    if not folderList:
        return None
    
    # Simple check first - saves an API call
    for folder in folderList:
        if target.lower() == folder.lower():
            return folder

    # If simple check fails, bring in the big guns (LLM)
    apiUrl = "http://localhost:11434/api/generate"
    
    # Prompt is tailored for Qwen 3.5 reasoning
    promptText = f"""
    Match "{target}" to one of these folders: {folderList}.
    This is for a {levelName} folder.
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

def getFinalPath(workType, brandName, projectId):
    """ Find or create the directory structure on the NAS """
    root = Path(userConfig["baseWorkDir"])
    
    levels = [("workType", workType), ("brandName", brandName), ("projectId", projectId)]
    currentPath = root

    for levelKey, levelValue in levels:
        if not currentPath.exists():
            currentPath.mkdir(parents=True, exist_ok=True)
            
        existingFolders = [d.name for d in currentPath.iterdir() if d.is_dir()]
        match = getMatchFromLLM(levelValue, existingFolders, levelKey)
        
        if match:
            currentPath = currentPath / match
        else:
            # If LLM is unsure, put it in a check folder so I can fix it later
            newFolderName = f"NEW_CHECK_{levelValue}"
            currentPath = currentPath / newFolderName
            currentPath.mkdir(parents=True, exist_ok=True)
            logging.info(f"Anomaly: created {currentPath}")
            sendNotification("Manual Check!", f"Sorting failed, check: {newFolderName}")

    # Add organized subfolders
    currentPath = currentPath / "passes" / "afterEffects"
    currentPath.mkdir(parents=True, exist_ok=True)

    return currentPath

def processFile(filePath):
    """ Main logic for moving the render file """
    logging.info(f"Detected: {filePath.name}")
    sendNotification("New Render", f"Processing {filePath.name}...")
    
    # Expected format: name_type_brand_project.ext
    parts = filePath.stem.split("_")
    if len(parts) < 4:
        logging.warning(f"File {filePath.name} doesn't follow naming rules. Ignoring.")
        return

    workType = parts[1]
    brandName = parts[2]
    projectId = "_".join(parts[3:])
    fileName = parts[0]

    # Copy to toSend folder for easy sharing
    try:
        toSendDir = Path(userConfig["baseWorkDir"]) / "toSend"
        toSendDir.mkdir(parents=True, exist_ok=True)
        toSendPath = toSendDir / f"{brandName}_{fileName}{filePath.suffix}"
        
        # Versioning for toSend copy (brandName_fileName_1, brandName_fileName_2, etc.)
        v = 1
        while toSendPath.exists():
            toSendPath = toSendDir / f"{brandName}_{fileName}_{v}{filePath.suffix}"
            v += 1
            
        shutil.copy2(str(filePath), str(toSendPath))
        logging.info(f"Copied to toSend: {toSendPath.name}")
    except Exception as e:
        logging.error(f"Copy to toSend failed: {e}")

    targetDir = getFinalPath(workType, brandName, projectId)
    finalPath = targetDir / filePath.name

    # Simple versioning so we don't overwrite old renders
    version = 1
    while finalPath.exists():
        finalPath = targetDir / filePath.with_name(f"{filePath.stem}_v{version:02d}{filePath.suffix}").name
        version += 1

    try:
        shutil.move(str(filePath), str(finalPath))
        logging.info(f"Done: {filePath.name} -> {targetDir.name}")
        sendNotification("Organized!", f"Moved to {targetDir.name}")
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
    """ Deletes old files from temp render folder and toSend folder """
    now = time.time()
    cleanupThreshold = userConfig["cleanupDays"] * 86400
    
    cleanupDirs = [
        Path(userConfig["sourceDir"]),
        Path(userConfig["baseWorkDir"]) / "toSend"
    ]

    for targetDir in cleanupDirs:
        if not targetDir.exists():
            continue
            
        for item in targetDir.iterdir():
            if item.is_file() and (now - item.stat().st_ctime > cleanupThreshold):
                try:
                    item.unlink()
                    logging.info(f"Cleaned: {item.name} from {targetDir.name}")
                except Exception as e:
                    logging.error(f"Failed to clean {item.name}: {e}")

def isVBoxRunning():
    """ Check if any VirtualBox VMs are currently active """
    try:
        result = subprocess.run(["vboxmanage", "list", "runningvms"], capture_output=True, text=True)
        return len(result.stdout.strip()) > 0
    except:
        return False

if __name__ == "__main__":
    logging.info("Daemon active...")
    
    sourceDir = Path(userConfig["sourceDir"])
    sourceDir.mkdir(parents=True, exist_ok=True)
    Path(userConfig["baseWorkDir"]).mkdir(parents=True, exist_ok=True)

    # Initial scan on startup
    for item in sourceDir.iterdir():
        if item.is_file(): processFile(item)

    handler = WatcherHandler()
    observer = Observer()
    observer.schedule(handler, str(sourceDir), recursive=False)
    observer.start()

    lastCleanupRun = 0
    try:
        while True:
            # Daily cleanup check
            if time.time() - lastCleanupRun > 86400:
                startCleanup()
                lastCleanupRun = time.time()
            
            # Manual polling fallback for VirtualBox/NAS sync issues
            if isVBoxRunning():
                for item in sourceDir.iterdir():
                    if item.is_file():
                        # Give it a tiny bit of time to finish writing
                        time.sleep(0.5)
                        processFile(item)

            time.sleep(5)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()
