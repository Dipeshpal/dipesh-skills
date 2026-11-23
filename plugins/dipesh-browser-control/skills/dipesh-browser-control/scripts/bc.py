#!/usr/bin/env python3
"""dipesh-browser-control: drive the user's own browser (or a standalone one) with Playwright.

Entry point is stdlib-only so `setup` can run before any dependency exists; every command that
needs Playwright re-executes itself inside the private venv at ~/.dipesh-browser-control/venv.

  bc setup [--scope user|project] [--yes]     detect browsers, choose profile + decision backend, install deps
  bc browsers                                  list detected browsers and profiles (JSON)
  bc profiles                                  numbered browsers/profiles (default marked) to offer the user
  bc start [--use N|TEXT [--remember]]         run the browser (default, or the one chosen for this task)
  bc use N|TEXT | resync [N|TEXT]              change the default / re-copy a real profile
  bc stop [--keep-model] | status              stop browser (+ model server) / show state
  bc open URL | back | reload                  navigate the active tab
  bc snapshot [--text N]                       page text + numbered interactive elements
  bc click REF | type REF TEXT [--enter] | select REF VALUE | toggle REF on|off
  bc press KEY [--ref REF] | scroll up|down | screenshot PATH | eval JS | tabs | tab N | wait SECONDS
  bc fill "Label=value" ...                    bulk form fill (label match, or one model call), verified
  bc ask yes|choose|rate QUESTION [OPTIONS]    model judges the page: yes/no, one of keys, ordered scale
  bc act INSTRUCTION [--value V | --key K]     model picks target+operation, executes, verifies
  bc do "intent" "intent :: value" ...         several act steps in one command, stops at the first failure
  bc auto GOAL [--value F=V] [--allow LABEL]   the model drives the whole goal; stops before risky clicks
  bc suggest GOAL                              model proposes the next step of a longer goal
  bc recall [URL|DOMAIN]                       rules + site lessons + recent failures
  bc learn --site DOMAIN --kind pitfall|recipe TEXT   |   bc learn --kind rule TEXT
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

HOME = Path(os.environ.get("DIPESH_BC_HOME", Path.home() / ".dipesh-browser-control"))
VENV = HOME / "venv"
CONFIG = HOME / "config.json"
SECRETS = HOME / "secrets.json"
STATE = HOME / "daemon.json"
MODEL_STATE = HOME / "openjev.json"   # {pid, model} of the model server we started
PROJECT_DIR = ".dipesh-browser-control"
SYSTEM = {"linux": "Linux", "darwin": "Darwin", "win32": "Windows"}.get(sys.platform, "Linux")

JEV_URL = "https://api.typesafe.ai/v1/systemone"
OPENJEV_PORT = 8009
MODELS = {
    "openjev-1.7b": ("alanhuangya/OpenJev-1.7B", "3.5 GB, laptops / CPU / small GPUs, fastest"),
    "openjev-4b": ("alanhuangya/OpenJev-4B", "8.4 GB, best accuracy, wants a GPU"),
    "kev-0.6b": ("arbazsiddiqui/kev-0.6b-browser-use", "~1.5 GB, tiny and fastest (~80 ms), browser steps only, less accurate"),
}
KEV_BASE = "jaredpalmer/kev-0.6b"   # the kev-0.6b adapter sits on this base model
KEV_PKG = "kev[serve] @ git+https://github.com/jaredpalmer/kev"   # not on PyPI (the `kev` there is another project)


def is_local(backend):
    return backend in MODELS


def model_packages(backend):
    return [KEV_PKG] if backend.startswith("kev") else ["openjev-ai[serve]"]


def model_repos(backend):
    return [MODELS[backend][0], KEV_BASE] if backend.startswith("kev") else [MODELS[backend][0]]


def server_cmd(backend):
    if backend.startswith("kev"):  # truncate rather than 422 on states past Kev's 384-token limit
        return [str(venv_python()), "-m", "kev.serve", "--run", MODELS[backend][0], "--port", str(OPENJEV_PORT)]
    wev = VENV / ("Scripts/wev.exe" if SYSTEM == "Windows" else "bin/wev")
    return [str(wev), "serve", "--model", MODELS[backend][0], "--port", str(OPENJEV_PORT)]
CACHE_DIRS = {"Cache", "Code Cache", "GPUCache", "DawnCache", "DawnGraphiteCache", "DawnWebGPUCache",
              "GrShaderCache", "ShaderCache", "Service Worker", "CacheStorage", "Crashpad", "component_crx_cache",
              "optimization_guide_model_store", "Safe Browsing", "cache2", "startupCache"}


# ---------------------------------------------------------------- small helpers

def load_json(path, default):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return default


def save_json(path, data, private=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")
    if private:
        path.chmod(0o600)


def die(msg, code=1):
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(code)


def venv_python():
    return VENV / ("Scripts/python.exe" if SYSTEM == "Windows" else "bin/python")


def in_venv():
    return Path(sys.prefix).resolve() == VENV.resolve()


def reexec_in_venv():
    if in_venv():
        return
    py = venv_python()
    if not py.exists():
        die("browser control is not set up yet. Run: bc setup")
    os.execv(str(py), [str(py), str(Path(__file__).resolve()), *sys.argv[1:]])


def domain_of(url_or_domain):
    s = url_or_domain.strip()
    host = urllib.parse.urlparse(s if "://" in s else "https://" + s).hostname or s
    return host.removeprefix("www.").lower()


# ---------------------------------------------------------------- menus (arrow keys on a tty, numbers otherwise)

def _tty_menu(title, options, multi=False, default_on=True):
    import termios
    import tty
    fd = sys.stdin.fileno()
    cur, on = 0, [default_on] * len(options)
    hint = "↑/↓ move · Space toggle · a all/none · Enter confirm" if multi else "↑/↓ move · Enter select"
    out = sys.stdout
    out.write(title + "\n\x1b[?25l")
    old = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        while True:
            for i, o in enumerate(options):
                box = ("[x] " if on[i] else "[ ] ") if multi else f"{i + 1}. "
                line = f"❯ {box}{o}" if i == cur else f"  {box}{o}"
                out.write("\x1b[2K" + (f"\x1b[36m{line}\x1b[0m" if i == cur else line) + "\n")
            out.write(f"\x1b[2K\x1b[2m  {hint}\x1b[0m\n")
            out.flush()
            k = os.read(fd, 3)
            if k in (b"\x1b[A", b"k"):
                cur = (cur - 1) % len(options)
            elif k in (b"\x1b[B", b"j"):
                cur = (cur + 1) % len(options)
            elif k in (b"\r", b"\n"):
                break
            elif multi and k == b" ":
                on[cur] = not on[cur]
            elif multi and k == b"a":
                on = [not all(on)] * len(options)
            elif not multi and k.isdigit() and 1 <= int(k) <= len(options):
                cur = int(k) - 1
            elif k == b"\x03":
                raise KeyboardInterrupt
            out.write(f"\x1b[{len(options) + 1}A")
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        out.write("\x1b[?25h\n")
    return [i for i, v in enumerate(on) if v] if multi else cur


def menu(title, options, multi=False, default_on=True):
    if sys.stdin.isatty() and SYSTEM != "Windows":
        return _tty_menu(title, options, multi, default_on)
    print(title)
    for i, o in enumerate(options):
        print(f"  {i + 1}. {o}")
    reply = input("Numbers, space-separated: " if multi else "Choice [1]: ").strip()
    if multi:
        return [int(n) - 1 for n in reply.split() if n.isdigit() and 0 < int(n) <= len(options)] if reply else \
            (list(range(len(options))) if default_on else [])
    return int(reply) - 1 if reply.isdigit() and 0 < int(reply) <= len(options) else 0


def ask(prompt, default=""):
    reply = input(f"{prompt}{f' [{default}]' if default else ''}: ").strip()
    return reply or default


def yes_no(prompt, default=True, assume=None):
    if assume is not None:
        return assume
    return menu(prompt, ["Yes", "No"]) == 0 if default else menu(prompt, ["No", "Yes"]) == 1


# ---------------------------------------------------------------- browser detection

def _chromium_families():
    h = Path.home()
    if SYSTEM == "Linux":
        cfg = Path(os.environ.get("XDG_CONFIG_HOME", h / ".config"))
        return [
            ("chrome", "Google Chrome", [cfg / "google-chrome"], ["google-chrome", "google-chrome-stable"]),
            ("chrome-beta", "Google Chrome Beta", [cfg / "google-chrome-beta"], ["google-chrome-beta"]),
            ("chromium", "Chromium", [cfg / "chromium", h / "snap/chromium/common/chromium"],
             ["chromium", "chromium-browser"]),
            ("edge", "Microsoft Edge", [cfg / "microsoft-edge"], ["microsoft-edge", "microsoft-edge-stable"]),
            ("brave", "Brave", [cfg / "BraveSoftware/Brave-Browser"], ["brave-browser", "brave"]),
            ("vivaldi", "Vivaldi", [cfg / "vivaldi"], ["vivaldi", "vivaldi-stable"]),
            ("opera", "Opera", [cfg / "opera"], ["opera"]),
        ]
    if SYSTEM == "Darwin":
        sup = h / "Library/Application Support"
        app = lambda n, b: [f"/Applications/{n}.app/Contents/MacOS/{b}", str(h / f"Applications/{n}.app/Contents/MacOS/{b}")]
        return [
            ("chrome", "Google Chrome", [sup / "Google/Chrome"], app("Google Chrome", "Google Chrome")),
            ("chromium", "Chromium", [sup / "Chromium"], app("Chromium", "Chromium")),
            ("edge", "Microsoft Edge", [sup / "Microsoft Edge"], app("Microsoft Edge", "Microsoft Edge")),
            ("brave", "Brave", [sup / "BraveSoftware/Brave-Browser"], app("Brave Browser", "Brave Browser")),
            ("vivaldi", "Vivaldi", [sup / "Vivaldi"], app("Vivaldi", "Vivaldi")),
            ("arc", "Arc", [sup / "Arc/User Data"], app("Arc", "Arc")),
            ("opera", "Opera", [sup / "com.operasoftware.Opera"], app("Opera", "Opera")),
        ]
    local = Path(os.environ.get("LOCALAPPDATA", h / "AppData/Local"))
    roaming = Path(os.environ.get("APPDATA", h / "AppData/Roaming"))
    pf = [Path(os.environ.get(v, d)) for v, d in
          (("PROGRAMFILES", "C:/Program Files"), ("PROGRAMFILES(X86)", "C:/Program Files (x86)"))] + [local]
    exe = lambda rel: [str(p / rel) for p in pf]
    return [
        ("chrome", "Google Chrome", [local / "Google/Chrome/User Data"], exe("Google/Chrome/Application/chrome.exe")),
        ("chromium", "Chromium", [local / "Chromium/User Data"], exe("Chromium/Application/chrome.exe")),
        ("edge", "Microsoft Edge", [local / "Microsoft/Edge/User Data"], exe("Microsoft/Edge/Application/msedge.exe")),
        ("brave", "Brave", [local / "BraveSoftware/Brave-Browser/User Data"],
         exe("BraveSoftware/Brave-Browser/Application/brave.exe")),
        ("vivaldi", "Vivaldi", [local / "Vivaldi/User Data"], exe("Vivaldi/Application/vivaldi.exe")),
        ("opera", "Opera", [roaming / "Opera Software/Opera Stable"], exe("Opera/opera.exe")),
    ]


def _find_exe(candidates):
    for c in candidates:
        if os.sep in c or "/" in c:
            if Path(c).is_file():
                return c
        elif shutil.which(c):
            return shutil.which(c)
    return None


def _firefox_roots():
    h = Path.home()
    if SYSTEM == "Linux":
        return [h / ".mozilla/firefox", h / "snap/firefox/common/.mozilla/firefox",
                h / ".var/app/org.mozilla.firefox/.mozilla/firefox"]
    if SYSTEM == "Darwin":
        return [h / "Library/Application Support/Firefox"]
    return [Path(os.environ.get("APPDATA", h / "AppData/Roaming")) / "Mozilla/Firefox"]


def detect_browsers():
    found = []
    for bid, name, data_dirs, exes in _chromium_families():
        exe = _find_exe(exes)
        for data in data_dirs:
            local_state = data / "Local State"
            if not local_state.is_file():
                continue
            cache = load_json(local_state, {}).get("profile", {}).get("info_cache", {})
            profiles = [{"dir": d, "name": i.get("name") or d, "account": i.get("user_name", "")}
                        for d, i in cache.items() if (data / d).is_dir()]
            if not profiles and (data / "Default").is_dir():
                profiles = [{"dir": "Default", "name": "Default", "account": ""}]
            found.append({"id": bid, "name": name, "engine": "chromium", "exe": exe,
                          "data_dir": str(data), "profiles": profiles})
            break
    import configparser
    for root in _firefox_roots():
        ini = root / "profiles.ini"
        if not ini.is_file():
            continue
        cp = configparser.ConfigParser()
        cp.read(ini)
        profiles = []
        for sec in cp.sections():
            if sec.startswith("Profile") and cp.has_option(sec, "Path"):
                p = cp.get(sec, "Path")
                full = root / p if cp.get(sec, "IsRelative", fallback="1") == "1" else Path(p)
                if full.is_dir():
                    profiles.append({"dir": str(full), "name": cp.get(sec, "Name", fallback=p), "account": ""})
        if profiles:
            found.append({"id": "firefox", "name": "Firefox", "engine": "firefox",
                          "exe": _find_exe(["firefox", "/Applications/Firefox.app/Contents/MacOS/firefox"]),
                          "data_dir": str(root), "profiles": profiles})
            break
    return found


# ---------------------------------------------------------------- profile cloning

def clone_target(browser_id, profile_dir):
    safe = "".join(c if c.isalnum() else "-" for c in Path(profile_dir).name)
    return HOME / "profiles" / f"{browser_id}-{safe}"


def clone_profile(browser, profile_dir):
    """Copy a profile (minus caches) to our own user-data-dir. The real profile is only read."""
    dst = clone_target(browser["id"], profile_dir)
    done = {"files": 0, "bytes": 0, "shown": 0.0}

    def copy(src, dst_file, **kw):  # copy2 + a live "files / MB" counter
        try:
            shutil.copy2(src, dst_file, **kw)
        except OSError:  # a file the running browser holds locked (Windows): skip it, the browser rebuilds it
            return dst_file
        done["files"] += 1
        done["bytes"] += os.path.getsize(dst_file)
        if time.time() - done["shown"] > 0.25:
            done["shown"] = time.time()
            print(f"\r  copied {done['files']} files, {done['bytes'] / 1e6:.0f} MB", end="", flush=True)
        return dst_file
    ignore = shutil.ignore_patterns(*CACHE_DIRS, "Singleton*", "lock", ".parentlock", "parent.lock", "*.tmp")
    if dst.exists():
        shutil.rmtree(dst)
    if browser["engine"] == "chromium":
        src_root = Path(browser["data_dir"])
        dst.mkdir(parents=True)
        shutil.copy2(src_root / "Local State", dst / "Local State")
        shutil.copytree(src_root / profile_dir, dst / profile_dir, ignore=ignore, ignore_dangling_symlinks=True,
                        copy_function=copy)
    else:
        shutil.copytree(profile_dir, dst, ignore=ignore, ignore_dangling_symlinks=True, copy_function=copy)
    print(f"\r  copied {done['files']} files, {done['bytes'] / 1e6:.0f} MB (caches skipped)")
    return dst


# ---------------------------------------------------------------- memory (learning)

def memory_dir(start=None):
    """Nearest project memory (walking up from cwd), else the user-level one."""
    p = Path(start or os.getcwd()).resolve()
    for d in (p, *p.parents):
        if (d / PROJECT_DIR / "memory").is_dir():
            return d / PROJECT_DIR / "memory", "project"
    return HOME / "memory", "user"


RULES_SEED = """# Browser-control rules (learned)

