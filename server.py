#!/usr/bin/env python3
"""
server.py

FastAPI server for managing Arduino projects with arduino-cli.
Now uses a global PROJECT_CACHE to store project file paths,
and provides just-in-time file reading to avoid large payloads.
"""

import os
import subprocess
import logging
import platform
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional, Dict, List
from pathlib import Path

# ---------------------------------------------------------
# Logging Setup
# ---------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("server.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------
# FastAPI Initialization
# ---------------------------------------------------------
app = FastAPI(
    title="Arduino Project Manager",
    description="API for managing Arduino projects, libraries, and board cores with arduino-cli (cached file listing).",
    version="2.0.0"  # bumped version
)

# ---------------------------------------------------------
# Arduino Directory Setup
# ---------------------------------------------------------
OS_TYPE = platform.system()  # 'Windows', 'Linux', 'Darwin'
if OS_TYPE == "Darwin":
    ARDUINO_DIR = Path.home() / "Documents" / "Arduino"
elif OS_TYPE == "Windows":
    ARDUINO_DIR = Path(os.environ["USERPROFILE"]) / "Documents" / "Arduino"
elif OS_TYPE == "Linux":
    ARDUINO_DIR = Path.home() / "Arduino"
else:
    raise RuntimeError(f"Unsupported operating system: {OS_TYPE}")

ARDUINO_DIR.mkdir(parents=True, exist_ok=True)
logger.info(f"Arduino projects directory set to: {ARDUINO_DIR}")

# ---------------------------------------------------------
# Global Project Cache
# ---------------------------------------------------------
# PROJECT_CACHE is a dict like:
# {
#   "ProjectName": {
#       "path": Path("/path/to/ProjectName"),
#       "files": ["main.ino", "src/engine.cpp", ...]
#   },
#   ...
# }
PROJECT_CACHE: Dict[str, Dict[str, any]] = {}

def build_initial_cache():
    """
    Scan ARDUINO_DIR for projects, build initial PROJECT_CACHE.
    """
    logger.info("Building initial project cache...")
    PROJECT_CACHE.clear()

    # Exclude known system/hidden directories
    excluded_dirs = {"hardware", "libraries", "tools"}

    for item in ARDUINO_DIR.iterdir():
        if not item.is_dir():
            continue
        if item.name.startswith('.') or item.name.lower() in excluded_dirs:
            continue

        project_name = item.name
        PROJECT_CACHE[project_name] = {
            "path": item,
            "files": get_files_in_dir(item)
        }

    logger.info(f"Initial cache built with {len(PROJECT_CACHE)} projects.")

def get_files_in_dir(project_dir: Path) -> List[str]:
    """
    Return a list of relative file paths under 'project_dir', skipping hidden files.
    """
    file_paths = []
    for root, dirs, files in os.walk(project_dir):
        # Filter out hidden subdirectories
        dirs[:] = [d for d in dirs if not d.startswith('.')]
        for f in files:
            if f.startswith('.') or f in ['.DS_Store', 'Thumbs.db']:
                continue
            full_path = Path(root) / f
            rel_path = full_path.relative_to(project_dir)
            file_paths.append(str(rel_path))
    return sorted(file_paths)

def refresh_project_cache(project_name: str):
    """
    Refresh the file list for a single project.
    If it doesn't exist (folder removed?), remove from PROJECT_CACHE.
    """
    project_dir = ARDUINO_DIR / project_name
    if not project_dir.exists():
        logger.info(f"Removing '{project_name}' from cache (no longer exists on disk).")
        PROJECT_CACHE.pop(project_name, None)
        return

    PROJECT_CACHE[project_name] = {
        "path": project_dir,
        "files": get_files_in_dir(project_dir)
    }
    logger.info(
        f"Refreshed cache for project '{project_name}' with "
        f"{len(PROJECT_CACHE[project_name]['files'])} files."
    )

# Build the cache at startup
build_initial_cache()

# ---------------------------------------------------------
# Pydantic Models
# ---------------------------------------------------------
class ProjectRequest(BaseModel):
    project_name: str

class UploadRequest(BaseModel):
    project_name: str
    port: str

class SketchRequest(BaseModel):
    project_name: str
    sketch_content: str
    file_path: Optional[str] = None

class ReadFileRequest(BaseModel):
    project_name: str
    file_path: str

# (LibraryRequest, LibrarySearchRequest, CoreRequest, etc., if you're using library/core endpoints)

# ---------------------------------------------------------
# Helper Functions
# ---------------------------------------------------------
def run_command(command: List[str], cwd: Optional[Path] = None) -> Dict[str, str]:
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            check=True
        )
        return {"status": "success", "output": result.stdout, "error": ""}
    except subprocess.CalledProcessError as e:
        logger.error(f"Command failed: {command}\nError: {e.stderr}")
        return {"status": "error", "output": "", "error": e.stderr}
    except Exception as e:
        logger.error(f"Unexpected error running command {command}: {str(e)}")
        return {"status": "error", "output": "", "error": str(e)}

def create_or_update_file(base_dir: Path, relative_file_path: str, content: str) -> None:
    full_path = base_dir / relative_file_path
    full_path.parent.mkdir(parents=True, exist_ok=True)
    with open(full_path, "w", encoding="utf-8") as f:
        f.write(content)
    logger.info(f"File created/updated: {full_path}")

# ---------------------------------------------------------
# Project Management Endpoints (with caching)
# ---------------------------------------------------------
@app.post("/check_folder")
async def check_folder(request: ProjectRequest):
    """
    Check if the specified project folder exists (per the cache or file system).
    """
    project_dir = ARDUINO_DIR / request.project_name
    exists = project_dir.exists() and project_dir.is_dir()
    return {"exists": exists}

