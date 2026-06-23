"""
RDTII Extraction Engine — system prerequisite installer.

Run once before `pip install -r requirements.txt`:
    python setup.py

Installs: Tesseract (required), sentence-transformers model (optional),
          Ollama + offline LLM models (optional).

Never pulls any Llama model variant — Meta license is not Apache 2.0.
"""

from __future__ import annotations

import platform
import shutil
import subprocess
import sys


# ── Output helpers ────────────────────────────────────────────────────────────

def ok(msg: str) -> None:
    print(f"[✓] {msg}")

def warn(msg: str) -> None:
    print(f"[!] {msg}")

def fail(msg: str) -> None:
    print(f"[✗] {msg}")


# ── OS detection ──────────────────────────────────────────────────────────────

def _os() -> str:
    """Return 'macos', 'linux', or 'windows'."""
    s = platform.system()
    if s == "Darwin":
        return "macos"
    if s == "Linux":
        return "linux"
    return "windows"


# ── Constants ────────────────────────────────────────────────────────────────

_OLLAMA_MODELS = ["qwen2.5:7b", "granite3-dense:8b"]
# Hard constraint: never pull any Llama variant — non-Apache 2.0 license.
_LLAMA_BLOCKLIST = ["llama", "llama3", "llama3.3", "llama-3"]


# ── Phase 1: pre-flight detection ────────────────────────────────────────────

def _detect_tesseract() -> bool:
    return bool(shutil.which("tesseract"))


def _detect_playwright() -> bool:
    """True if Chromium binary is already downloaded by Playwright."""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "playwright", "install", "--dry-run", "chromium"],
            capture_output=True, text=True, check=False,
        )
        return "already installed" in (result.stdout + result.stderr).lower()
    except Exception:
        return False


def _detect_embedder() -> bool:
    import os
    cache_dir = os.path.join(
        os.path.expanduser("~"), ".cache", "torch", "sentence_transformers",
        "sentence-transformers_all-MiniLM-L6-v2",
    )
    return os.path.isdir(cache_dir)


def _detect_ollama() -> bool:
    if not shutil.which("ollama"):
        return False
    result = subprocess.run(
        ["ollama", "list"], capture_output=True, text=True, check=False
    )
    listing = result.stdout
    return all(m.split(":")[0] in listing for m in _OLLAMA_MODELS)


def _ask(prompt: str, default_yes: bool = False) -> bool:
    hint = "[Y/n]" if default_yes else "[y/N]"
    raw = input(f"{prompt} {hint}: ").strip().lower()
    if raw == "":
        return default_yes
    return raw in ("y", "yes")


def gather_choices() -> dict:
    print()
    print("=============================================")
    print(" RDTII Extraction Engine — Setup")
    print("=============================================")
    print("Scanning for already-installed components...")
    print()

    already_tesseract  = _detect_tesseract()
    already_playwright = _detect_playwright()
    already_embedder   = _detect_embedder()
    already_ollama     = _detect_ollama()

    # Show what was found
    print("Current status:")
    print(f"  {'[✓]' if already_tesseract  else '[ ]'} Tesseract")
    print(f"  {'[✓]' if already_playwright else '[ ]'} Playwright Chromium")
    print(f"  {'[✓]' if already_embedder   else '[ ]'} Embedding model (all-MiniLM-L6-v2)")
    print(f"  {'[✓]' if already_ollama     else '[ ]'} Ollama + models")
    print()

    # Only ask about what is missing
    q_num = 0

    # Required components — no questions, just inform if they will be installed
    if not already_tesseract:
        print("[!] Tesseract is required and will be installed automatically.")
        print()
    if not already_playwright:
        print("[!] Playwright Chromium is required and will be installed automatically.")
        print()

    want_playwright = True  # always install if missing, no prompt

    want_embedder = already_embedder
    if not already_embedder:
        q_num += 1
        want_embedder = _ask(
            f"[{q_num}] Pre-download sentence-transformers embedding model\n"
            "      (all-MiniLM-L6-v2, ~90MB)?\n"
            "      Skipping means first engine run will download it automatically."
        )
        print()

    want_ollama = already_ollama
    if not already_ollama:
        q_num += 1
        want_ollama = _ask(
            f"[{q_num}] Install Ollama for offline mode\n"
            "      (qwen2.5:7b + granite3-dense:8b, ~9GB)?\n"
            "      Only needed if you have NO ANTHROPIC_API_KEY, OPENAI_API_KEY, or GROQ_API_KEY."
        )
        print()

    if q_num == 0:
        print("All components are already installed. Nothing to do.")
        return {
            "want_embedder": False,
            "want_ollama": False,
            "want_playwright": False,
            "proceed": False,
            "all_done": True,
        }

    # Plan summary
    print("─────────────────────────────────────────────")
    print("Installation plan:")
    _plan_line("Tesseract            (required)", not already_tesseract, already_tesseract)
    _plan_line("Playwright Chromium  (required)", not already_playwright, already_playwright)
    _plan_line("all-MiniLM-L6-v2    (optional)", want_embedder, already_embedder)
    _plan_line("Ollama + models      (optional)", want_ollama, already_ollama)
    print()

    proceed = _ask("Proceed?", default_yes=True)
    print("─────────────────────────────────────────────")

    return {
        "want_embedder": want_embedder and not already_embedder,
        "want_ollama": want_ollama and not already_ollama,
        "want_playwright": want_playwright and not already_playwright,
        "install_tesseract": not already_tesseract,
        "proceed": proceed,
        "all_done": False,
    }