General lessons that apply to every site. One line each, newest last. Keep it short: merge or
delete rules that turn out wrong.

- Take a fresh `bc snapshot` after every navigation or click that changes the page; refs are only valid for the snapshot that produced them.
- Never submit payments, send messages, delete data or change account settings without the user's explicit go-ahead in this turn.
"""

POINTER_BEGIN = "<!-- dipesh-browser-control:begin -->"
POINTER_END = "<!-- dipesh-browser-control:end -->"


def pointer_block(mem):
    return (f"{POINTER_BEGIN}\n## Browser control memory\n"
            f"Before any browser task run `bc recall <site>` (skill: dipesh-browser-control). Lessons live in "
            f"`{mem}`: `rules.md` (all sites) and `sites/<domain>.md` (per site). After a mistake you had to "
            f"correct, or a multi-step flow that worked, record it with `bc learn` so it is not repeated.\n"
            f"{POINTER_END}\n")


def link_instruction_file(path, mem):
    path = Path(path)
    text = path.read_text() if path.exists() else ""
    block = pointer_block(mem)
    if POINTER_BEGIN in text:
        start, end = text.index(POINTER_BEGIN), text.index(POINTER_END) + len(POINTER_END)
        text = text[:start] + block.rstrip("\n") + text[end:]
    else:
        text = text + ("\n" if text and not text.endswith("\n\n") else "") + block
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def init_memory(mem):
    (mem / "sites").mkdir(parents=True, exist_ok=True)
    if not (mem / "rules.md").exists():
        (mem / "rules.md").write_text(RULES_SEED)


def log_event(mem, event):
    mem = Path(mem)
    mem.mkdir(parents=True, exist_ok=True)
    event = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), **event}
    with open(mem / "log.jsonl", "a") as f:
        f.write(json.dumps(event) + "\n")


def cmd_learn(a):
    mem, scope = memory_dir()
    init_memory(mem)
    text = " ".join(a.text).strip()
    if not text:
        die("nothing to learn: give the lesson text")
    if a.kind == "rule":
        f = mem / "rules.md"
        line = f"- {text}"
    else:
        if not a.site:
            die("--site is required for pitfall/recipe")
        d = domain_of(a.site)
        f = mem / "sites" / f"{d}.md"
        if not f.exists():
            f.write_text(f"# {d}\n\n## Pitfalls\n\n## Recipes\n")
        line = f"- {text}"
    body = f.read_text()
    if line in body:
        print(f"already known ({scope} memory): {f}")
        return
    if a.kind == "rule":
        body = body.rstrip("\n") + "\n" + line + "\n"
    else:
        head, sep, recipes = body.partition("## Recipes")
        if a.kind == "pitfall":
            head = head.rstrip("\n") + "\n" + line + "\n\n"
        else:
            recipes = recipes.rstrip("\n") + "\n" + line + "\n"
        body = head + sep + recipes
    f.write_text(body)
    log_event(mem, {"type": "learn", "kind": a.kind, "site": a.site, "text": text})
    print(f"learned ({scope} memory): {f}")


def cmd_recall(a):
    mem, scope = memory_dir()
    print(f"# memory: {mem} ({scope})\n")
    rules = mem / "rules.md"
    print(rules.read_text() if rules.exists() else "(no rules yet)\n")
    if not a.site:
        sites = sorted(p.stem for p in (mem / "sites").glob("*.md")) if (mem / "sites").is_dir() else []
        print("sites with lessons: " + (", ".join(sites) or "none"))
        return
    d = domain_of(a.site)
    parts = d.split(".")
    related = [".".join(parts[i:]) for i in range(len(parts) - 1)]  # a.b.example.com -> ..., example.com
    shown = False
    for name in related:
        f = mem / "sites" / f"{name}.md"
        if f.exists():
            print(f.read_text())
            shown = True
    if not shown:
        print(f"(no lessons for {d} yet)\n")
    fails = []
    if (mem / "log.jsonl").exists():
        for line in (mem / "log.jsonl").read_text().splitlines()[-500:]:
            e = load_json_line(line)
            if e.get("type") == "error" and any(e.get("domain", "") == r or e.get("domain", "").endswith("." + r)
                                                    for r in related):
                fails.append(e)
    if fails:
        print(f"## Recent failed actions on {d} (auto-logged — turn real lessons into `bc learn`)")
        for e in fails[-8:]:
            print(f"- {e['ts']} {e.get('action')} {e.get('args')}: {e.get('error', '')[:160]}")


def load_json_line(line):
    try:
        return json.loads(line)
    except ValueError:
        return {}


# ---------------------------------------------------------------- setup

def run(cmd, **kw):
    print("\x1b[2m$ " + " ".join(str(c) for c in cmd) + "\x1b[0m" if sys.stdout.isatty() else "$ " + " ".join(str(c) for c in cmd), flush=True)
    subprocess.run([str(c) for c in cmd], check=True, **kw)


def download_models(backend, attempts=3):
    """Big downloads drop now and then; snapshot_download resumes from the cache, so just retry."""
    for repo in model_repos(backend):
        for n in range(1, attempts + 1):
            try:
                run([venv_python(), "-c", f"from huggingface_hub import snapshot_download; snapshot_download({repo!r})"])
                break
            except subprocess.CalledProcessError:
                if n == attempts:
                    raise
                print(f"  download interrupted; retrying ({n + 1}/{attempts}), resuming where it stopped …", flush=True)
                time.sleep(5 * n)


def run_steps(steps):
    """Numbered progress for the slow part of setup: [2/5] title … ✓ 42s. Tools show their own bars."""
    t0 = time.time()
    for i, (title, fn) in enumerate(steps, 1):
        bold = ("\x1b[1m", "\x1b[0m") if sys.stdout.isatty() else ("", "")
        print(f"\n{bold[0]}[{i}/{len(steps)}] {title}{bold[1]}", flush=True)
        t = time.time()
        try:
            fn()
        except (subprocess.CalledProcessError, OSError) as e:
            die(f"step {i}/{len(steps)} failed ({e}).\nYour choices are saved; resume with: bc setup --refresh")
        print(f"  ✓ done in {time.time() - t:.0f}s", flush=True)
    print(f"\nall steps done in {(time.time() - t0) / 60:.1f} min")


def ensure_venv(need_openjev):
    if venv_python().exists():
        out = subprocess.run([str(venv_python()), "-c", "import sys;print(sys.version_info[:2]>=(3,11))"],
                             capture_output=True, text=True).stdout.strip()
        if out == "True" or not need_openjev:
            return
        shutil.rmtree(VENV)
    uv = shutil.which("uv")
    if uv:
        run([uv, "venv", "--quiet", "--python", "3.12", VENV])
        return
    if sys.version_info < (3, 11) and need_openjev:
        die("OpenJev needs Python 3.11+. Install uv (https://docs.astral.sh/uv/) or a newer Python and rerun setup.")
    run([sys.executable, "-m", "venv", VENV])


def pip_install(*pkgs):
    uv = shutil.which("uv")
    if uv:
        run([uv, "pip", "install", "--python", venv_python(), *pkgs])
    else:
        run([venv_python(), "-m", "pip", "install", "--upgrade", "--progress-bar", "on", *pkgs])


def cmd_refresh():
    """After a skill update: upgrade dependencies with the choices saved at setup. No questions."""
    cfg = load_json(CONFIG, None)
    if not cfg:
        print("dipesh-browser-control is not set up yet; nothing to refresh (run: bc setup)")
        return
    backend = cfg.get("backend", "agent")
    need_openjev = is_local(backend)
    br = cfg.get("browser", {})
    if br.get("mode") == "profile" and not Path(br["user_data_dir"]).exists():  # an interrupted first setup
        clone_profile({"id": br["id"], "engine": br["engine"], "data_dir": br["source_data_dir"]}, br["profile_dir"])
    ensure_venv(need_openjev)
    pip_install("--upgrade", "playwright>=1.47")
    engines = {"chromium"} if cfg["browser"]["mode"] == "standalone" else set()
    if cfg["browser"].get("engine") == "firefox":
        engines.add("firefox")
    for e in sorted(engines):
        run([venv_python(), "-m", "playwright", "install", e])
    if need_openjev:
        pip_install("--upgrade", *model_packages(backend))
        download_models(backend)
    if load_json(STATE, None):
        print("note: restart the browser to use the upgraded Playwright: bc stop && bc start")
    print("dependencies refreshed")


def cmd_setup(a):
    if a.refresh:
        return cmd_refresh()
    assume = True if a.yes else None
    HOME.mkdir(parents=True, exist_ok=True)
    cfg = load_json(CONFIG, {})
    print("dipesh-browser-control setup\n")

    # 1. scope of the learning memory
    scope = a.scope
    if not scope:
        scope = "user" if a.yes else ["user", "project"][menu(
            "Where should browser lessons (memory, rules) be kept?",
            ["User level, shared by every project  (~/.dipesh-browser-control/memory)",
             f"This project only                    ({Path.cwd() / PROJECT_DIR / 'memory'})"])]

    # 2. which browser / profile
    choices = browser_choices()
    labels = [c["label"] for c in choices]
    if a.browser:
        sel = match_choice(choices, a.browser)
    elif a.yes:
        sel = len(choices) - 1
    else:
        sel = menu("Which browser should the agent control by default? (you can pick another per task)", labels)
    cfg["browser"] = choices[sel]
    copy_from = None
    if cfg["browser"]["mode"] == "profile":
        print(f"\nYour real profile is never opened by automation: it is copied (without caches) to "
              f"{cfg['browser']['user_data_dir']} and the copy is used, logins included."
              + (" On Windows, Chrome 127+ encrypts cookies per app, so you may need to log in once in the copy."
                 if SYSTEM == "Windows" else "")
              + " Re-copy any time with `bc resync`.\n")
        copy_from = cfg["browser"]
    cfg["headless"] = bool(a.headless)

    # 3. decision backend
    backends = [("agent", "No extra model: the coding agent (Claude, Codex, …) reads snapshots and decides"),
                ("jev", "Jev hosted API (api.typesafe.ai) — needs an API key"),
                ("openjev-1.7b", f"OpenJev-1.7B, local — {MODELS['openjev-1.7b'][1]}"),
                ("openjev-4b", f"OpenJev-4B, local — {MODELS['openjev-4b'][1]}"),
                ("kev-0.6b", f"Kev-0.6B browser-use, local — {MODELS['kev-0.6b'][1]}")]
    if a.backend:
        backend = a.backend
    elif a.yes:
        backend = "agent"
    else:
        backend = backends[menu("Which model should suggest browser steps?", [d for _, d in backends])][0]
    cfg["backend"] = backend
    if backend == "jev":
        secrets = load_json(SECRETS, {})
        key = os.environ.get("TYPESAFE_API_KEY") or secrets.get("TYPESAFE_API_KEY") or ask("Jev / TypeSafe API key")
        if not key:
            die("an API key is required for the Jev backend")
        secrets["TYPESAFE_API_KEY"] = key
        save_json(SECRETS, secrets, private=True)
        print(f"key stored in {SECRETS} (mode 600, never inside a project)")

    # 4. everything slow, as numbered steps
    need_openjev = is_local(backend)
    engines = {"chromium"} if cfg["browser"]["mode"] == "standalone" else set()
    if cfg["browser"]["engine"] == "firefox":
        engines.add("firefox")
    steps = []
    if copy_from:
        steps.append((f"Copy {copy_from['label']} (read-only, caches skipped)", lambda: clone_for(copy_from)))
    steps.append(("Create the Python environment (" + str(VENV) + ")", lambda: ensure_venv(need_openjev)))
    steps.append(("Install Playwright", lambda: pip_install("playwright>=1.47")))
    for e in sorted(engines):
        steps.append((f"Download Playwright's {e} browser (~150 MB)", lambda e=e: run(
            [venv_python(), "-m", "playwright", "install", *(["--with-deps"] if SYSTEM == "Linux" and a.with_deps else []), e])))
    if need_openjev:
        repo, size = MODELS[backend][0], MODELS[backend][1].split(",")[0]
        name = "Kev (from GitHub)" if backend.startswith("kev") else "OpenJev"
        steps.append((f"Install {name} with PyTorch + transformers (~2-3 GB, the slowest step)",
                      lambda: pip_install(*model_packages(backend))))
        steps.append((f"Download the {repo} model ({size}, cached by Hugging Face)", lambda: download_models(backend)))
    save_json(CONFIG, cfg)  # before the slow part, so a failed download can resume with `bc setup --refresh`
    run_steps(steps)

    # 5. memory + instruction-file pointers
    mem = (Path.cwd() / PROJECT_DIR / "memory") if scope == "project" else HOME / "memory"
    init_memory(mem)
    if scope == "project":
        files = [Path.cwd() / n for n in ("CLAUDE.md", "AGENTS.md") if (Path.cwd() / n).exists()] or \
                [Path.cwd() / "AGENTS.md"]
        shown_mem = f"{PROJECT_DIR}/memory"
    else:
        files = [Path.home() / ".claude/CLAUDE.md", Path.home() / ".codex/AGENTS.md"]
        shown_mem = str(mem)
    print()
    if yes_no("Add a short pointer to the memory in " + ", ".join(str(f) for f in files)
              + " so every agent session reads it?", assume=assume):
        for f in files:
            link_instruction_file(f, shown_mem)
            print(f"linked {f}")
    save_json(CONFIG, cfg)
    print(f"\ndone. config: {CONFIG}\nmemory ({scope}): {mem}\nTry: bc start && bc open https://example.com && bc snapshot")


def browser_choices():
    """Every browser/profile the agent can drive, as config dicts: real profiles (used via a copy),
    a fresh profile per installed Chromium browser, and Playwright's standalone Chromium."""
    out = []
    browsers = detect_browsers()
    for b in browsers:
        if b["engine"] == "chromium" and not b["exe"]:
            continue
        for p in b["profiles"]:
            acct = f" — {p['account']}" if p["account"] else ""
            note = "  (best effort: Playwright's Firefox build)" if b["engine"] == "firefox" else ""
            out.append({"mode": "profile", "engine": b["engine"], "exe": b["exe"], "id": b["id"],
                        "label": f"{b['name']}: profile \"{p['name']}\"{acct}{note}",
                        "source_data_dir": b["data_dir"], "profile_dir": p["dir"],
                        "user_data_dir": str(clone_target(b["id"], p["dir"]))})
    for b in browsers:
        if b["engine"] == "chromium" and b["exe"]:
            out.append({"mode": "fresh", "engine": "chromium", "exe": b["exe"], "id": b["id"],
                        "label": f"{b['name']}: fresh separate profile (no logins)",
                        "user_data_dir": str(HOME / "profiles" / f"{b['id']}-fresh")})
    out.append({"mode": "standalone", "engine": "chromium",
                "label": "Standalone Playwright Chromium (fresh profile, nothing from your browsers)"})
    return out


