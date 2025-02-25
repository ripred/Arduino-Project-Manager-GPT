#!/usr/bin/env python3
"""
server.py

FastAPI server for managing Arduino projects with arduino-cli.
Now includes endpoints for library and board (core) management.
"""

import os
import subprocess
import logging
import platform
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional, Dict, List
from pathlib import Path

# -------------------------------------------------------
# Logging Setup
# -------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("server.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# -------------------------------------------------------
# FastAPI Initialization
# -------------------------------------------------------
app = FastAPI(
    title="Arduino Project Manager",
    description="API for managing Arduino projects, libraries, and board cores with arduino-cli",
    version="1.3.0"  # bumped version
)

# -------------------------------------------------------
# Arduino Directory Setup
# -------------------------------------------------------
OS_TYPE = platform.system()  # e.g. 'Windows', 'Linux', 'Darwin'
if OS_TYPE == "Darwin":  # macOS
    ARDUINO_DIR = Path.home() / "Documents" / "Arduino"
elif OS_TYPE == "Windows":
    ARDUINO_DIR = Path(os.environ["USERPROFILE"]) / "Documents" / "Arduino"
elif OS_TYPE == "Linux":
    ARDUINO_DIR = Path.home() / "Arduino"
else:
    raise RuntimeError(f"Unsupported operating system: {OS_TYPE}")

ARDUINO_DIR.mkdir(parents=True, exist_ok=True)
logger.info(f"Arduino projects directory set to: {ARDUINO_DIR}")

# -------------------------------------------------------
# Pydantic Models
# -------------------------------------------------------
class ProjectRequest(BaseModel):
    project_name: str

class UploadRequest(BaseModel):
    project_name: str
    port: str

class SketchRequest(BaseModel):
    project_name: str
    sketch_content: str
    file_path: Optional[str] = None

# For library actions
class LibraryRequest(BaseModel):
    """
    Request model for library actions such as install, uninstall, update.
    """
    library_name: str

class LibrarySearchRequest(BaseModel):
    """
    Request model for searching libraries by a keyword.
    """
    keyword: str

class CoreRequest(BaseModel):
    """
    Request model for installing/uninstalling board cores (e.g. "arduino:avr").
    """
    core: str

class CoreSearchRequest(BaseModel):
    """
    Request model for searching available board cores by a keyword.
    """
    keyword: str

# -------------------------------------------------------
# Helper Functions
# -------------------------------------------------------
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

# -------------------------------------------------------
# Project Management Endpoints (unchanged from previous)
# -------------------------------------------------------
@app.post("/check_folder")
async def check_folder(request: ProjectRequest):
    """
    Check if the specified project folder exists under ARDUINO_DIR.
    """
    project_dir = ARDUINO_DIR / request.project_name
    exists = project_dir.exists() and project_dir.is_dir()
    return {"exists": exists}

@app.post("/read_files")
async def read_files(request: ProjectRequest):
    """
    Recursively read all files in the specified project folder and return their contents.
    """
    project_dir = ARDUINO_DIR / request.project_name
    if not project_dir.exists() or not project_dir.is_dir():
        raise HTTPException(status_code=404, detail="Project folder not found")

    def is_valid_file(filename: str) -> bool:
        if filename.startswith('.') or filename in ['.DS_Store', 'Thumbs.db']:
            return False
        return True

    files_content = {}
    for root, dirs, files in os.walk(project_dir):
        for file in files:
            if not is_valid_file(file):
                continue
            file_path = Path(root) / file
            rel_path = file_path.relative_to(project_dir)
            try:
                with open(file_path, "r", encoding='utf-8', errors='replace') as f:
                    files_content[str(rel_path)] = f.read()
            except Exception as e:
                logger.warning(f"Failed to read file {file_path}: {str(e)}")

        # skip hidden directories
        dirs[:] = [d for d in dirs if not d.startswith('.')]

    return {"files": files_content}

@app.post("/create_project")
async def create_project(request: SketchRequest):
    """
    Creates a new project folder. By default, also creates <project_name>.ino.
    If file_path is provided, that file is created instead.
    """
    project_dir = ARDUINO_DIR / request.project_name
    if project_dir.exists():
        raise HTTPException(status_code=400, detail="Project already exists")

    try:
        project_dir.mkdir(parents=True, exist_ok=True)
        file_path = request.file_path if request.file_path else f"{request.project_name}.ino"
        create_or_update_file(project_dir, file_path, request.sketch_content)

        return {
            "status": "success",
            "message": f"Created project '{request.project_name}' with file '{file_path}'"
        }
    except Exception as e:
        logger.error(f"Failed to create project {project_dir}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to create project: {str(e)}")

