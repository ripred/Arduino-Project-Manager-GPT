#!/usr/bin/env python3
"""
server.py

FastAPI server for managing Arduino projects with arduino-cli.
Features:
- PROJECT_CACHE: tracks local Arduino projects (read/write)
- LIBRARY_CACHE: tracks Arduino/libraries (read-only)
- Just-in-time file reading to avoid huge payloads
- Endpoints for library/core mgmt, plus copying library examples
- Now fixes read_library_file to accept a JSON body via Pydantic model

Author: [Your Name]
Version: 2.1.1
"""

import os
import subprocess
import logging
import platform
import shutil
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
    version="2.1.1"
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
# Global Caches
# ---------------------------------------------------------
# PROJECT_CACHE: Map of project_name -> {path: Path, files: [relPaths]}
PROJECT_CACHE: Dict[str, Dict[str, any]] = {}

# LIBRARY_CACHE: Map of library_name -> {path: Path, files: [relPaths]}
LIBRARY_CACHE: Dict[str, Dict[str, any]] = {}

def get_files_in_dir(base_dir: Path) -> List[str]:
    """
    Return a sorted list of all relative file paths in base_dir, skipping hidden/system files.
    """
    file_paths = []
    for root, dirs, files in os.walk(base_dir):
        # Filter out hidden subdirectories
        dirs[:] = [d for d in dirs if not d.startswith('.')]
        for f in files:
            if f.startswith('.') or f in ['.DS_Store', 'Thumbs.db']:
                continue
            full_path = Path(root) / f
            rel_path = full_path.relative_to(base_dir)
            file_paths.append(str(rel_path))
    return sorted(file_paths)

# ---------------------------------------------------------
# Build & Refresh Project Cache
# ---------------------------------------------------------
def build_initial_project_cache():
    """
    Scan ARDUINO_DIR for projects, build PROJECT_CACHE.
    """
    logger.info("Building initial project cache...")
    PROJECT_CACHE.clear()

    # Exclude known system or hidden directories
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

def refresh_project_cache(project_name: str):
    """
    Refresh the file list for a single project.
    If it no longer exists, remove from PROJECT_CACHE.
    """
    project_dir = ARDUINO_DIR / project_name
    if not project_dir.exists():
        logger.info(f"Removing '{project_name}' from PROJECT_CACHE (no longer on disk).")
        PROJECT_CACHE.pop(project_name, None)
        return

    PROJECT_CACHE[project_name] = {
        "path": project_dir,
        "files": get_files_in_dir(project_dir)
    }
    logger.info(f"Refreshed cache for project '{project_name}'. File count: {len(PROJECT_CACHE[project_name]['files'])}")

# ---------------------------------------------------------
# Build & Refresh Library Cache
# ---------------------------------------------------------
def build_library_cache():
    """
    Scan ARDUINO_DIR/libraries for library folders, build LIBRARY_CACHE.
    Libraries are read-only; no create/update endpoints.
    """
    logger.info("Building library cache...")
    LIBRARY_CACHE.clear()

    libraries_dir = ARDUINO_DIR / "libraries"
    if not libraries_dir.exists():
        libraries_dir.mkdir(parents=True, exist_ok=True)

    for lib_folder in libraries_dir.iterdir():
        if not lib_folder.is_dir() or lib_folder.name.startswith('.'):
            continue
        lib_name = lib_folder.name
        LIBRARY_CACHE[lib_name] = {
            "path": lib_folder,
            "files": get_files_in_dir(lib_folder)
        }

    logger.info(f"Library cache built with {len(LIBRARY_CACHE)} libraries.")

# ---------------------------------------------------------
# Startup: build project & library caches
# ---------------------------------------------------------
build_initial_project_cache()
build_library_cache()

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

# New model for read_library_file
class ReadLibraryFileRequest(BaseModel):
    library_name: str
    file_path: str

class CopyExampleRequest(BaseModel):
    library_name: str
    example_folder: str
    new_project_name: str

# -------------------
# Library & Board Mgmt
# -------------------
class LibraryRequest(BaseModel):
    library_name: str

class LibrarySearchRequest(BaseModel):
    keyword: str

class CoreRequest(BaseModel):
    core: str

