"""Small stdlib-only host CLI. All container commands use argument arrays."""
import argparse
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

REPO = Path(__file__).resolve().parents[1]
LABEL = "io.pod-pi.instance"
ACTIONS = ("attach", "start", "stop", "status", "shell", "logs", "check", "build",
           "recreate", "enable", "disable", "path")


def run(args, **kwargs):
    return subprocess.run([str(a) for a in args], check=True, **kwargs)


def output(args):
    return run(args, capture_output=True, text=True).stdout.strip()


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


def name_of(value):
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,47}", value):
        raise ValueError("Use a name starting with a lowercase letter, then letters, digits or hyphens (max 48).")
    if value == "pod":
        raise ValueError("The name 'pod' is reserved for the pod-pi command.")
    return value


def strings(value, field):
    if not isinstance(value, list) or any(not isinstance(s, str) or not s or "\0" in s for s in value):
        raise ValueError(f"{field} must be an array of nonempty strings")
    return value


def validate(config):
    if not isinstance(config, dict):
        raise ValueError("Configuration must be a JSON object")
    unknown = set(config) - {"build_args", "create_args", "settings", "image"}
    if unknown:
        raise ValueError(f"Unknown configuration keys: {', '.join(sorted(unknown))}")
    strings(config.get("create_args", []), "create_args")
    build = config.get("build_args", {})
    if not isinstance(build, dict) or any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", k)
                                         or not isinstance(v, str) for k, v in build.items()):
        raise ValueError("build_args must map argument names to strings")
    settings = config.get("settings", {})
    if not isinstance(settings, dict):
        raise ValueError("settings must be an object")
    packages = settings.get("packages", [])
    if not isinstance(packages, list):
        raise ValueError("settings.packages must be an array")
    for package in packages:
        source = package.get("source") if isinstance(package, dict) else package
        if not isinstance(source, str) or not source or source.startswith("-"):
            raise ValueError("Each package must be a source string or an object with a source")
    if "image" in config and (not isinstance(config["image"], str) or not config["image"]
                              or config["image"].startswith("-")):
        raise ValueError("image must be a nonempty image reference")
    return config


def available_path(path):
    if os.path.lexists(path):
        raise ValueError(f"Refusing to overwrite {path}")


def create(args):
    name = name_of(args.name)
    root = args.data_dir / name
    launcher = args.bin_dir / f"{name}-pi"
    available_path(root)
    available_path(launcher)
    existing = shutil.which(f"{name}-pi")
    if existing:
        raise ValueError(f"Command already exists: {existing}")
    profile = Path(args.profile).expanduser()
    if not profile.is_file():
        profile = REPO / "profiles" / f"{args.profile}.json"
    config = validate(json.loads(profile.read_text()))
    defaults = args.config_dir / "defaults.json"
    if defaults.exists():
        override = validate(json.loads(defaults.read_text()))
        # Scalar/list values replace; dictionaries merge one level deep.
        for key, value in override.items():
            if isinstance(value, dict):
                config[key] = {**config.get(key, {}), **value}
            else:
                config[key] = value
    if args.extensions is not None:
        config.setdefault("settings", {})["packages"] = json.loads(Path(args.extensions).read_text())
    validate(config)
    args.data_dir.mkdir(parents=True, exist_ok=True)
    args.bin_dir.mkdir(parents=True, exist_ok=True)
    root.mkdir(mode=0o700)
    try:
        (root / "workspace").mkdir()
        agent = root / "home/.pi/agent"
        agent.mkdir(parents=True, mode=0o700)
        write_json(agent / "settings.json", config.pop("settings", {"packages": []}))
        write_json(root / "instance.json", config)
        (root / "env").write_text("# Private environment variables: KEY=value, one per line.\n")
        (root / "env").chmod(0o600)
        (root / "empty-hooks").mkdir()
        shutil.copytree(REPO / "container", root / "container")
        # Capture the data location so this command works in future shells too.
        script = ("#!/usr/bin/env python3\nimport os, sys\n"
                  f"os.execv({sys.executable!r}, [{sys.executable!r}, {str(REPO / 'bin/pod-pi')!r}, "
                  f"'--data-dir', {str(args.data_dir)!r}, 'run', {name!r}, *sys.argv[1:]])\n")
        with launcher.open("x") as stream:
            stream.write(script)
        launcher.chmod(0o755)
    except Exception:
        shutil.rmtree(root)
        raise
    print(f"Created {name}: {root}\nLaunch: {launcher.name}\nSettings: {agent / 'settings.json'}")
    if str(args.bin_dir) not in os.environ.get("PATH", "").split(os.pathsep):
        print(f"Add {args.bin_dir} to PATH, or use {launcher} directly.")
    if args.start:
        Instance(root).action("start")