def _plan_line(label: str, will_install: bool, already_done: bool) -> None:
    if already_done:
        print(f"  [✓] {label:<25} already installed")
    elif will_install:
        print(f"  [→] {label:<25} will install")
    else:
        print(f"  [✗] {label:<25} skipped")


# ── Phase 2: execution ────────────────────────────────────────────────────────

def _run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=check)


# ── Tesseract ─────────────────────────────────────────────────────────────────

def install_tesseract(os_name: str) -> None:
    print()
    print("Installing Tesseract...")

    if os_name == "windows":
        print()
        warn("Windows detected. Tesseract must be installed manually.")
        print("     Download the installer from:")
        print("     https://github.com/UB-Mannheim/tesseract/wiki")
        print("     After install, add the Tesseract directory to your PATH.")
        return

    if shutil.which("tesseract"):
        # Already present — just verify version
        _verify_tesseract()
        return

    if os_name == "macos":
        if not shutil.which("brew"):
            fail("Homebrew not found. Install it from https://brew.sh, then re-run setup.py")
            sys.exit(1)
        _run(["brew", "install", "tesseract"])
    elif os_name == "linux":
        warn("This step requires sudo to install Tesseract via apt-get.")
        _run(["sudo", "apt-get", "install", "-y", "tesseract-ocr"])

    _verify_tesseract()


def _verify_tesseract() -> None:
    binary = shutil.which("tesseract")
    if not binary:
        fail("Tesseract install failed — see error above")
        sys.exit(1)

    result = subprocess.run(
        ["tesseract", "--version"],
        capture_output=True, text=True, check=False
    )
    output = (result.stdout + result.stderr).strip()
    version_line = output.splitlines()[0] if output else ""
    # e.g. "tesseract 5.3.4"
    try:
        parts = version_line.split()
        major = int(parts[1].split(".")[0]) if len(parts) > 1 else 0
    except (ValueError, IndexError):
        major = 0

    if major < 5:
        warn(f"Tesseract < 5 detected. Recommended: 5.3+. Continuing...")
    else:
        ok(f"{version_line} installed")


# ── sentence-transformers model ───────────────────────────────────────────────

# ── Playwright ────────────────────────────────────────────────────────────────

def install_playwright() -> None:
    print()
    print("Installing Playwright Chromium browser...")

    # Check if playwright package is available
    try:
        from playwright.sync_api import sync_playwright  # type: ignore  # noqa: F401
    except ImportError:
        warn(
            "playwright package not installed yet — "
            "run pip install -r requirements.txt first, then re-run setup.py"
        )
        return

    # Check if Chromium binary already exists
    try:
        import subprocess as sp
        result = sp.run(
            [sys.executable, "-m", "playwright", "install", "--dry-run", "chromium"],
            capture_output=True, text=True, check=False
        )
        # If dry-run output says "chromium" is already installed, skip
        if "already installed" in (result.stdout + result.stderr).lower():
            ok("Playwright Chromium already installed — skipping")
            return
    except Exception:
        pass  # dry-run not supported in all versions — proceed with install

    result = subprocess.run(
        [sys.executable, "-m", "playwright", "install", "chromium"],
        check=False,
    )
    if result.returncode != 0:
        fail("Playwright Chromium install failed — see error above")
        sys.exit(1)
    ok("Playwright Chromium installed")