@app.post("/update_sketch")
async def update_sketch(request: SketchRequest):
    """
    Legacy endpoint name. Updates or creates a file in an existing project.
    If file_path is omitted, updates <project_name>.ino by default.
    """
    project_dir = ARDUINO_DIR / request.project_name
    if not project_dir.exists():
        raise HTTPException(status_code=404, detail="Project or sketch file not found")

    try:
        file_path = request.file_path if request.file_path else f"{request.project_name}.ino"
        create_or_update_file(project_dir, file_path, request.sketch_content)
        return {"status": "success", "message": f"Updated file '{file_path}'"}
    except Exception as e:
        logger.error(f"Failed to update file in {project_dir}: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/compile_project")
async def compile_project(request: ProjectRequest):
    """
    Compiles the project using arduino-cli, assuming <project_name>.ino is in the root.
    """
    project_dir = ARDUINO_DIR / request.project_name
    ino_file = project_dir / f"{request.project_name}.ino"
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
    """
    Uploads the project using arduino-cli, also assuming <project_name>.ino in root.
    """
    project_dir = ARDUINO_DIR / request.project_name
    ino_file = project_dir / f"{request.project_name}.ino"
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
    Lists all project folders in the Arduino directory,
    excluding hidden/system directories (hardware, libraries, tools).
    """
    try:
        items = list(ARDUINO_DIR.iterdir())
        excluded_dirs = {"hardware", "libraries", "tools"}
        projects = []
        for item in items:
            if item.is_dir() and not item.name.startswith('.') and item.name.lower() not in excluded_dirs:
                projects.append(item.name)
        return {"projects": sorted(projects), "arduino_dir": str(ARDUINO_DIR)}
    except PermissionError as e:
        return {"projects": [], "error": f"Permission error: {str(e)}"}
    except Exception as e:
        return {"projects": [], "error": str(e)}

# -------------------------------------------------------
# NEW: Library Management Endpoints
# -------------------------------------------------------
@app.get("/list_libraries")
async def list_libraries():
    """
    Lists all installed Arduino libraries using 'arduino-cli lib list'.
    """
    command = ["arduino-cli", "lib", "list"]
    result = run_command(command)
    # The output is a textual listing; you might want to parse it if needed.
    return result

@app.post("/search_library")
async def search_library(request: LibrarySearchRequest):
    """
    Searches for libraries by keyword using 'arduino-cli lib search <keyword>'.
    """
    command = ["arduino-cli", "lib", "search", request.keyword]
    result = run_command(command)
    return result

@app.post("/install_library")
async def install_library(request: LibraryRequest):
    """
    Installs a library by name using 'arduino-cli lib install <library_name>'.
    For example, 'Adafruit_Sensor' or 'ArduinoJson@6.20.0'.
    """
    command = ["arduino-cli", "lib", "install", request.library_name]
    result = run_command(command)
    return result

@app.post("/uninstall_library")
async def uninstall_library(request: LibraryRequest):
    """
    Uninstalls a library by name using 'arduino-cli lib uninstall <library_name>'.
    """
    command = ["arduino-cli", "lib", "uninstall", request.library_name]
    result = run_command(command)
    return result

@app.post("/update_library")
async def update_library(request: LibraryRequest):
    """
    Updates a specific library by name using 'arduino-cli lib update <library_name>'.
    If you want to update all libraries, call /update_all_libraries instead.
    """
    command = ["arduino-cli", "lib", "update", request.library_name]
    result = run_command(command)
    return result

@app.post("/update_all_libraries")
async def update_all_libraries():
    """
    Updates all installed libraries using 'arduino-cli lib update'.
    """
    command = ["arduino-cli", "lib", "update"]
    result = run_command(command)
    return result

# -------------------------------------------------------
# NEW: Board (Core) Management Endpoints
# -------------------------------------------------------
@app.get("/list_connected_boards")
async def list_connected_boards():
    """
    Lists all currently connected (physical) boards via 'arduino-cli board list'.
    """
    command = ["arduino-cli", "board", "list"]
    result = run_command(command)
    return result

@app.post("/search_cores")
async def search_cores(request: CoreSearchRequest):
    """
    Searches for available board cores by a keyword, e.g. 'esp32', 'rp2040', 'stm32'.
    Uses 'arduino-cli core search <keyword>'.
    """
    command = ["arduino-cli", "core", "search", request.keyword]
    result = run_command(command)
    return result

@app.post("/install_core")
async def install_core(request: CoreRequest):
    """
    Installs a board core using 'arduino-cli core install <core>', e.g. 'esp32:esp32'.
    """
    command = ["arduino-cli", "core", "install", request.core]
    result = run_command(command)
    return result

@app.post("/uninstall_core")
async def uninstall_core(request: CoreRequest):
    """
    Uninstalls a board core using 'arduino-cli core uninstall <core>'.
    """
    command = ["arduino-cli", "core", "uninstall", request.core]
    result = run_command(command)
    return result