class CoreSearchRequest(BaseModel):
    keyword: str

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
    Check if the specified project folder exists.
    """
    project_dir = ARDUINO_DIR / request.project_name
    exists = project_dir.exists() and project_dir.is_dir()
    return {"exists": exists}

@app.post("/read_files", deprecated=True)
async def read_files(request: ProjectRequest):
    """
    DEPRECATED: Formerly returned the contents of all files.
    Now only returns a list of filenames. 
    Use /read_file to get actual file content on demand.
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
    Return the list of all file paths (no content) for a given project.
    Uses PROJECT_CACHE. If missing, attempt to refresh. If still missing, 404.
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
        # Check if file actually exists on disk
        full_path = PROJECT_CACHE[project_name]["path"] / file_path
        if not full_path.exists():
            raise HTTPException(status_code=404, detail="File not found in project")
        # Refresh the cache
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
    Refresh and list all project folders in ARDUINO_DIR (excluding system).
    """
    build_initial_project_cache()
    project_list = sorted(PROJECT_CACHE.keys())
    return {
        "projects": project_list,
        "arduino_dir": str(ARDUINO_DIR)
    }

# ---------------------------------------------------------
# Read-Only Library Browsing
# ---------------------------------------------------------
@app.get("/list_libraries")
async def list_libraries():
    """
    Lists the names of all libraries in Arduino/libraries, read from LIBRARY_CACHE.
    """
    build_library_cache()
    libs = sorted(LIBRARY_CACHE.keys())
    return {"libraries": libs}

@app.get("/list_files_in_library")
async def list_files_in_library(library_name: str):
    """
    Return the file paths in a specified library (read-only). No content returned here.
    """
    if library_name not in LIBRARY_CACHE:
        raise HTTPException(status_code=404, detail="Library not found")
    return {
        "library_name": library_name,
        "files": LIBRARY_CACHE[library_name]["files"]
    }

@app.post("/read_library_file")
async def read_library_file(request: ReadLibraryFileRequest):
    """
    Returns the content of a single file in a specified library, read-only.
    Accepts JSON body: { "library_name": ..., "file_path": ... }
    """
    library_name = request.library_name
    file_path = request.file_path

    if library_name not in LIBRARY_CACHE:
        raise HTTPException(status_code=404, detail="Library not found in cache")

    if file_path not in LIBRARY_CACHE[library_name]["files"]:
        raise HTTPException(status_code=404, detail="File not found in this library")

    full_path = LIBRARY_CACHE[library_name]["path"] / file_path
    try:
        with open(full_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        return {"file_path": file_path, "content": content}
    except Exception as e:
        logger.error(f"Failed to read file {full_path}: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

# ---------------------------------------------------------
# Copy Example Folder from Library to New Project
# ---------------------------------------------------------
@app.post("/copy_library_example")
async def copy_library_example(request: CopyExampleRequest):
    """
    Copies an example folder from a library into a new or existing local project folder.
    example_folder is relative to library's "examples" subfolder.
    """
    library_name = request.library_name
    example_folder = request.example_folder
    new_project_name = request.new_project_name

    if library_name not in LIBRARY_CACHE:
        raise HTTPException(status_code=404, detail="Library not found")

    library_path = LIBRARY_CACHE[library_name]["path"]
    source_folder = library_path / "examples" / example_folder
    if not source_folder.exists() or not source_folder.is_dir():
        raise HTTPException(status_code=404, detail="Example folder not found in library")

    project_dir = ARDUINO_DIR / new_project_name
    project_dir.mkdir(parents=True, exist_ok=True)

    # Recursively copy
    for root, dirs, files in os.walk(source_folder):
        # skip hidden dirs
        dirs[:] = [d for d in dirs if not d.startswith('.')]
        rel = Path(root).relative_to(source_folder)
        dest_dir = project_dir / rel
        dest_dir.mkdir(parents=True, exist_ok=True)

        for file in files:
            if file.startswith('.') or file in ['.DS_Store', 'Thumbs.db']:
                continue
            src_file = Path(root) / file
            shutil.copy2(src_file, dest_dir)

    # Refresh project cache so new files appear
    refresh_project_cache(new_project_name)

    return {
        "status": "success",
        "message": f"Copied example '{example_folder}' from library '{library_name}' to project '{new_project_name}'"
    }

# ---------------------------------------------------------
# Library Management (Install/Uninstall/Search/Update)
# ---------------------------------------------------------
@app.get("/list_libraries_installed")
async def list_libraries_installed():
    """
    Run `arduino-cli lib list` to see all installed libraries (CLI text-based).
    """
    command = ["arduino-cli", "lib", "list"]
    result = run_command(command)
    return result

@app.post("/search_library")
async def search_library(request: LibrarySearchRequest):
    command = ["arduino-cli", "lib", "search", request.keyword]
    return run_command(command)

@app.post("/install_library")
async def install_library(request: LibraryRequest):
    command = ["arduino-cli", "lib", "install", request.library_name]
    r = run_command(command)
    build_library_cache()  # refresh to reflect new library folder
    return r

@app.post("/uninstall_library")
async def uninstall_library(request: LibraryRequest):
    command = ["arduino-cli", "lib", "uninstall", request.library_name]
    r = run_command(command)
    build_library_cache()
    return r

@app.post("/update_library")
async def update_library(request: LibraryRequest):
    command = ["arduino-cli", "lib", "update", request.library_name]
    r = run_command(command)
    build_library_cache()
    return r

@app.post("/update_all_libraries")
async def update_all_libraries():
    command = ["arduino-cli", "lib", "update"]
    r = run_command(command)
    build_library_cache()
    return r

# ---------------------------------------------------------
# Board / Core Management Endpoints
# ---------------------------------------------------------
@app.get("/list_connected_boards")
async def list_connected_boards():
    command = ["arduino-cli", "board", "list"]
    return run_command(command)

@app.post("/search_cores")
async def search_cores(request: CoreSearchRequest):
    command = ["arduino-cli", "core", "search", request.keyword]
    return run_command(command)

@app.post("/install_core")
async def install_core(request: CoreRequest):
    command = ["arduino-cli", "core", "install", request.core]
    return run_command(command)

@app.post("/uninstall_core")
async def uninstall_core(request: CoreRequest):
    command = ["arduino-cli", "core", "uninstall", request.core]
    return run_command(command)