class Instance:
    def __init__(self, root):
        self.root = root.resolve()
        self.name = name_of(root.name)
        self.container = f"{self.name}-pi"
        self.config = validate(json.loads((root / "instance.json").read_text()))
        self.podman = shutil.which("podman") or "podman"
        self.unit = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "systemd/user" / f"{self.container}.service"

    def pod(self, *args, **kwargs):
        return run([self.podman, *args], **kwargs)

    def state(self):
        result = subprocess.run([self.podman, "container", "exists", self.container], capture_output=True)
        if result.returncode == 1:
            return None
        if result.returncode:
            raise RuntimeError(result.stderr.decode().strip() or "Podman could not query containers")
        info = json.loads(output([self.podman, "inspect", self.container]))[0]
        if info.get("Config", {}).get("Labels", {}).get(LABEL) != str(self.root):
            raise ValueError(f"Container {self.container} belongs to another installation; refusing to change it")
        return info["State"]["Status"]

    def image(self):
        if self.config.get("image"):
            return self.config["image"]
        digest = hashlib.sha256(json.dumps(self.config.get("build_args", {}), sort_keys=True).encode())
        for path in sorted((self.root / "container").rglob("*")):
            if path.is_file():
                digest.update(str(path.relative_to(self.root)).encode())
                digest.update(path.read_bytes())
        return f"localhost/pod-pi:{digest.hexdigest()[:20]}"

    def build(self):
        image = self.image()
        if self.config.get("image"):
            self.pod("pull", image)
        else:
            args = ["build", "-t", image]
            for key, value in self.config.get("build_args", {}).items():
                args += ["--build-arg", f"{key}={value}"]
            self.pod(*args, "-f", self.root / "container/Containerfile", self.root / "container")

    def create_container(self):
        image = self.image()
        result = subprocess.run([self.podman, "image", "exists", image])
        if result.returncode == 1:
            self.build()
        elif result.returncode:
            raise RuntimeError("Podman could not query images")
        self.pod("create", *self.config.get("create_args", []),
                 "--name", self.container, "--label", f"{LABEL}={self.root}",
                 "--init", "--hooks-dir", self.root / "empty-hooks",
                 "--env-file", self.root / "env",
                 "--volume", f"{self.root / 'workspace'}:/workspace:Z",
                 "--volume", f"{self.root / 'home'}:/root:Z", image)

    @contextlib.contextmanager
    def lock(self):
        with (self.root / ".lock").open("w") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            yield

    def start(self):
        with self.lock():
            state = self.state()
            if state is None:
                self.create_container()
            elif state in ("stopping", "paused", "removing"):
                raise ValueError(f"Container is {state}; inspect it before retrying. No data has been replaced.")
            if self.unit.exists():
                run(["systemctl", "--user", "start", self.unit.name])
            elif state != "running":
                self.pod("start", self.container)

    def ready(self):
        self.start()
        print("Waiting for Pi (first launch may install extensions)...", flush=True)
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            try:
                result = subprocess.run([self.podman, "exec", self.container, "tmux", "has-session", "-t", "pi"],
                                        capture_output=True, timeout=5)
                if result.returncode == 0:
                    return
            except subprocess.TimeoutExpired:
                pass
            if self.state() == "exited":
                break
            time.sleep(1)
        self.pod("logs", "--tail", "30", self.container)
        raise RuntimeError(f"Pi did not start; inspect {self.container} logs and retry")

    def service(self, enable):
        if not enable:
            if self.unit.exists():
                self.verify_unit()
                run(["systemctl", "--user", "disable", self.unit.name])
            return
        self.start()
        self.unit.parent.mkdir(parents=True, exist_ok=True)
        if self.unit.exists():
            self.verify_unit()
        # systemd interprets % specifiers and $ variables even in quoted words.
        def quote(value):
            return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%').replace('$', '$$') + '"'
        podman = quote(self.podman)
        self.unit.write_text(f"""# pod-pi instance: {self.root}
[Unit]
Description=Persistent Pi workspace ({self.name})
Wants=network-online.target
After=network-online.target
[Service]
Type=simple
ExecStart={podman} start --attach {self.container}
ExecStop={podman} stop --time 30 {self.container}
ExecStopPost=-{podman} stop --time 10 {self.container}
Restart=on-failure
RestartSec=5
TimeoutStopSec=45
[Install]
WantedBy=default.target
""")
        run(["systemctl", "--user", "daemon-reload"])
        run(["systemctl", "--user", "enable", "--now", self.unit.name])
        print(f"Enabled {self.unit.name} for user login. For boot without login: loginctl enable-linger {os.environ.get('USER', '<user>')}")

    def verify_unit(self):
        if self.unit.read_text().splitlines()[0] != f"# pod-pi instance: {self.root}":
            raise ValueError(f"Refusing to use unrelated service {self.unit}")

    def action(self, action):
        if action == "path":
            print(self.root)
            return
        if self.unit.exists():
            self.verify_unit()
        if action == "build":
            with self.lock():
                self.build()
        elif action == "start":
            self.ready()
        elif action in ("attach", "shell"):
            if not sys.stdin.isatty() or not sys.stdout.isatty():
                raise ValueError("Use an interactive terminal (or ssh -t) to attach or open a shell")
            self.ready()
            command = ["tmux", "attach-session", "-t", "pi"] if action == "attach" else ["bash"]
            self.pod("exec", "-it", "-w", "/workspace", "--env", f"TERM={os.environ.get('TERM', 'xterm-256color')}",
                     self.container, *command)
        elif action in ("enable", "disable"):
            self.service(action == "enable")
        elif action == "status":
            print(f"{self.container}: {self.state() or 'not created'}\n{self.root}")
        elif action == "logs":
            if self.state() is None:
                raise ValueError("Container has not been created")
            self.pod("logs", "--tail", "100", self.container)
        elif action == "check":
            self.ready()
            self.pod("exec", self.container, "bash", "-ec",
                     "pi --version; pi list; test -w /workspace; test -w /root; tmux has-session -t pi")
        elif action == "stop":
            with self.lock():
                state = self.state()
                if self.unit.exists():
                    run(["systemctl", "--user", "stop", self.unit.name])
                if state is not None:
                    self.pod("stop", "--time", "30", self.container)
        elif action == "recreate":
            with self.lock():
                state = self.state()
                if state is not None and state not in ("exited", "created", "stopped"):
                    raise ValueError(f"Run {self.container} stop first. Recreate discards container-only changes; home and workspace survive.")
                # Complete any build before removing the old container.
                self.build()
                if state is not None:
                    self.pod("rm", self.container)
                self.create_container()
            self.ready()


