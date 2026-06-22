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


# ── Phase 1: gather choices ───────────────────────────────────────────────────

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
    print("This script will install system prerequisites.")
    print("Answer the questions below, then installation will begin.")
    print()

    want_embedder = _ask(
        "[1/2] Pre-download sentence-transformers embedding model\n"
        "      (all-MiniLM-L6-v2, ~90MB)?\n"
        "      Skipping means first engine run will download it automatically."
    )
    print()

    want_ollama = _ask(
        "[2/2] Install Ollama for offline mode\n"
        "      (qwen2.5:7b + granite3-dense:8b, ~9GB)?\n"
        "      Only needed if you have NO ANTHROPIC_API_KEY, OPENAI_API_KEY, or GROQ_API_KEY."
    )
    print()

    # Plan summary
    print("─────────────────────────────────────────────")
    print("Installation plan:")
    print("  [✓] Tesseract          (required)")
    if want_embedder:
        print("  [✓] all-MiniLM-L6-v2  (you selected yes)")
    else:
        print("  [✗] all-MiniLM-L6-v2  (skipped)")
    if want_ollama:
        print("  [✓] Ollama + models    (you selected yes)")
    else:
        print("  [✗] Ollama             (skipped)")
    print()

    proceed = _ask("Proceed?", default_yes=True)
    print("─────────────────────────────────────────────")

    return {
        "want_embedder": want_embedder,
        "want_ollama": want_ollama,
        "proceed": proceed,
    }


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

def download_embedder() -> None:
    print()
    print("Pre-downloading sentence-transformers model (all-MiniLM-L6-v2)...")
    try:
        from sentence_transformers import SentenceTransformer  # type: ignore
        SentenceTransformer("all-MiniLM-L6-v2")
        ok("all-MiniLM-L6-v2 downloaded")
    except ImportError:
        warn(
            "sentence-transformers not installed yet — "
            "run pip install -r requirements.txt first, then re-run setup.py"
        )


# ── Ollama ────────────────────────────────────────────────────────────────────

_OLLAMA_MODELS = ["qwen2.5:7b", "granite3-dense:8b"]
# Hard constraint: never pull any Llama variant — non-Apache 2.0 license.
_LLAMA_BLOCKLIST = ["llama", "llama3", "llama3.3", "llama-3"]


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

    if not choices["proceed"]:
        print("Aborted — nothing installed.")
        sys.exit(0)

    os_name = _os()

    install_tesseract(os_name)

    if choices["want_embedder"]:
        download_embedder()

    if choices["want_ollama"]:
        install_ollama(os_name)

    print()
    print("Setup complete. Next step: cp .env.example .env and fill in your API keys.")


if __name__ == "__main__":
    main()