def match_choice(choices, spec):
    """A number from `bc profiles`, or any text that matches one label (e.g. 'brave', 'chrome dipesh')."""
    if str(spec).isdigit() and 1 <= int(spec) <= len(choices):
        return int(spec) - 1
    words = _norm(spec).split()
    hits = [i for i, c in enumerate(choices) if all(w in _norm(c["label"]) for w in words)]
    if len(hits) == 1:
        return hits[0]
    listing = "\n".join(f"  {i + 1}. {c['label']}" for i, c in enumerate(choices))
    die(f"{'no' if not hits else 'more than one'} browser matches {spec!r}. Options:\n{listing}")


def clone_for(br):
    if br.get("mode") == "profile":
        clone_profile({"id": br["id"], "engine": br["engine"], "data_dir": br["source_data_dir"]}, br["profile_dir"])


def same_browser(x, y):
    return bool(x and y) and (x.get("mode"), x.get("user_data_dir")) == (y.get("mode"), y.get("user_data_dir"))


def cmd_profiles(a):
    """Numbered list for the agent to show the user at the start of a browser task."""
    cfg = load_json(CONFIG, {})
    default = cfg.get("browser")
    running = (load_json(STATE, None) or {}).get("browser")
    choices = browser_choices()
    if a.json:
        print(json.dumps([{**c, "n": i + 1, "default": same_browser(c, default), "running": same_browser(c, running),
                           "copied": c.get("mode") == "profile" and Path(c["user_data_dir"]).exists()}
                          for i, c in enumerate(choices)], indent=2))
        return
    print("Browsers and profiles on this machine (bc start --use N):")
    for i, c in enumerate(choices):
        tags = [t for t, on in (("default", same_browser(c, default)), ("running now", same_browser(c, running))) if on]
        if c.get("mode") == "profile" and not Path(c["user_data_dir"]).exists():
            tags.append("first use copies it")
        print(f"  {i + 1}. {c['label']}" + (f"   ← {', '.join(tags)}" if tags else ""))


def cmd_use(a):
    """Make a choice the default for future tasks (copies a profile the first time)."""
    cfg = load_json(CONFIG, None) or die("not set up yet. Run: bc setup")
    choices = browser_choices()
    br = choices[match_choice(choices, a.choice)]
    if br.get("mode") == "profile" and not Path(br["user_data_dir"]).exists():
        print(f"copying {br['label']} …")
        clone_for(br)
    cfg["browser"] = br
    save_json(CONFIG, cfg)
    print(f"default browser: {br['label']}")


def cmd_resync(a):
    cfg = load_json(CONFIG, {})
    br = cfg.get("browser", {})
    if a.choice:
        choices = browser_choices()
        br = choices[match_choice(choices, a.choice)]
    if br.get("mode") != "profile":
        die("resync only applies to a browser profile (it re-copies your real profile)")
    if same_browser((load_json(STATE, None) or {}).get("browser"), br) and call("ping", quiet_fail=True):
        die("that profile is open right now: bc stop first")
    clone_for(br)
    print(f"re-copied {br['label']} to {br['user_data_dir']}")


def cmd_browsers(a):
    print(json.dumps(detect_browsers(), indent=2))


# ---------------------------------------------------------------- daemon (holds Playwright between commands)

# Runs in every frame. Walks open shadow roots, stamps refs (global numbering across frames),
# and reports role, real label, group/dialog context and state — no selectors leave the page.
SNAPSHOT_JS = r"""
({start, maxText, main}) => {
  const SEL = 'a[href],button,input:not([type=hidden]),textarea,select,summary,[contenteditable=""],[contenteditable=true],' +
    '[role=button],[role=link],[role=checkbox],[role=radio],[role=tab],[role=menuitem],[role=menuitemcheckbox],' +
    '[role=menuitemradio],[role=option],[role=combobox],[role=listbox],[role=switch],[role=textbox],[role=searchbox],' +
    '[role=slider],[role=spinbutton],[role=treeitem],[onclick],[tabindex]:not([tabindex="-1"])';
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const roots = [];
  const collect = root => { roots.push(root); for (const el of root.querySelectorAll('*')) if (el.shadowRoot) collect(el.shadowRoot); };
  collect(document);
  for (const r of roots) r.querySelectorAll('[data-bc-ref]').forEach(e => e.removeAttribute('data-bc-ref'));
  const roleOf = el => {
    const r = el.getAttribute('role'); if (r) return r;
    const tag = el.tagName.toLowerCase(), t = (el.getAttribute('type') || 'text').toLowerCase();
    if (tag === 'a') return 'link';
    if (tag === 'button' || tag === 'summary') return 'button';
    if (tag === 'textarea' || el.isContentEditable) return 'textbox';
    if (tag === 'select') return 'combobox';
    if (tag === 'input') {
      if (['checkbox', 'radio'].includes(t)) return t;
      if (['button', 'submit', 'reset', 'image'].includes(t)) return 'button';
      if (t === 'range') return 'slider';
      if (t === 'number') return 'spinbutton';
      if (t === 'search') return 'searchbox';
      return 'textbox';
    }
    return 'generic';
  };
  const labelOf = el => {
    const by = el.getAttribute('aria-labelledby');
    if (by) { const t = clean(by.split(/\s+/).map(id => el.getRootNode().getElementById?.(id)?.innerText || '').join(' ')); if (t) return t; }
    const direct = el.getAttribute('aria-label') || (el.labels && el.labels[0] && el.labels[0].innerText);
    if (clean(direct)) return clean(direct);
    const tag = el.tagName.toLowerCase();
    const own = ['input', 'textarea', 'select'].includes(tag) ? '' : el.innerText;
    return clean(own || el.placeholder || (['button', 'submit'].includes(el.type) ? el.value : '') || el.title ||
                 el.getAttribute('alt') || el.querySelector?.('img[alt]')?.alt || el.name || '');
  };
  const contextOf = el => {
    const fs = el.closest('fieldset'), lg = fs && fs.querySelector('legend');
    if (lg && clean(lg.innerText)) return clean(lg.innerText).slice(0, 60);
    const grp = el.closest('[role=radiogroup],[role=group]');
    if (grp && (grp.getAttribute('aria-label') || grp.getAttribute('aria-labelledby'))) return clean(labelOf(grp)).slice(0, 60);
    const dlg = el.closest('dialog,[role=dialog],[role=alertdialog],[aria-modal=true]');
    if (dlg) return 'dialog: ' + clean(dlg.getAttribute('aria-label') || dlg.querySelector('h1,h2,h3')?.innerText || '').slice(0, 50);
    return undefined;
  };
  const out = []; let n = start;
  for (const root of roots) for (const el of root.querySelectorAll(SEL)) {
    const r = el.getBoundingClientRect(), st = getComputedStyle(el);
    if (r.width < 1 || r.height < 1 || st.visibility === 'hidden' || st.display === 'none') {
      // styled checkbox/radio: the real input is hidden behind a visible label; keep it, clicks go to the label
      if (!(['checkbox', 'radio'].includes(el.type) && el.labels && el.labels.length)) continue;
    }
    el.setAttribute('data-bc-ref', String(++n));
    const tag = el.tagName.toLowerCase(), role = roleOf(el);
    const item = {ref: n, tag, role, label: labelOf(el).slice(0, 120)};
    const ctx = contextOf(el); if (ctx) item.context = ctx;
    if (tag === 'a' && el.getAttribute('href')) item.href = el.getAttribute('href').slice(0, 120);
    if (['textbox', 'searchbox', 'spinbutton', 'combobox', 'slider'].includes(role) && tag !== 'select')
      item.value = (el.isContentEditable ? el.innerText : (el.value || '')).slice(0, 80);
    if (tag === 'input' && el.type && !['text', 'checkbox', 'radio', 'submit', 'button'].includes(el.type)) item.input = el.type;
    if (tag === 'select') { item.value = el.selectedOptions[0]?.label || ''; item.options = [...el.options].slice(0, 40).map(o => o.label); }
    const state = [];
    if (el.disabled || el.getAttribute('aria-disabled') === 'true') state.push('disabled');
    if ('checked' in el && ['checkbox', 'radio'].includes(el.type)) state.push(el.checked ? 'checked' : 'unchecked');
    else if (el.hasAttribute('aria-checked')) state.push(el.getAttribute('aria-checked') === 'true' ? 'checked' : 'unchecked');
    if (el.hasAttribute('aria-expanded')) state.push(el.getAttribute('aria-expanded') === 'true' ? 'expanded' : 'collapsed');
    if (el.getAttribute('aria-selected') === 'true') state.push('selected');
    if (el.required || el.getAttribute('aria-required') === 'true') state.push('required');
    if (el.getAttribute('aria-invalid') === 'true' || (el.matches && el.matches('input:invalid,textarea:invalid,select:invalid') && el.value)) state.push('invalid');
    if (state.length) item.state = state;
    if (r.bottom < 0 || r.top > innerHeight) item.offscreen = true;
    out.push(item);
    if (n - start >= 400) break;
  }
  let text = '';
  if (main) {
    const m = document.querySelector('main,[role=main],article');
    const src = (m && m.innerText.length > 200) ? m : document.body;
    text = (src ? src.innerText : '').replace(/\n{3,}/g, '\n\n');
    const alerts = [...document.querySelectorAll('[role=alert],[role=status],[aria-live=assertive]')].map(a => clean(a.innerText)).filter(Boolean);
    if (alerts.length) text = 'ALERTS: ' + alerts.join(' | ') + '\n\n' + text;
  }
  return {url: location.href, title: document.title, text: text.slice(0, maxText), elements: out};
}
"""

