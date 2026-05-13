#!/usr/bin/env python3
"""
setup.py — Auto-installer for OCR Enterprise System
Run once: python setup.py
"""
import subprocess, sys, platform, shutil, os

RED   = "\033[91m"; GREEN = "\033[92m"
YELLOW= "\033[93m"; CYAN  = "\033[96m"
BOLD  = "\033[1m";  RESET = "\033[0m"

def pr(color, msg): print(f"{color}{msg}{RESET}")

def run(cmd, check=True):
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if check and result.returncode != 0:
        pr(RED, f"✗  FAILED: {cmd}\n{result.stderr}")
    else:
        pr(GREEN, f"✓  {cmd}")
    return result

pr(BOLD+CYAN, "\n══════════════════════════════════════════")
pr(BOLD+CYAN,   "  OCR Enterprise System — Auto Installer  ")
pr(BOLD+CYAN, "══════════════════════════════════════════\n")

# ── 1. Python packages ────────────────────────────────────────────────────────
PACKAGES = [
    "flask>=3.0.0",
    "flask-cors>=4.0.0",
    "PyMuPDF>=1.23.0",
    "pytesseract>=0.3.10",
    "Pillow>=10.0.0",
    "python-docx>=1.1.0",
    "numpy>=1.24.0",
    "arabic-reshaper>=3.0.0",
    "python-bidi>=0.4.2",
    "werkzeug>=3.0.0",
    "psutil>=5.9.0",
    "lxml>=4.9.0",
    "scipy>=1.11.0",
]

pr(BOLD, "\n[1/3] Installing Python packages…")
for pkg in PACKAGES:
    run(f"{sys.executable} -m pip install \"{pkg}\" -q")

# ── 2. Tesseract ──────────────────────────────────────────────────────────────
pr(BOLD, "\n[2/3] Checking Tesseract-OCR…")
if shutil.which("tesseract"):
    pr(GREEN, "✓  Tesseract already installed")
    result = subprocess.run("tesseract --version", shell=True, capture_output=True, text=True)
    pr(CYAN, result.stdout.split("\n")[0])
else:
    system = platform.system()
    if system == "Linux":
        pr(YELLOW, "Installing Tesseract via apt…")
        run("sudo apt-get update -qq")
        run("sudo apt-get install -y tesseract-ocr tesseract-ocr-ara tesseract-ocr-eng")
    elif system == "Darwin":
        pr(YELLOW, "Installing Tesseract via Homebrew…")
        run("brew install tesseract")
        run("brew install tesseract-lang")
    elif system == "Windows":
        pr(YELLOW, "⚠  Windows detected.")
        pr(YELLOW, "  Please download Tesseract from:")
        pr(CYAN,   "  https://github.com/UB-Mannheim/tesseract/wiki")
        pr(YELLOW, "  After installing, add to PATH:")
        pr(CYAN,   "  C:\\Program Files\\Tesseract-OCR\\")
        pr(YELLOW, "  Then re-run this script to verify.")
    else:
        pr(RED, f"Unknown OS: {system} — install Tesseract manually")

# ── 3. Verify Tesseract + Arabic data ─────────────────────────────────────────
pr(BOLD, "\n[3/3] Verifying OCR languages…")
try:
    import pytesseract
    langs = pytesseract.get_languages()
    pr(GREEN, f"✓  Tesseract languages: {', '.join(langs)}")
    if "ara" not in langs:
        pr(YELLOW, "⚠  Arabic language data (ara) not found.")
        pr(YELLOW, "  Ubuntu/Debian: sudo apt install tesseract-ocr-ara")
        pr(YELLOW, "  macOS:         brew install tesseract-lang")
        pr(YELLOW, "  Windows:       re-run installer with Arabic selected")
    else:
        pr(GREEN, "✓  Arabic OCR data present")
except Exception as e:
    pr(RED, f"✗  Tesseract check failed: {e}")

# ── Summary ───────────────────────────────────────────────────────────────────
pr(BOLD+GREEN, "\n══════════════════════════════════════════")
pr(BOLD+GREEN,   "  Setup complete!  ")
pr(BOLD+GREEN, "══════════════════════════════════════════")
pr(CYAN, "\nTo start the server:")
pr(BOLD, "  python app_v2.py")
pr(CYAN, "\nThen open index.html in your browser.")
print()