def main():
    parser = argparse.ArgumentParser(description="Create named, persistent Pi workspaces with rootless Podman.")
    parser.add_argument("--data-dir", type=Path, default=Path(os.environ.get("POD_PI_HOME", str(Path.home() / ".local/share/pod-pi"))))
    parser.add_argument("--bin-dir", type=Path, default=Path(os.environ.get("POD_PI_BIN_DIR", str(Path.home() / ".local/bin"))))
    parser.add_argument("--config-dir", type=Path, default=Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "pod-pi")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("install", help="Install the pod-pi command from this checkout")
    new = sub.add_parser("create", help="Create an instance and its <name>-pi command")
    new.add_argument("name")
    new.add_argument("--profile", default="default", help="Built-in profile name or JSON file")
    new.add_argument("--extensions", help="JSON file containing the desired Pi package array")
    new.add_argument("--start", action="store_true", help="Build and start immediately")
    sub.add_parser("list", help="List instance directories")
    sub.add_parser("profiles", help="List built-in profiles")
    instance = sub.add_parser("run", help="Same as <name>-pi [action]")
    instance.add_argument("name")
    instance.add_argument("action", nargs="?", default="attach", choices=ACTIONS)
    args = parser.parse_args()
    for field in ("data_dir", "bin_dir", "config_dir"):
        path = getattr(args, field).expanduser().resolve()
        if any(c in str(path) for c in ("\n", "\r", ":", "\0")):
            parser.error("Paths cannot contain newlines, colons or NULs")
        setattr(args, field, path)
    try:
        if args.command == "install":
            args.bin_dir.mkdir(parents=True, exist_ok=True)
            target = args.bin_dir / "pod-pi"
            if target.is_symlink() and target.resolve() == REPO / "bin/pod-pi":
                print(f"Already installed: {target}")
            else:
                available_path(target)
                target.symlink_to(REPO / "bin/pod-pi")
                print(f"Installed {target}; keep this checkout at {REPO}. Add {args.bin_dir} to PATH.")
        elif args.command == "create":
            create(args)
        elif args.command == "profiles":
            print("\n".join(p.stem for p in sorted((REPO / "profiles").glob("*.json"))))
        elif args.command == "list":
            for path in sorted(args.data_dir.glob("*/instance.json")):
                print(f"{path.parent.name}-pi\t{path.parent}")
        else:
            Instance(args.data_dir / name_of(args.name)).action(args.action)
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"pod-pi: {error}\n")
    except KeyboardInterrupt:
        parser.exit(130, "\n")
