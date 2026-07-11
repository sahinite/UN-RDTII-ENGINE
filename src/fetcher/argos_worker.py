"""
Argos Translate subprocess worker — translate <source> → English in isolation.

Argos pulls in ctranslate2 / onnxruntime, whose native OpenMP/threading runtime
segfaults when it shares a process with the RAG torch/faiss stack. Running Argos
here, in a short-lived subprocess, keeps those native libs out of the main
pipeline process entirely.

Protocol: read {"text": ..., "source_lang": ...} as JSON on stdin, write
{"ok": bool, "text": str} (or {"ok": false, "error": ...}) as JSON on stdout.
"""

from __future__ import annotations

import json
import sys


def _translate(text: str, src: str) -> dict:
    import os
    from argostranslate import package, settings, translate

    # Argos's default stanza sentence-splitter has no Malay/Tagalog model; MiniSBD
    # maps those to the English splitter and works.
    settings.chunk_type = settings.ChunkType.MINISBD
    # Cap ctranslate2 threads per worker. Default (0) = all cores, which oversubscribes
    # badly when several workers run in parallel; the pool sets a per-worker share.
    intra = int(os.environ.get("ARGOS_INTRA_THREADS", "0"))
    if intra > 0:
        settings.intra_threads = intra

    installed = {(p.from_code, p.to_code) for p in package.get_installed_packages()}
    if (src, "en") not in installed:
        package.update_package_index()
        pkg = next((p for p in package.get_available_packages()
                    if p.from_code == src and p.to_code == "en"), None)
        if pkg is None:
            return {"ok": False, "error": f"no argos model for {src}->en"}
        package.install_from_path(pkg.download())

    out = translate.translate(text, src, "en")
    if not out or not out.strip():
        return {"ok": False, "error": "empty translation"}
    return {"ok": True, "text": out}


def _install(langs: list[str]) -> dict:
    """Pre-install <lang>→en models (used by the startup bootstrap)."""
    from argostranslate import package

    package.update_package_index()
    available = package.get_available_packages()
    installed = {(p.from_code, p.to_code) for p in package.get_installed_packages()}
    done = []
    for src in langs:
        if (src, "en") in installed:
            done.append(src)
            continue
        pkg = next((p for p in available if p.from_code == src and p.to_code == "en"), None)
        if pkg is not None:
            package.install_from_path(pkg.download())
            done.append(src)
    return {"ok": True, "installed": done}


def _serve() -> None:
    """Persistent server loop: load the model once, then translate one JSON request
    per stdin line, writing one JSON response line each. Keeping the process alive
    avoids reloading the ctranslate2 model (~3.5s) on every call."""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            result = _translate(req["text"], req["source_lang"])
        except Exception as exc:
            result = {"ok": False, "error": str(exc)[:300]}
        sys.stdout.write(json.dumps(result) + "\n")
        sys.stdout.flush()


def main() -> None:
    if "--serve" in sys.argv:
        _serve()
        return
    try:
        req = json.loads(sys.stdin.read())
        if req.get("langs"):                       # install-only request
            result = _install(req["langs"])
        else:
            result = _translate(req["text"], req["source_lang"])
    except Exception as exc:  # any failure → caller falls back to DeepL
        result = {"ok": False, "error": str(exc)[:300]}
    sys.stdout.write(json.dumps(result))


if __name__ == "__main__":
    main()