IDLE_JS = r"""() => {
  if (!window.__bcObs) {
    window.__bcLast = performance.now();
    window.__bcObs = new MutationObserver(() => { window.__bcLast = performance.now(); });
    window.__bcObs.observe(document, {subtree: true, childList: true, attributes: true, characterData: true});
    return 0;
  }
  return performance.now() - window.__bcLast;
}"""

FIELD_ROLES = {"textbox", "searchbox", "spinbutton", "combobox", "listbox", "checkbox", "radio", "switch", "slider"}
TRUE_WORDS = {"true", "yes", "on", "1", "checked", "y"}
FALSE_WORDS = {"false", "no", "off", "0", "unchecked", "n"}


class Daemon:
    def __init__(self, cfg):
        from playwright.sync_api import sync_playwright
        self.cfg = cfg
        self.pw = sync_playwright().start()
        br = json.loads(os.environ["BC_BROWSER"]) if os.environ.get("BC_BROWSER") else \
            cfg.get("browser", {"mode": "standalone", "engine": "chromium"})
        self.br = br
        engine = getattr(self.pw, br.get("engine", "chromium"))
        headless = cfg.get("headless", False)
        if br["mode"] == "standalone":
            self.browser = engine.launch(headless=headless)
            self.ctx = self.browser.new_context(viewport=None)
        else:
            kw = {"headless": headless, "viewport": None}
            if br.get("engine") == "chromium":
                kw["executable_path"] = br["exe"]
                # Playwright's defaults --password-store=basic / --use-mock-keychain hide the OS keyring, so the
                # browser can't decrypt the profile's cookies (every login looks gone). Keep extensions too.
                kw["ignore_default_args"] = ["--enable-automation", "--no-sandbox", "--password-store=basic",
                                             "--use-mock-keychain", "--disable-extensions"]
                if br["mode"] == "profile":
                    kw["args"] = [f"--profile-directory={Path(br['profile_dir']).name}"]
            self.ctx = engine.launch_persistent_context(br["user_data_dir"], **kw)
        self.page = self.ctx.pages[0] if self.ctx.pages else self.ctx.new_page()
        self.ctx.on("page", self._on_page)
        self.refs = {}  # ref -> frame that owns it (refs are global across frames)
        self.inflight = {}  # request -> (path, start time), for settle()
        self.seen = {}  # path -> recent start times; a repeating path is background polling
        self.conns = {}  # (scheme, host) -> keep-alive connection to the decision model
        self.ctx.on("request", self._track)
        self.ctx.on("requestfinished", lambda r: self._track(r, True))
        self.ctx.on("requestfailed", lambda r: self._track(r, True))

    def _on_page(self, page):
        self.page = page
        self.refs = {}

    def settle(self, cap=2.5, min_wait=0.0):
        """Ready = DOM unchanged for 250 ms and no one-off xhr/fetch still pending (started < 1.5 s ago). URL paths
        requested again and again (polling, analytics beacons) are background noise and don't count.
        networkidle never happens on apps like Jira, which made every action cost 2.5 s."""
        try:
            self.page.wait_for_load_state("domcontentloaded", timeout=8000)
        except Exception:
            pass
        t0 = time.time()
        while time.time() - t0 < cap:
            now = time.time()
            busy = any(now - ts < 1.5 and not self._polling(path, now) for path, ts in self.inflight.values())
            try:
                idle_ms = self.page.evaluate(IDLE_JS)
            except Exception:  # navigating: the old document is gone
                idle_ms = 0
            if not busy and idle_ms >= 250 and now - t0 >= min_wait:
                return
            time.sleep(0.05)

    def _track(self, req, done=False):
        if req.resource_type in ("document", "xhr", "fetch"):
            if done:
                self.inflight.pop(req, None)
                return
            now, path = time.time(), urllib.parse.urlsplit(req.url)._replace(query="", fragment="").geturl()
            self.inflight[req] = (path, now)
            seen = [t for t in self.seen.get(path, []) if now - t < 3] + [now]
            self.seen[path] = seen

    def _polling(self, path, now):
        return len([t for t in self.seen.get(path, []) if now - t < 3]) >= 2

    def model_post(self, url, headers, body, timeout=30, retries=2):
        """Decision-model calls go through the daemon so they reuse one keep-alive HTTPS connection
        (saves the TLS handshake on every call). Retries network errors, 408, 429, 5xx; honours Retry-After."""
        import http.client
        u = urllib.parse.urlsplit(url)
        key = (u.scheme, u.netloc)
        attempt = 0
        while True:
            conn = self.conns.get(key)
            reused = conn is not None
            if conn is None:
                cls = http.client.HTTPSConnection if u.scheme == "https" else http.client.HTTPConnection
                conn = self.conns[key] = cls(u.netloc, timeout=timeout)
            try:
                conn.request("POST", u.path, body=body.encode(), headers={"content-type": "application/json", **headers})
                r = conn.getresponse()
                data = r.read()
            except (OSError, http.client.HTTPException) as e:
                conn.close()
                self.conns.pop(key, None)
                if reused:  # the server closed an idle keep-alive connection: reconnect, not a real failure
                    continue
                if attempt >= retries:
                    raise RuntimeError(f"unreachable after {retries + 1} attempts: {e}")
                time.sleep(min(0.25 * 2 ** attempt, 2))
                attempt += 1
                continue
            if r.status in (408, 429) or r.status >= 500:
                if attempt >= retries:
                    raise RuntimeError(f"HTTP {r.status}: {data[:300].decode(errors='replace')}")
                ra = r.getheader("retry-after") or ""
                time.sleep(min(float(ra), 10) if ra.replace(".", "").isdigit() else min(0.25 * 2 ** attempt, 2))
                attempt += 1
                continue
            if r.status >= 400:
                raise RuntimeError(f"HTTP {r.status}: {data[:300].decode(errors='replace')}")
            return json.loads(data)

    def snapshot(self, max_text):
        self.settle()
        main = self.page.main_frame
        snap = main.evaluate(SNAPSHOT_JS, {"start": 0, "maxText": max_text, "main": True})
        self.refs = {e["ref"]: main for e in snap["elements"]}
        n = max(self.refs, default=0)
        for frame in [f for f in self.page.frames if f is not main][:20]:
            try:
                sub = frame.evaluate(SNAPSHOT_JS, {"start": n, "maxText": 0, "main": False})
            except Exception:  # detached / cross-origin frame still loading: skip, don't stall
                continue
            for e in sub["elements"]:
                e["frame"] = (frame.url or "iframe")[:100]
                self.refs[e["ref"]] = frame
            snap["elements"] += sub["elements"]
            n = max(self.refs, default=n)
        return snap

    def loc(self, ref):
        ref = int(ref)
        frame = self.refs.get(ref)
        if frame is None or frame.is_detached():
            raise ValueError(f"ref {ref} is not on the page — take a new `bc snapshot`")
        el = frame.locator(f'[data-bc-ref="{ref}"]')
        if el.count() == 0:
            raise ValueError(f"ref {ref} is not on the page — take a new `bc snapshot`")
        return el.first

    def click(self, el):
        """Real click first. If an overlay intercepts the pointer (styled controls, sticky covers) or the element
        is visually hidden, click the element itself through the DOM: a forced click would land on the overlay."""
        try:
            el.click(timeout=5000)
            return "click"
        except Exception as e:
            msg = str(e)
            if "intercepts pointer events" not in msg and "not visible" not in msg and "Timeout" not in msg:
                raise
        el.dispatch_event("click")
        return "dom-click (pointer was intercepted)"

    def set_checked(self, el, on):
        try:
            el.set_checked(on, timeout=4000)
        except Exception:
            el.set_checked(on, force=True, timeout=3000)

    def apply(self, op, ref, value=None):
        """One deterministic operation on a ref. Used by single commands, `act` and bulk `fill`."""
        el = self.loc(ref)
        if op == "click":
            return self.click(el)
        if op == "type":
            el.fill(str(value), timeout=10000)
            return "fill"
        if op == "select":
            try:
                el.select_option(label=str(value), timeout=5000)
            except Exception:
                el.select_option(value=str(value), timeout=5000)
            return "select"
        if op in ("check", "uncheck"):
            self.set_checked(el, op == "check")
            return op
        if op == "press":
            el.press(str(value))
            return "press"
        raise ValueError(f"unknown operation {op}")

    def handle(self, cmd, args):
        p = self.page
        before = p.url
        extra = {}
        if cmd == "open":
            url = args["url"] if "://" in args["url"] else "https://" + args["url"]
            p.goto(url, wait_until="domcontentloaded", timeout=45000)
        elif cmd == "back":
            p.go_back(wait_until="domcontentloaded")
        elif cmd == "reload":
            p.reload(wait_until="domcontentloaded")
        elif cmd == "snapshot":
            return self.snapshot(args.get("text", 3000))
        elif cmd == "click":
            extra["method"] = self.apply("click", args["ref"])
        elif cmd == "type":
            self.apply("type", args["ref"], args["text"])
            if args.get("enter"):
                self.apply("press", args["ref"], "Enter")
        elif cmd == "select":
            self.apply("select", args["ref"], args["value"])
        elif cmd == "toggle":
            self.apply("check" if args["state"] == "on" else "uncheck", args["ref"])
        elif cmd == "apply":  # [{op, ref, value}] executed in order, stops at first failure
            done = []
            for step in args["steps"]:
                try:
                    out = {**step, "method": self.apply(step["op"], step["ref"], step.get("value"))}
                    if step["op"] in ("type", "select", "check", "uncheck"):
                        try:  # evidence for verification, read before anything re-renders
                            el = self.loc(step["ref"])
                            out["checked_after" if step["op"] in ("check", "uncheck") else "value_after"] = (
                                el.is_checked() if step["op"] in ("check", "uncheck") else
                                el.evaluate("e => e.tagName === 'SELECT' ? (e.selectedOptions[0]?.label || '') : "
                                            "(e.isContentEditable ? e.innerText : e.value)"))
                        except Exception:
                            pass
                    done.append(out)
                except Exception as e:
                    done.append({**step, "error": str(e).splitlines()[0][:200]})
                    break
            extra["steps"] = done
        elif cmd == "model":
            return {"answers": self.model_post(args["url"], args.get("headers", {}), args["body"],
                                               args.get("timeout", 30)).get("answers")}
        elif cmd == "press":
            if args.get("ref"):
                self.apply("press", args["ref"], args["key"])
            else:
                p.keyboard.press(args["key"])
        elif cmd == "scroll":
            p.mouse.wheel(0, -700 if args["dir"] == "up" else 700)
        elif cmd == "screenshot":
            p.screenshot(path=args["path"], full_page=args.get("full", False))
            return {"saved": args["path"]}
        elif cmd == "eval":
            return {"result": p.evaluate(args["js"])}
        elif cmd == "tabs":
            return {"tabs": [{"n": i, "url": t.url, "title": t.title(), "active": t is p}
                             for i, t in enumerate(self.ctx.pages)]}
        elif cmd == "tab":
            self.page = self.ctx.pages[int(args["n"])]
            self.page.bring_to_front()
            self.refs = {}
        elif cmd == "wait":
            p.wait_for_timeout(float(args["seconds"]) * 1000)
        elif cmd == "ping":
            return {"ok": True}
        else:
            raise ValueError(f"unknown command {cmd}")
        self.settle(min_wait=0.35 if cmd in ("click", "type", "select", "toggle", "apply", "press") else 0.0)
        return {"url": self.page.url, "title": self.page.title(), "navigated": self.page.url != before, **extra}

    def close(self):
        for f in (self.ctx.close, getattr(self, "browser", None) and self.browser.close, self.pw.stop):
            try:
                f and f()
            except Exception:
                pass


