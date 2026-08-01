import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ALLOWED_USER_IDS = [
    int(uid.strip())
    for uid in os.getenv("ALLOWED_USER_IDS", "").split(",")
    if uid.strip().isdigit()
]

AGY_PATH = os.getenv("AGY_PATH", "/home/borichdev/.local/bin/agy")
WORKSPACE_DIR = os.getenv("WORKSPACE_DIR", "/home/borichdev")
DEFAULT_MODEL = os.getenv("DEFAULT_MODEL", "")