@app.post("/read_files", deprecated=True)
async def read_files(request: ProjectRequest):
    """
    DEPRECATED: Formerly returned the contents of all files.
    Now, to avoid huge payloads, returns only a list of filenames.
    Use /read_file to fetch individual file contents on demand.
    """
    project_name = request.project_name
    if project_name not in PROJECT_CACHE:
        project_dir = ARDUINO_DIR / project_name
        if not project_dir.exists():
            raise HTTPException(status_code=404, detail="Project folder not found")
        refresh_project_cache(project_name)

    files_list = PROJECT_CACHE[project_name]["files"]
    return {
        "files": files_list,
        "message": "Use /read_file to get content of individual files."
    }

@app.get("/list_files_in_project")
async def list_files_in_project(project_name: str):
    """
    Return the list of all file paths (no content) for a given project, from the cache.
    If missing in cache, attempt to refresh. If still missing, 404.
    """
    if project_name not in PROJECT_CACHE:
        project_dir = ARDUINO_DIR / project_name
        if not project_dir.exists():
            raise HTTPException(status_code=404, detail="Project folder not found")
        refresh_project_cache(project_name)

    return {
        "project_name": project_name,
        "files": PROJECT_CACHE[project_name]["files"]
    }

@app.post("/read_file")
async def read_file(request: ReadFileRequest):
    """
    Returns the content of a single file from a given project, on demand.
    """
    project_name = request.project_name
    file_path = request.file_path

    if project_name not in PROJECT_CACHE:
        refresh_project_cache(project_name)
        if project_name not in PROJECT_CACHE:
            raise HTTPException(status_code=404, detail="Project folder not found")

    if file_path not in PROJECT_CACHE[project_name]["files"]:
        # Might need to check if the file is actually on disk
        full_path = PROJECT_CACHE[project_name]["path"] / file_path
        if not full_path.exists():
            raise HTTPException(status_code=404, detail="File not found in project")
        # If file is on disk, we can refresh cache
        refresh_project_cache(project_name)

        if file_path not in PROJECT_CACHE[project_name]["files"]:
            raise HTTPException(status_code=404, detail="File not found in project after refresh")

    # Read content
    full_path = PROJECT_CACHE[project_name]["path"] / file_path
    try:
        with open(full_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        return {"file_path": file_path, "content": content}
    except Exception as e:
        logger.error(f"Failed to read file {full_path}: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/create_project")
async def create_project(request: SketchRequest):
    project_name = request.project_name
    project_dir = ARDUINO_DIR / project_name
    if project_dir.exists():
        raise HTTPException(status_code=400, detail="Project already exists")

    try:
        project_dir.mkdir(parents=True, exist_ok=True)
        file_path = request.file_path if request.file_path else f"{project_name}.ino"
        create_or_update_file(project_dir, file_path, request.sketch_content)
        # Update cache
        refresh_project_cache(project_name)

        return {
            "status": "success",
            "message": f"Created project '{project_name}' with file '{file_path}'"
        }
    except Exception as e:
        logger.error(f"Failed to create project {project_dir}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to create project: {str(e)}")

@app.post("/update_sketch")
async def update_sketch(request: SketchRequest):
    project_name = request.project_name
    project_dir = ARDUINO_DIR / project_name
    if not project_dir.exists():
        raise HTTPException(status_code=404, detail="Project or sketch file not found")

    file_path = request.file_path if request.file_path else f"{project_name}.ino"
    try:
        create_or_update_file(project_dir, file_path, request.sketch_content)
        refresh_project_cache(project_name)
        return {"status": "success", "message": f"Updated file '{file_path}' in project '{project_name}'"}
    except Exception as e:
        logger.error(f"Failed to update file in {project_dir}: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/compile_project")
async def compile_project(request: ProjectRequest):
    project_name = request.project_name
    project_dir = ARDUINO_DIR / project_name
    ino_file = project_dir / f"{project_name}.ino"

    if not project_dir.exists() or not ino_file.exists():
        raise HTTPException(status_code=404, detail="Project or sketch file not found")

    command = [
        "arduino-cli", "compile",
        "--fqbn", "arduino:avr:nano:cpu=atmega328old",
        str(project_dir)
    ]
    result = run_command(command, cwd=ARDUINO_DIR)
    if result["status"] == "error":
        return {"status": "error", "message": result["error"]}
    return result

@app.post("/upload_project")
async def upload_project(request: UploadRequest):
    project_name = request.project_name
    project_dir = ARDUINO_DIR / project_name
    ino_file = project_dir / f"{project_name}.ino"
    if not project_dir.exists() or not ino_file.exists():
        raise HTTPException(status_code=404, detail="Project or sketch file not found")

    command = [
        "arduino-cli", "upload",
        "-p", request.port,
        "--fqbn", "arduino:avr:nano:cpu=atmega328old",
        str(project_dir)
    ]
    result = run_command(command, cwd=ARDUINO_DIR)
    if result["status"] == "error":
        return {"status": "error", "message": result["error"]}
    return result

@app.get("/list_projects")
async def list_projects():
    """
    Lists all project folders from the cache; refreshes first to pick up new/removed projects.
    """
    build_initial_cache()  # re-scan in case user manually added/deleted something
    project_list = sorted(PROJECT_CACHE.keys())
    return {
        "projects": project_list,
        "arduino_dir": str(ARDUINO_DIR)
    }

# (Optionally re-add your library/core endpoints if needed)