def daemon_main():
    import http.server
    import secrets as _secrets
    cfg = load_json(CONFIG, {})
    d = Daemon(cfg)
    token = _secrets.token_hex(16)

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            if self.headers.get("x-token") != token:
                self.send_response(403)
                self.end_headers()
                return
            req = json.loads(self.rfile.read(int(self.headers["content-length"])))
            cmd, args = req["cmd"], req.get("args", {})
            try:
                if cmd == "stop":
                    body, code = {"stopped": True}, 200
                else:
                    body, code = d.handle(cmd, args), 200
            except Exception as e:  # report, and auto-log for the learning memory
                msg = str(e).splitlines()[0][:400]
                body, code = {"error": msg}, 500
                if req.get("memory") and cmd not in ("ping", "snapshot", "model"):
                    try:
                        log_event(req["memory"], {"type": "error", "domain": domain_of(d.page.url or ""),
                                                  "url": d.page.url, "action": cmd, "args": args, "error": msg})
                    except Exception:
                        pass
            raw = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            if cmd == "stop":
                d.close()
                STATE.unlink(missing_ok=True)
                os._exit(0)

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    save_json(STATE, {"port": srv.server_address[1], "token": token, "pid": os.getpid(), "browser": d.br}, private=True)
    d.ctx.on("close", lambda *_: (STATE.unlink(missing_ok=True), os._exit(0)))
    srv.serve_forever()


def _local_http(port, method, path, body=b"", headers=None, timeout=120):
    """Minimal HTTP/1.0 to 127.0.0.1 without urllib (whose import alone costs ~120 ms per bc command)."""
    import socket
    with socket.create_connection(("127.0.0.1", port), timeout=timeout) as sock:
        head = "".join(f"{k}: {v}\r\n" for k, v in (headers or {}).items())
        sock.sendall(f"{method} {path} HTTP/1.0\r\nHost: 127.0.0.1\r\nContent-Length: {len(body)}\r\n{head}\r\n".encode()
                     + body)
        chunks = []
        while True:
            c = sock.recv(65536)
            if not c:
                break
            chunks.append(c)
    raw = b"".join(chunks)
    status_line, _, rest = raw.partition(b"\r\n")
    return int(status_line.split(b" ", 2)[1]), rest.partition(b"\r\n\r\n")[2]


def call(cmd, args=None, quiet_fail=False):
    st = load_json(STATE, None)
    if not st:
        if quiet_fail:
            return None
        die("browser is not running. Run: bc start")
    body = json.dumps({"cmd": cmd, "args": args or {}, "memory": str(memory_dir()[0])}).encode()
    try:
        status, payload = _local_http(st["port"], "POST", "/", body,
                                      {"x-token": st["token"], "content-type": "application/json"})
    except OSError:
        STATE.unlink(missing_ok=True)
        if quiet_fail:
            return None
        die("browser daemon is gone (was the window closed?). Run: bc start")
    if status != 200:
        err = load_json_line(payload.decode(errors="replace")).get("error", f"HTTP {status}")
        if quiet_fail and cmd in ("ping", "stop"):
            return None
        die(err + ("\n(hint: refs change when the page changes — run `bc snapshot` again)"
                   if "Timeout" in err or "detached" in err else ""))
    return json.loads(payload)


def openjev_up():
    try:
        return _local_http(OPENJEV_PORT, "GET", "/v1/models", timeout=2)[0] == 200
    except OSError:
        return False


def _proc_cmdline(pid):
    """Command line of a running process, or '' if it is gone. Used so we only ever stop our own server
    (after a reboot the saved pid may belong to an unrelated process)."""
    try:
        if SYSTEM == "Linux":
            return Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
        if SYSTEM == "Windows":
            out = subprocess.run(["wmic", "process", "where", f"ProcessId={pid}", "get", "CommandLine"],
                                 capture_output=True, text=True).stdout
            return out.split("\n", 1)[1].strip() if "\n" in out else ""
        return subprocess.run(["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True).stdout.strip()
    except (OSError, IndexError, ValueError):
        return ""


def our_model_pid():
    st = load_json(MODEL_STATE, None)
    cl = _proc_cmdline(st["pid"]) if st else ""
    if st and "serve" in cl and ("wev" in cl or "kev.serve" in cl):
        return st
    MODEL_STATE.unlink(missing_ok=True)
    return None


def stop_model():
    st = our_model_pid()
    if not st:
        return False
    pid = st["pid"]
    if SYSTEM == "Windows":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
    else:
        import signal
        try:
            os.killpg(pid, signal.SIGTERM)  # started in its own session: stops uvicorn workers too
        except OSError:
            pass
        for _ in range(40):
            if not _proc_cmdline(pid):
                break
            time.sleep(0.25)
        else:
            try:
                os.killpg(pid, signal.SIGKILL)
            except OSError:
                pass
    MODEL_STATE.unlink(missing_ok=True)
    return True


def ensure_model(backend, wait=True, timeout=600):
    """Make sure the local OpenJev server for `backend` is up: start it (or restart it if it died or a different
    model is loaded), then wait until it answers. Weights load from the Hugging Face cache, no download."""
    repo = MODELS[backend][0]
    st = our_model_pid()
    if st and st.get("model") != repo:
        print(f"switching model server from {st.get('model')} to {repo} …", flush=True)
        stop_model()
        st = None
    if openjev_up() and (st or not MODEL_STATE.exists()):
        return
    if not st:
        if openjev_up():  # someone else's server on our port: use it as is
            return
        cmd = server_cmd(backend)
        if not Path(cmd[0]).exists():
            die(f"the {backend} server is not installed in {VENV}. Run: bc setup")
        mlog = open(HOME / "openjev.log", "a")
        kw = {"start_new_session": True} if SYSTEM != "Windows" else {"creationflags": 0x00000008 | 0x00000200}
        proc = subprocess.Popen(cmd, stdout=mlog, stderr=mlog, stdin=subprocess.DEVNULL,
                                env={**os.environ, "KEV_TRUNCATE_STATES": "1"}, **kw)
        save_json(MODEL_STATE, {"pid": proc.pid, "model": repo})
        print(f"starting model server {repo} on 127.0.0.1:{OPENJEV_PORT} (log: {HOME / 'openjev.log'})", flush=True)
    if not wait:
        return
    t0 = time.time()
    tty = sys.stdout.isatty()
    while time.time() - t0 < timeout:
        if openjev_up():
            print(("\r" if tty else "") + f"model ready ({time.time() - t0:.0f}s)          ", flush=True)
            return
        if not our_model_pid():
            tail = (HOME / "openjev.log").read_text(errors="replace").splitlines()[-8:]
            die("model server exited while loading:\n  " + "\n  ".join(tail)
                + f"\n(full log: {HOME / 'openjev.log'}"
                + ("; out of memory? try OpenJev-1.7B via `bc setup`)" if backend == "openjev-4b" else ")"))
        if tty:
            print(f"\rloading model weights … {time.time() - t0:.0f}s", end="", flush=True)
        time.sleep(1)
    die(f"model server did not become ready in {timeout}s; see {HOME / 'openjev.log'}")


def cmd_start(a):
    cfg = load_json(CONFIG, None)
    if cfg and a.use:
        choices = browser_choices()
        want = choices[match_choice(choices, a.use)]
        running = (load_json(STATE, None) or {}).get("browser")
        if running and not same_browser(running, want) and call("ping", quiet_fail=True):
            print(f"switching from {running.get('label')} to {want['label']} …")
            call("stop", quiet_fail=True)
            time.sleep(1)
        if want.get("mode") == "profile" and not Path(want["user_data_dir"]).exists():
            print(f"first use of {want['label']}: copying it (read-only) …")
            clone_for(want)
        if a.remember:
            cfg["browser"] = want
            save_json(CONFIG, cfg)
        os.environ["BC_BROWSER"] = json.dumps(want)
        cfg = {**cfg, "browser": want}
    if call("ping", quiet_fail=True):
        print("browser already running")
        if cfg and is_local(cfg.get("backend", "agent")):
            try:
                ensure_model(cfg["backend"], wait=not a.no_wait)  # e.g. the model server crashed
            except SystemExit:
                print("warning: model unavailable; the browser works without it", file=sys.stderr)
        return
    if not cfg:
        die("not set up yet. Run: bc setup")
    HOME.mkdir(parents=True, exist_ok=True)
    log = open(HOME / "daemon.log", "a")
    kw = {"start_new_session": True} if SYSTEM != "Windows" else {"creationflags": 0x00000008}
    subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "_daemon"], stdout=log, stderr=log,
                     stdin=subprocess.DEVNULL, **kw)
    backend = cfg.get("backend", "agent")
    if is_local(backend):
        try:
            ensure_model(backend, wait=not a.no_wait)  # the browser starts meanwhile
        except SystemExit:  # the model failed: the browser still works, the agent decides without it
            print("warning: continuing without the model; `bc ask/act/suggest` will retry it", file=sys.stderr)
    for _ in range(60):
        time.sleep(0.5)
        if load_json(STATE, None) and call("ping", quiet_fail=True):
            print(f"browser running: {cfg['browser'].get('label', 'standalone')}"
                  + ("" if a.use else "  (default; another one: bc profiles, then bc start --use N)"))
            mem, scope = memory_dir()
            print(f"memory: {mem} ({scope}) — run `bc recall <site>` before acting")
            if backend != "agent":
                print(f"decision model: {backend} — act through it: bc act \"<intent>\" [--value V], bc fill, "
                      f"bc ask yes \"<check>\"; refs (bc click N) are the fallback")
            return
    die(f"browser did not start; see {HOME / 'daemon.log'}"
        + (" (is that browser already open with the same copied profile?)" if cfg["browser"]["mode"] != "standalone" else ""))


def cmd_stop(a):
    print("browser stopped" if call("stop", quiet_fail=True) else "browser not running")
    if not a.keep_model and stop_model():
        print("model server stopped (memory freed)")


def cmd_status(a):
    cfg = load_json(CONFIG, {})
    mem, scope = memory_dir()
    up = bool(call("ping", quiet_fail=True))
    running = (load_json(STATE, None) or {}).get("browser") or {}
    print(json.dumps({"running": up, "default_browser": cfg.get("browser", {}).get("label"),
                      "running_browser": running.get("label") if up else None, "backend": cfg.get("backend"),
                      "openjev_server": ({"up": openjev_up(), **(our_model_pid() or {})}
                                       if is_local(str(cfg.get("backend", ""))) else None),
                      "memory": str(mem), "memory_scope": scope, "config": str(CONFIG)}, indent=2))