def download_embedder() -> None:
    print()
    print("Pre-downloading sentence-transformers model (all-MiniLM-L6-v2)...")
    try:
        from sentence_transformers import SentenceTransformer  # type: ignore

        # Check if model is already cached
        import os
        cache_dir = os.path.join(
            os.path.expanduser("~"), ".cache", "torch", "sentence_transformers",
            "sentence-transformers_all-MiniLM-L6-v2"
        )
        if os.path.isdir(cache_dir):
            ok("all-MiniLM-L6-v2 already cached — skipping download")
            return

        SentenceTransformer("all-MiniLM-L6-v2")
        ok("all-MiniLM-L6-v2 downloaded")
    except ImportError:
        warn(
            "sentence-transformers not installed yet — "
            "run pip install -r requirements.txt first, then re-run setup.py"
        )


# ── Ollama ────────────────────────────────────────────────────────────────────

def install_ollama(os_name: str) -> None:
    print()
    print("Installing Ollama...")

    if os_name == "windows":
        print()
        warn("Windows detected. Ollama must be installed manually.")
        print("     Download from: https://ollama.com/download")
        print("     After install, run: ollama pull qwen2.5:7b")
        print("     Then run:           ollama pull granite3-dense:8b")
        return

    if not shutil.which("ollama"):
        if os_name == "macos":
            if not shutil.which("brew"):
                fail("Homebrew not found. Install from https://brew.sh, then re-run.")
                sys.exit(1)
            _run(["brew", "install", "ollama"])
        elif os_name == "linux":
            _run(["sh", "-c", "curl -fsSL https://ollama.com/install.sh | sh"])
        ok("Ollama installed")
    else:
        ok("Ollama already installed")

    _ensure_ollama_running()
    _pull_ollama_models()


def _ensure_ollama_running() -> None:
    result = subprocess.run(
        ["ollama", "list"], capture_output=True, check=False
    )
    if result.returncode != 0:
        print("  Starting ollama serve in background...")
        subprocess.Popen(
            ["ollama", "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        import time
        time.sleep(3)


def _pull_ollama_models() -> None:
    for model in _OLLAMA_MODELS:
        # Safety guard — should never be needed, but be explicit
        assert not any(blocked in model for blocked in _LLAMA_BLOCKLIST), (
            f"Blocked model: {model} — Llama variants are not Apache 2.0"
        )
        print(f"  Pulling {model} (this may take several minutes)...")
        result = subprocess.run(["ollama", "pull", model], check=False)
        if result.returncode != 0:
            fail(f"Failed to pull {model} — see error above")
            sys.exit(1)
        ok(f"{model} pulled")

    # Verify both appear in ollama list
    result = subprocess.run(
        ["ollama", "list"], capture_output=True, text=True, check=False
    )
    listing = result.stdout
    for model in _OLLAMA_MODELS:
        base = model.split(":")[0]
        if base not in listing:
            fail(f"{model} not found in `ollama list` after pull")
            sys.exit(1)
    ok("All Ollama models verified in `ollama list`")


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    choices = gather_choices()

    if choices.get("all_done"):
        sys.exit(0)

    if not choices["proceed"]:
        print("Aborted — nothing installed.")
        sys.exit(0)

    os_name = _os()

    if choices.get("install_tesseract", True):
        install_tesseract(os_name)

    if choices["want_playwright"]:
        install_playwright()

    if choices["want_embedder"]:
        download_embedder()

    if choices["want_ollama"]:
        install_ollama(os_name)

    print()
    print("Setup complete. Next steps:")
    print("  1. cp .env.example .env  (if not done already)")
    print("  2. Fill in your API keys in .env")
    print("  3. pip install -r requirements.txt")
    print("  4. python main.py --economy Singapore --pillar 7")


if __name__ == "__main__":
    main()