def describe(e):
    """One-line element description shared by the printed snapshot and model requests."""
    s = f"[{e['ref']}] {e['role']} \"{e['label']}\""
    for k in ("value", "options", "href", "input"):
        if k in e and e[k] not in ("", None):
            s += f" {k}={json.dumps(e[k])}"
    if e.get("state"):
        s += " (" + ", ".join(e["state"]) + ")"
    if e.get("context"):
        s += f" in [{e['context']}]"
    if e.get("frame"):
        s += " [iframe]"
    if e.get("offscreen"):
        s += " (offscreen)"
    return s


def print_snapshot(s):
    print(f"URL: {s['url']}\nTITLE: {s['title']}\n\n--- page text ---\n{s['text']}\n\n--- elements (use the ref number) ---")
    for e in s["elements"]:
        print(describe(e))
    backend = load_json(CONFIG, {}).get("backend", "agent")
    if backend != "agent":
        print(f"\n(decision model {backend} is configured: do the next step with "
              f"bc act \"<intent>\"; use a ref only if act exits 2 or 3)")


# ---------------------------------------------------------------- decision model (Jev API / local OpenJev)
# Bounded decisions only: the model picks among real refs and a fixed operation vocabulary; values come
# from the caller; low confidence fails visibly instead of guessing (ideas from filedcom/playjev).

DEBUG = os.environ.get("BC_DEBUG") == "1"


def backend_or_die():
    backend = load_json(CONFIG, {}).get("backend", "agent")
    if backend == "agent":
        die("no decision model configured (backend: agent). Decide from `bc snapshot` yourself, "
            "or rerun `bc setup` to add Jev / OpenJev.")
    return backend


def system_one(state, questions, timeout=30):
    """POST /v1/systemone via the browser daemon, which keeps one connection open and retries
    network errors, 408, 429 and 5xx (honouring Retry-After)."""
    backend = backend_or_die()
    if backend == "jev":
        key = os.environ.get("TYPESAFE_API_KEY") or load_json(SECRETS, {}).get("TYPESAFE_API_KEY")
        if not key:
            die("no Jev API key: set TYPESAFE_API_KEY or rerun `bc setup`")
        url, headers, model = JEV_URL, {"Authorization": f"Bearer {key}"}, "jev-latest"
    else:
        ensure_model(backend)  # starts / restarts / waits as needed
        url, headers = f"http://127.0.0.1:{OPENJEV_PORT}/v1/systemone", {}
        model = "kev-latest" if backend.startswith("kev") else "wev-latest"
    if DEBUG:
        print(f"[bc] {backend} questions: " + ", ".join(f"{k}({q['type']}, {len(q.get('criteria') or [])} options)"
                                                       for k, q in questions.items()), file=sys.stderr)
    t = time.time()
    answers = call("model", {"url": url, "headers": headers, "timeout": timeout,
                             "body": json.dumps({"model": model, "state": state, "questions": questions})})["answers"]
    if not isinstance(answers, dict):
        die(f"{backend} returned an invalid response")
    if DEBUG:
        print(f"[bc] answers ({(time.time() - t) * 1000:.0f} ms): {json.dumps(answers)[:1500]}", file=sys.stderr)
    return answers


def model_state(s, max_elements=200):
    return {"page": {"url": s["url"], "title": s["title"], "text": s["text"]},
            "elements": [describe(e) for e in s["elements"][:max_elements]]}


def confidence(ans):
    return round(ans.get("confidence", max(ans.get("probabilities", {0: 0}).values())), 3)


def top(ans, k=3):
    return [{"ref": int(r) if str(r).isdigit() else r, "p": round(p, 3)}
            for r, p in sorted(ans.get("probabilities", {}).items(), key=lambda kv: -kv[1])[:k]]


def ask_yes(question, s=None, recent=None):
    s = s or call("snapshot", {"text": 2500})
    state = model_state(s)
    if recent:
        state["recent_actions"] = recent
    ans = system_one(state, {"q": {"type": "noul", "instructions": question,
                                            "criteria": {"true": "Yes, this is true of the current page.",
                                                         "false": "No, this is not true of the current page."}}})["q"]
    p = float(ans.get("noul", ans.get("probability", 0)))
    return {"answer": p >= 0.5, "probability": round(p, 3)}


# ---- Kev-0.6B browser-use: trained on one exact text shape (Mind2Web style), ~10 candidates, 384 tokens.

KEV_ELEMENT_Q = "Which element should be acted on?"
KEV_OP_Q = "What operation should be performed on the target element?"


def _words(t):
    return set(_norm(t).split())


def kev_candidates(goal, els, k=10):
    """Pre-rank the snapshot to the ~10 candidates Kev can see: word overlap with the goal, on-screen first."""
    gw = _words(goal)
    def score(e):
        return (len(gw & _words(f"{e['label']} {e.get('context', '')} {e['role']}")), not e.get("offscreen"))
    return sorted(els, key=score, reverse=True)[:k]


def kev_label(i, e):
    return f'[{i}] <{e.get("tag", "div")}> "{e["label"][:60]}"'


def kev_decide(goal, els, history=None):
    if not els:
        die("no actionable elements on the page")
    cands = kev_candidates(goal, els)
    lines = [f'[{i}] <{e.get("tag", "div")}> role={e["role"] if e["role"] != "generic" else "None"} "{e["label"][:60]}"'
             for i, e in enumerate(cands)]
    state = f"Goal: {goal}\n" + (f"Previous actions:\n{history}\n" if history else "") + "Candidate elements:\n" + "\n".join(lines)
    opts = {kev_label(i, e): None for i, e in enumerate(cands)}
    ans = system_one(state, {"element": {"type": "choice", "instructions": KEV_ELEMENT_Q, "criteria": opts},
                             "operation": {"type": "choice", "instructions": KEV_OP_Q,
                                           "criteria": {"CLICK": None, "TYPE": None, "SELECT": None}}})
    labels = list(opts)
    el, op = ans["element"], ans["operation"]
    pick = cands[labels.index(el["choice"])]
    alts = [{"ref": cands[labels.index(l)]["ref"], "p": round(p, 3)}
            for l, p in sorted(el["probabilities"].items(), key=lambda kv: -kv[1])[1:4]]
    return {"element": pick, "target_confidence": confidence(el), "operation": op["choice"],
            "operation_confidence": confidence(op), "alternatives": alts}


def is_kev():
    return load_json(CONFIG, {}).get("backend", "").startswith("kev")


def cmd_ask(a):
    if is_kev():
        die("Kev-0.6B browser-use only answers browser-step questions (element + operation), so `bc ask` isn't "
            "supported with it. Judge the page from `bc snapshot`, or switch to OpenJev / Jev with `bc setup`.")
    if a.kind == "yes":
        out = ask_yes(a.question)
    elif a.kind == "choose":
        opts = dict(o.split("=", 1) if "=" in o else (o, o) for o in a.options)
        if len(opts) < 2:
            die('give at least two options: key="description"')
        ans = system_one(model_state(call("snapshot", {"text": 2500})),
                         {"q": {"type": "choice", "instructions": a.question, "criteria": opts}})["q"]
        out = {"answer": ans["choice"], "confidence": confidence(ans),
               "probabilities": {k: round(v, 3) for k, v in ans["probabilities"].items()}}
    else:
        if len(a.options) < 2:
            die("give an ordered scale of at least two levels, lowest first")
        ans = system_one(model_state(call("snapshot", {"text": 2500})),
                         {"q": {"type": "score", "instructions": a.question, "criteria": a.options}})["q"]
        out = {k: v for k, v in ans.items() if k != "type"}
    print(json.dumps(out, indent=2))


OPS = {"CLICK": "click", "CHECK": "check", "UNCHECK": "uncheck", "TYPE": "type", "SELECT": "select", "PRESS": "press"}


def act_once(instruction, value=None, key=None, min_conf=0.65, verify=0.5):
    """Choose one target + operation, execute it deterministically, verify. Returns (exit code, result):
    0 done + verified, 1 execution error, 2 low confidence (nothing ran), 3 ran but not verified."""
    if is_kev():
        return kev_act_once(instruction, value, key, min_conf)
    s = call("snapshot", {"text": 2500})
    els = [e for e in s["elements"] if "disabled" not in e.get("state", [])][:200]
    if not els:
        return 2, {"instruction": instruction, "success": False, "reason": "no actionable elements on the page"}
    if value is not None:
        ops = {"TYPE": "Type the given value into a text field.", "SELECT": "Pick the given value in a dropdown."}
    elif key:
        ops = {"PRESS": f"Press {key} with focus on an element."}
    else:
        ops = {"CLICK": "Click a button, link, tab, menu item or option.",
               "CHECK": "Tick a checkbox, radio or switch.", "UNCHECK": "Untick a checkbox or switch."}
    goal = {"instruction": instruction, **({"value": value} if value is not None else {}), **({"key": key} if key else {})}
    questions = {"target": {"type": "choice", "instructions": {"goal": goal, "rules": "Pick the element to act on."},
                            "criteria": {str(e["ref"]): describe(e) for e in els}}}
    if len(ops) > 1:
        questions["operation"] = {"type": "choice", "criteria": ops, "instructions": {"goal": goal}}
    ans = system_one(model_state(s), questions)
    t = ans["target"]
    ref, tconf = int(t["choice"]), confidence(t)
    op = ans["operation"]["choice"] if "operation" in ans else next(iter(ops))
    el = next(e for e in els if e["ref"] == ref)
    result = {"instruction": instruction, "ref": ref, "target": describe(el), "operation": op, "target_confidence": tconf,
              **({"operation_confidence": confidence(ans["operation"])} if "operation" in ans else {})}
    if tconf < min_conf:
        result.update(success=False, reason=f"target confidence {tconf} < {min_conf}; nothing executed",
                      candidates=top(t))
        return 2, result
    res = call("apply", {"steps": [{"op": OPS[op], "ref": ref, "value": value if value is not None else key}]})
    step = res["steps"][0]
    if "error" in step:
        result.update(success=False, reason=step["error"])
        return 1, result
    # Proof beats a second model call: the value is in the field, the box has the state, the link's page loaded.
    href = urllib.parse.urljoin(s["url"], el["href"]) if el.get("href") else None
    same = lambda x, y: x.rstrip("/").split("#")[0] == y.rstrip("/").split("#")[0]
    if "value_after" in step and value is not None and _norm(step["value_after"]) == _norm(value):
        how, p = "value is in the field", 1.0
    elif "checked_after" in step and step["checked_after"] == (op == "CHECK"):
        how, p = "control has the requested state", 1.0
    elif op == "CLICK" and href and res["navigated"] and same(res["url"], href):
        how, p = "link destination reached", 1.0
    else:
        after = call("snapshot", {"text": 2500})
        sig = lambda snap: {(e["role"], e["label"], tuple(e.get("state", []))) for e in snap["elements"]}
        changed = res["navigated"] or sig(after) != sig(s)
        if op in ("CLICK", "PRESS") and changed:
            # The model already picked this target confidently; the page visibly responding (navigation, a dialog
            # or editor opening, controls changing) is the evidence. Asking "is the instruction accomplished?"
            # here was flaky: after "Click Add a comment" no comment exists yet, so the model hesitates.
            how, p = "page responded to the click", 1.0
        else:  # nothing visible changed, or a type/select that didn't stick: let the model judge
            recent = [{"action": f"{op} {describe(el)}" + (f' with "{value}"' if value is not None else ""),
                       "url_before": s["url"], "url_after": res["url"], "page_changed": changed}]
            how, p = "model check of the recent action", ask_yes(
                f"The most recent action accomplished this instruction: {instruction}", s=after, recent=recent)["probability"]
    result.update(method=step.get("method"), url=res["url"], navigated=res["navigated"],
                  verification=how, verification_probability=p, success=p >= verify)
    return (0 if result["success"] else 3), result


def cmd_act(a):
    code, result = act_once(a.instruction, a.value, a.key, a.min_conf, a.verify)
    print(json.dumps(result, indent=2))
    sys.exit(code)


def parse_step(text):
    """`bc do` step: "intent", "intent :: value" or "intent :: key:Enter"."""
    intent, sep, rest = text.partition("::")
    intent, rest = intent.strip(), rest.strip()
    if not sep:
        return intent, None, None
    if rest.lower().startswith("key:"):
        return intent, None, rest[4:].strip()
    return intent, rest, None


def cmd_do(a):
    """Several intents in one command (one agent turn); stops at the first step that fails."""
    out, t0 = [], time.time()
    prev = None
    for text in a.steps:
        intent, value, key = parse_step(text)
        t = time.time()
        if key and prev and prev.get("operation") in ("TYPE", "SELECT") and prev.get("ref"):
            # a key right after typing belongs to that field (Enter to search/submit): no model call needed. Send it
            # to the focused element: widgets like Wikipedia's search rebuild the input while you type, so the ref is gone
            r = call("press", {"key": key})
            code, res = 0, {"instruction": intent, "ref": prev["ref"], "operation": "PRESS", "target": prev["target"],
                            "url": r["url"], "navigated": r["navigated"], "verification": "key sent to the focused field just typed in",
                            "success": True}
        else:
            code, res = act_once(intent, value, key, a.min_conf, a.verify)
        prev = res
        res["ms"] = round((time.time() - t) * 1000)
        out.append(res)
        if code:
            print(json.dumps({"success": False, "stopped_at": len(out), "of": len(a.steps), "steps": out}, indent=2))
            sys.exit(code)
    print(json.dumps({"success": True, "steps": out, "total_ms": round((time.time() - t0) * 1000)}, indent=2))


RISKY = re.compile(r"\b(submit|pay|payment|buy|purchase|place order|order now|checkout|check out|delete|remove|send|post|"
                   r"publish|confirm|transfer|sign out|log ?out|unsubscribe|deactivate|cancel|book now|reserve|"
                   r"approve|merge|deploy|accept|save)\b", re.I)


def cmd_auto(a):
    """The model drives a multi-step goal by itself (no agent turn per step), jev-ultrafast style.
    Stops before anything that looks irreversible, on low confidence, when stuck, or at --max-steps."""
    backend = backend_or_die()
    if backend.startswith("kev"):
        die("`bc auto` needs a model that can say DONE/BLOCKED (Jev or OpenJev); Kev only picks single steps. "
            "Use `bc do` with explicit steps instead.")
    last_run = load_json(HOME / "auto.json", {}) if a.cont else {}
    if a.cont and not last_run:
        die("no stopped `bc auto` run to continue")
    if not a.goal and not last_run:
        die("give the goal: bc auto \"<goal>\"")
    values = dict(last_run.get("values", {}))
    for kv in a.value or []:
        k, sep, v = kv.partition("=")
        if not sep:
            die(f'--value expects "Field=value", got {kv!r}')
        values[k.strip()] = v
    allowed = [_norm(x) for x in (a.allow or [])] + last_run.get("allowed", [])
    goal = " ".join(a.goal) or last_run["goal"]
    history, steps, seen, t0 = list(last_run.get("history", [])), [], {}, time.time()

    def finish(status, code, **extra):
        save_json(HOME / "auto.json", {"goal": goal, "values": values, "allowed": allowed, "history": history,
                                       "status": status})
        print(json.dumps({"status": status, "goal": goal, "steps": steps, **extra,
                          "url": last.get("url"), "title": last.get("title"),
                          "total_ms": round((time.time() - t0) * 1000)}, indent=2))
        sys.exit(code)

    last = {}
    for n in range(1, a.max_steps + 1):
        s = last = call("snapshot", {"text": 2500})
        els = [e for e in s["elements"] if "disabled" not in e.get("state", [])][:200]
        typable = {str(e["ref"]): describe(e) for e in els if e["role"] in ("textbox", "searchbox", "spinbutton")}
        selects = {str(e["ref"]): describe(e) for e in els if "options" in e}
        clickable = {str(e["ref"]): describe(e) for e in els if str(e["ref"]) not in typable and str(e["ref"]) not in selects}
        ops = {"CLICK": "Click an element, button, link, menu option or suggestion.",
               "SCROLL": "Scroll to reveal more of the page.",
               "DONE": "Every requirement of the goal is visibly satisfied.",
               "BLOCKED": "No available operation can make progress."}
        if values and typable:
            ops["TYPE_TEXT"] = "Enter one of the given values into an editable field."
        if values and selects:
            ops["SELECT"] = "Choose one of the given values in a dropdown."
        if not clickable:
            ops.pop("CLICK")
        ig = {"goal": goal, **({"given_values": values} if values else {})}
        q = {"operation": {"type": "choice", "criteria": ops, "instructions": ig}}
        if clickable:
            q["click_target"] = {"type": "choice", "criteria": clickable, "instructions": {**ig, "operation": "CLICK"}}
        if "TYPE_TEXT" in ops:
            q["type_text_target"] = {"type": "choice", "criteria": typable, "instructions": {**ig, "operation": "TYPE_TEXT"}}
        if "SELECT" in ops:
            q["select_target"] = {"type": "choice", "criteria": selects, "instructions": {**ig, "operation": "SELECT"}}
        if values and ("TYPE_TEXT" in ops or "SELECT" in ops):
            q["value"] = {"type": "choice", "criteria": {k: f"{k}: {v}" for k, v in values.items()},
                          "instructions": {**ig, "rules": "Which given value is typed or selected next?"}}
        state = {**model_state(s), "recent_actions": history[-8:]}
        ans = system_one(state, q)
        op, oconf = ans["operation"]["choice"], confidence(ans["operation"])
        if op == "DONE":
            steps.append({"step": n, "operation": "DONE", "confidence": oconf})
            finish("done", 0)
        if op == "BLOCKED":
            steps.append({"step": n, "operation": "BLOCKED", "confidence": oconf})
            finish("blocked", 2)
        if op == "SCROLL":
            call("scroll", {"dir": "down"})
            history.append({"action": "SCROLL down", "url": s["url"]})
            steps.append({"step": n, "operation": "SCROLL", "confidence": oconf})
            continue
        head = {"CLICK": "click_target", "TYPE_TEXT": "type_text_target", "SELECT": "select_target"}[op]
        t = ans[head]
        ref, tconf = int(t["choice"]), confidence(t)
        el = next(e for e in els if e["ref"] == ref)
        val = values[ans["value"]["choice"]] if op in ("TYPE_TEXT", "SELECT") else None
        rec = {"step": n, "operation": op, "target": describe(el), "confidence": oconf, "target_confidence": tconf,
               **({"value": val} if val is not None else {})}
        if oconf < 0.5 or tconf < a.min_conf:
            steps.append({**rec, "executed": False})
            finish("low_confidence", 2, candidates=top(t))
        if op == "CLICK" and RISKY.search(el["label"]) and not any(x and x in _norm(el["label"]) for x in allowed):
            steps.append({**rec, "executed": False})
            finish("needs_confirmation", 2, next_step={"ref": ref, "target": describe(el)},
                   hint=f"ask the user; if they agree run: bc auto --continue --allow \"{el['label'][:40]}\"")
        sig = (op, el["label"], val)
        seen[sig] = seen.get(sig, 0) + 1
        if seen[sig] >= 3:
            steps.append({**rec, "executed": False})
            finish("stuck", 2, reason="the same action was chosen 3 times without reaching the goal")
        res = call("apply", {"steps": [{"op": {"CLICK": "click", "TYPE_TEXT": "type", "SELECT": "select"}[op],
                                        "ref": ref, "value": val}]})
        st = res["steps"][0]
        if "error" in st:
            steps.append({**rec, "executed": False, "error": st["error"]})
            finish("error", 1)
        steps.append({**rec, "executed": True, "url": res["url"]})
        history.append({"action": f"{op} {el['label'][:60]}" + (f' "{val}"' if val else ""),
                        "url_before": s["url"], "url_after": res["url"], "page_changed": res["navigated"]})
    finish("max_steps", 3)


def kev_act_once(instruction, value, key, min_conf):
    """Kev variant of act_once: Kev can't answer a yes/no check, so verification is deterministic."""
    class A:  # keep the body below readable
        pass
    a = A()
    a.instruction, a.value, a.key, a.min_conf = instruction, value, key, min_conf
    s = call("snapshot", {"text": 0})
    els = [e for e in s["elements"] if "disabled" not in e.get("state", [])][:200]
    goal = a.instruction + (f' (value: "{a.value}")' if a.value is not None else "")
    d = kev_decide(goal, els)
    e, op = d["element"], d["operation"]
    if a.value is not None and op == "CLICK":
        op = "SELECT" if "options" in e else "TYPE"  # a value was given: never just click
    result = {"target": describe(e), "operation": op, "target_confidence": d["target_confidence"],
              "operation_confidence": d["operation_confidence"]}
    if d["target_confidence"] < a.min_conf:
        result.update(success=False, reason=f"target confidence {d['target_confidence']} < {a.min_conf}; nothing executed",
                      candidates=d["alternatives"])
        return 2, result
    if op in ("TYPE", "SELECT") and a.value is None:
        result.update(success=False, reason=f"Kev chose {op} but no --value was given; nothing executed")
        return 2, result
    step = {"op": {"CLICK": "click", "TYPE": "type", "SELECT": "select"}[op], "ref": e["ref"], "value": a.value}
    if a.key:
        step = {"op": "press", "ref": e["ref"], "value": a.key}
    res = call("apply", {"steps": [step]})
    if "error" in res["steps"][0]:
        result.update(success=False, reason=res["steps"][0]["error"])
        return 1, result
    after = {x["ref"]: x for x in call("snapshot", {"text": 0})["elements"]}
    if step["op"] in ("type", "select"):
        ok = _norm(after.get(e["ref"], {}).get("value", "")) == _norm(a.value)
    else:
        ok = True  # a click ran; Kev has no yes/no check, so confirm the outcome from the snapshot yourself
    result.update(method=res["steps"][0].get("method"), url=res["url"], navigated=res["navigated"], success=ok,
                  verification="deterministic (Kev has no yes/no head)")
    return (0 if ok else 3), result


def _norm(s):
    return " ".join("".join(c.lower() if c.isalnum() else " " for c in str(s)).split())


def _match_score(field, value, e):
    """Deterministic label matching for `fill` when no model is configured."""
    f, lab, ctx = _norm(field), _norm(e["label"]), _norm(e.get("context", ""))
    if e["role"] == "radio":
        group = ctx or lab
        g = 3 if f == group else 2 if f in group or (group and group in f) else 0
        v = _norm(value)
        return g + (3 if v == lab else 1 if v and v in lab else -9) if g else 0
    if not lab:
        return 0
    if f == lab:
        return 6
    if lab.startswith(f) or f.startswith(lab):
        return 4
    if f in lab or lab in f:
        return 3
    fw, lw = set(f.split()), set(lab.split())
    return 2 if fw and len(fw & lw) / len(fw) >= 0.6 else 0


def _op_for(e, value):
    v = str(value).strip()
    if e["role"] in ("checkbox", "switch"):
        if v.lower() in TRUE_WORDS:
            return "check", None
        if v.lower() in FALSE_WORDS:
            return "uncheck", None
        raise ValueError(f"{e['label']!r} is a checkbox: use yes/no")
    if e["role"] == "radio":
        return "check", None
    if e["role"] in ("combobox", "listbox") and "options" in e:
        return "select", v
    if e["role"] in ("button", "menuitem"):
        return "click", None
    return "type", v


def cmd_fill(a):
    """Bulk form fill: caller supplies every value; each field maps to a distinct control; result is verified."""
    fields = []
    for kv in a.fields:
        if "=" not in kv:
            die(f"expected Label=value, got {kv!r}")
        k, v = kv.split("=", 1)
        fields.append((k.strip(), v))
    s = call("snapshot", {"text": 1500})
    cands = [e for e in s["elements"] if (e["role"] in FIELD_ROLES or e["role"] == "generic" and "value" in e)
             and "disabled" not in e.get("state", [])]
    if not cands:
        die("no form fields on the page")
    backend = load_json(CONFIG, {}).get("backend", "agent")
    picks, problems = {}, []
    if backend.startswith("kev") and not a.no_model:
        for k, v in fields:  # one short request per field (Kev sees one goal at a time)
            d = kev_decide(f'Fill "{k}" with "{v}"', cands)
            if d["target_confidence"] < a.min_conf:
                problems.append(f"{k}: target confidence {d['target_confidence']} < {a.min_conf} (candidates {d['alternatives']})")
            else:
                picks[k] = d["element"]["ref"]
    elif backend != "agent" and not a.no_model:
        qs = {f"f{i}": {"type": "choice", "instructions": {"field": k, "value": v,
                                                           "rules": "Pick the control this form field and value belong to."},
                        "criteria": {str(e["ref"]): describe(e) for e in cands}} for i, (k, v) in enumerate(fields)}
        ans = system_one(model_state(s), qs)  # one batched round trip for the whole form
        for i, (k, v) in enumerate(fields):
            c = ans[f"f{i}"]
            if confidence(c) < a.min_conf:
                problems.append(f"{k}: target confidence {confidence(c)} < {a.min_conf} (candidates {top(c)})")
            else:
                picks[k] = int(c["choice"])
    else:
        for k, v in fields:
            scored = sorted(((_match_score(k, v, e), e["ref"]) for e in cands), reverse=True)
            if not scored or scored[0][0] <= 0:
                problems.append(f"{k}: no control with a matching label")
            elif len(scored) > 1 and scored[1][0] == scored[0][0]:
                problems.append(f"{k}: ambiguous — refs {scored[0][1]} and {scored[1][1]} match equally; use `bc type/select/toggle`")
            else:
                picks[k] = scored[0][1]
    dup = {r for r in picks.values() if list(picks.values()).count(r) > 1}
    for k in [k for k, r in picks.items() if r in dup]:
        problems.append(f"{k}: shares ref {picks.pop(k)} with another field")
    by_ref = {e["ref"]: e for e in cands}
    steps = []
    for k, v in fields:
        if k in picks:
            try:
                op, val = _op_for(by_ref[picks[k]], v)
                steps.append({"field": k, "op": op, "ref": picks[k], "value": val})
            except ValueError as e:
                problems.append(f"{k}: {e}")
    if problems and not a.partial:
        print(json.dumps({"success": False, "nothing_executed": True, "problems": problems,
                          "matched": {k: describe(by_ref[r]) for k, r in picks.items()}}, indent=2))
        sys.exit(2)
    res = call("apply", {"steps": [{x: st[x] for x in ("op", "ref", "value")} for st in steps]})
    after = {e["ref"]: e for e in call("snapshot", {"text": 0})["elements"]}  # fresh snapshot to verify
    report = []
    for st, done in zip(steps, res["steps"]):
        el = after.get(st["ref"], {})
        if "error" in done:
            ok = False
        elif st["op"] in ("check", "uncheck"):
            ok = ("checked" in el.get("state", [])) == (st["op"] == "check")
        elif st["op"] in ("type", "select"):
            ok = _norm(el.get("value", "")) == _norm(st["value"]) or el.get("input") == "password"
        else:
            ok = True
        report.append({"field": st["field"], "ref": st["ref"], "op": st["op"], "ok": ok,
                       **({"error": done["error"]} if "error" in done else {})})
    success = all(r["ok"] for r in report) and len(report) == len(fields)
    print(json.dumps({"success": success, "fields": report, **({"problems": problems} if problems else {})}, indent=2))
    sys.exit(0 if success else 3)


def cmd_suggest(a):
    """Advisory next step for a multi-step goal; you decide whether to follow it."""
    if is_kev():
        s = call("snapshot", {"text": 0})
        d = kev_decide(" ".join(a.goal), [e for e in s["elements"] if "disabled" not in e.get("state", [])])
        print(json.dumps({"operation": d["operation"], "confidence": d["operation_confidence"], "ref": d["element"]["ref"],
                          "target": describe(d["element"]), "target_confidence": d["target_confidence"],
                          "alternatives": d["alternatives"],
                          "note": "Kev only picks CLICK/TYPE/SELECT; judge DONE/BLOCKED from the snapshot yourself"}, indent=2))
        return
    s = call("snapshot", {"text": 2500})
    els = [e for e in s["elements"] if "disabled" not in e.get("state", [])][:200]
    if not els:
        die("no interactive elements on the page")
    goal = " ".join(a.goal)
    typable = {str(e["ref"]): describe(e) for e in els if e["role"] in ("textbox", "searchbox", "spinbutton")}
    clickable = {str(e["ref"]): describe(e) for e in els if str(e["ref"]) not in typable}
    ops = {"CLICK": "Click an element, button, link, menu option or suggestion.",
           "TYPE_TEXT": "Enter or replace text in an editable field.",
           "SCROLL": "Scroll to reveal more of the page.",
           "DONE": "Every requirement of the goal is visibly satisfied.",
           "BLOCKED": "No available operation can make progress."}
    if not typable:
        ops.pop("TYPE_TEXT")
    if not clickable:
        ops.pop("CLICK")
    q = {"operation": {"type": "choice", "criteria": ops, "instructions": {"goal": goal}}}
    if clickable:
        q["click_target"] = {"type": "choice", "criteria": clickable, "instructions": {"goal": goal, "operation": "CLICK"}}
    if typable:
        q["type_text_target"] = {"type": "choice", "criteria": typable, "instructions": {"goal": goal, "operation": "TYPE_TEXT"}}
    ans = system_one(model_state(s), q)
    op = ans["operation"]
    out = {"operation": op["choice"], "confidence": confidence(op)}
    head = {"CLICK": "click_target", "TYPE_TEXT": "type_text_target"}.get(op["choice"])
    if head and head in ans:
        t = ans[head]
        out.update(ref=int(t["choice"]), target=describe(next(e for e in els if str(e["ref"]) == t["choice"])),
                   target_confidence=confidence(t), alternatives=top(t, 4)[1:])
    print(json.dumps(out, indent=2))


# ---------------------------------------------------------------- cli

# Everyday browser commands skip argparse (building ~30 sub-parsers costs ~90 ms on every call).
FAST = {
    "click": ["ref:int"], "type": ["ref:int", "text", "--enter"], "select": ["ref:int", "value"],
    "toggle": ["ref:int", "state"], "press": ["key", "--ref:int"], "scroll": ["dir"], "open": ["url"],
    "back": [], "reload": [], "tabs": [], "tab": ["n:int"], "wait": ["seconds:float"], "eval": ["js"],
    "screenshot": ["path", "--full"], "snapshot": ["--text:int"],
}


def fast_main(argv):
    """Parse an everyday command by hand; return None (fall back to argparse) on anything unusual."""
    if not argv or argv[0] not in FAST or any(x in ("-h", "--help") for x in argv):
        return None
    spec, args, rest = FAST[argv[0]], {}, list(argv[1:])
    conv = {"int": int, "float": float, None: str}
    try:
        for item in spec:
            if item.startswith("--"):
                name, _, typ = item[2:].partition(":")
                if f"--{name}" in rest:
                    i = rest.index(f"--{name}")
                    if typ:
                        args[name] = conv[typ](rest[i + 1])
                        del rest[i:i + 2]
                    else:
                        args[name] = True
                        del rest[i]
        for item in [x for x in spec if not x.startswith("--")]:
            name, _, typ = item.partition(":")
            args[name] = conv[typ or None](rest.pop(0))
    except (IndexError, ValueError):
        return None
    if rest:
        return None
    if argv[0] == "snapshot":
        args.setdefault("text", 3000)
    return argv[0], args


def main():
    fast = fast_main(sys.argv[1:])
    if fast:
        cmd, args = fast
        res = call(cmd, args)
        print_snapshot(res) if cmd == "snapshot" else print(json.dumps(res, indent=2))
        return
    import argparse
    ap = argparse.ArgumentParser(prog="bc", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    s = sp.add_parser("setup")
    s.add_argument("--scope", choices=["user", "project"])
    s.add_argument("--browser", help="substring of the menu option to pick, e.g. 'Chrome: profile \"Work\"'")
    s.add_argument("--backend", choices=["agent", "jev", *MODELS])
    s.add_argument("--headless", action="store_true")
    s.add_argument("--with-deps", action="store_true", help="Linux: also apt-install Playwright system libraries")
    s.add_argument("--yes", action="store_true", help="no questions: user scope, standalone browser, agent backend")
    s.add_argument("--refresh", action="store_true", help="upgrade dependencies with the saved choices (used by updates)")
    s = sp.add_parser("start")
    s.add_argument("--use", help="browser/profile for this task: a number from `bc profiles` or text like 'chrome dipesh'")
    s.add_argument("--remember", action="store_true", help="with --use: also make it the default")
    s.add_argument("--no-wait", action="store_true", help="don't wait for the model to finish loading")
    sp.add_parser("profiles").add_argument("--json", action="store_true")
    sp.add_parser("use").add_argument("choice", help="number from `bc profiles` or matching text")
    sp.add_parser("resync").add_argument("choice", nargs="?", help="which profile (default: the default one)")
    s = sp.add_parser("stop")
    s.add_argument("--keep-model", action="store_true", help="leave the model server running for the next task")
    for name in ("browsers", "status", "back", "reload", "tabs", "_daemon"):
        sp.add_parser(name)
    sp.add_parser("open").add_argument("url")
    s = sp.add_parser("snapshot")
    s.add_argument("--text", type=int, default=3000, help="max characters of page text")
    sp.add_parser("click").add_argument("ref", type=int)
    s = sp.add_parser("type")
    s.add_argument("ref", type=int)
    s.add_argument("text")
    s.add_argument("--enter", action="store_true")
    s = sp.add_parser("select")
    s.add_argument("ref", type=int)
    s.add_argument("value")
    s = sp.add_parser("press")
    s.add_argument("key")
    s.add_argument("--ref", type=int, help="focus this element first")
    s = sp.add_parser("toggle")
    s.add_argument("ref", type=int)
    s.add_argument("state", choices=["on", "off"])
    s = sp.add_parser("ask")
    s.add_argument("kind", choices=["yes", "choose", "rate"])
    s.add_argument("question")
    s.add_argument("options", nargs="*", help='choose: key="description" ...; rate: levels, lowest first')
    s = sp.add_parser("act")
    s.add_argument("instruction")
    s.add_argument("--value", help="text to type / option to select (never generated by the model)")
    s.add_argument("--key", help="key to press, e.g. Enter")
    s.add_argument("--min-conf", type=float, default=0.65, help="minimum target confidence (raise for risky steps)")
    s.add_argument("--verify", type=float, default=0.5, help="minimum verification probability")
    s = sp.add_parser("do", help="several model-driven steps in one command")
    s.add_argument("steps", nargs="+", help='"intent", "intent :: value" or "intent :: key:Enter"')
    s.add_argument("--min-conf", type=float, default=0.65)
    s.add_argument("--verify", type=float, default=0.5)
    s = sp.add_parser("auto", help="the model drives a multi-step goal by itself")
    s.add_argument("goal", nargs="*")
    s.add_argument("--value", action="append", help='"Field=value" the model may type/select (repeatable)')
    s.add_argument("--allow", action="append", help="label of an irreversible-looking button the user approved")
    s.add_argument("--max-steps", type=int, default=12)
    s.add_argument("--min-conf", type=float, default=0.6)
    s.add_argument("--continue", dest="cont", action="store_true",
                   help="resume the last stopped run (same goal, values and history); combine with --allow")
    s = sp.add_parser("fill")
    s.add_argument("fields", nargs="+", help='"Label=value"; checkboxes take yes/no, radios take the option label')
    s.add_argument("--min-conf", type=float, default=0.6)
    s.add_argument("--no-model", action="store_true", help="label matching only, even if a model is configured")
    s.add_argument("--partial", action="store_true", help="fill what matched even if some fields did not")
    sp.add_parser("scroll").add_argument("dir", choices=["up", "down"])
    s = sp.add_parser("screenshot")
    s.add_argument("path")
    s.add_argument("--full", action="store_true")
    sp.add_parser("eval").add_argument("js")
    sp.add_parser("tab").add_argument("n", type=int)
    sp.add_parser("wait").add_argument("seconds", type=float)
    sp.add_parser("suggest").add_argument("goal", nargs="+")
    sp.add_parser("recall").add_argument("site", nargs="?")
    s = sp.add_parser("learn")
    s.add_argument("--kind", choices=["pitfall", "recipe", "rule"], required=True)
    s.add_argument("--site")
    s.add_argument("text", nargs="+")
    a = ap.parse_args()

    local = {"setup": cmd_setup, "browsers": cmd_browsers, "learn": cmd_learn, "recall": cmd_recall,
             "stop": cmd_stop, "status": cmd_status, "resync": cmd_resync, "profiles": cmd_profiles, "use": cmd_use, "suggest": cmd_suggest,
             "ask": cmd_ask, "act": cmd_act, "fill": cmd_fill, "do": cmd_do, "auto": cmd_auto}
    if a.cmd in local:
        return local[a.cmd](a)
    if a.cmd == "_daemon":
        reexec_in_venv()
        return daemon_main()
    if a.cmd == "start":
        reexec_in_venv()
        return cmd_start(a)
    args = {k: v for k, v in vars(a).items() if k != "cmd"}
    res = call(a.cmd, args)
    if a.cmd == "snapshot":
        print_snapshot(res)
    else:
        print(json.dumps(res, indent=2))


def entry():
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    entry()
