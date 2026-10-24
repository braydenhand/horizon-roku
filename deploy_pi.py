"""Install or update the watcher on the Pi (or any always-on Debian/Raspberry Pi OS box).

    python deploy_pi.py --install   first time: apt packages, files, systemd service
    python deploy_pi.py             update: copy the code and config, restart

Uses the "pi" and "weather" sections of config.json and plain ssh/scp, so set
up key-based SSH to config["pi"]["ssh"] first (and passwordless sudo, the
Raspberry Pi OS default for the first user). Nothing here touches the
network configuration: the service only listens on pi.address:pi.port.
"""
import argparse
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CODE = ["watcher.py", "media.py", "roku_ecp.py"]
APT_PACKAGES = ["python3-websockets", "python3-pil"]


def ssh(target, command, stdin=None):
    result = subprocess.run(["ssh", "-o", "BatchMode=yes", target, command],
                            input=stdin, capture_output=True, text=True)
    if result.returncode != 0:
        sys.exit(f"ssh failed: {command}\n{result.stderr.strip()}")
    return result.stdout


def scp(target, files, dest):
    subprocess.run(["scp", "-q", "-o", "BatchMode=yes", *map(str, files), f"{target}:{dest}"], check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--install", action="store_true", help="also install apt packages and the systemd service")
    args = parser.parse_args()

    config = json.loads((ROOT / "config.json").read_text())
    pi = config["pi"]
    target, folder = pi["ssh"], pi.get("dir", "horizon")

    user, home = ssh(target, "echo $USER $HOME").split()
    directory = f"{home}/{folder}"
    print(f"deploying to {target}:{directory}")

    if args.install:
        print("installing", ", ".join(APT_PACKAGES))
        ssh(target, "sudo DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "
            + " ".join(APT_PACKAGES))

    ssh(target, f"mkdir -p {directory}/photos")
    scp(target, [ROOT / f for f in CODE], directory + "/")
    pi_config = {"tv": config["tv"], "listen": pi["address"], "port": pi.get("port", 8099)}
    if config.get("weather"):
        pi_config["weather"] = config["weather"]
    ssh(target, f"cat > {directory}/config.json", stdin=json.dumps(pi_config, indent=2) + "\n")

    unit = (ROOT / "pi" / "roku-watcher.service.in").read_text()
    unit = unit.replace("@USER@", user).replace("@DIR@", directory)
    ssh(target, "cat > /tmp/roku-watcher.service", stdin=unit)
    ssh(target, "sudo install -m 644 -o root -g root /tmp/roku-watcher.service "
                "/etc/systemd/system/roku-watcher.service && rm /tmp/roku-watcher.service "
                "&& sudo systemctl daemon-reload && sudo systemctl enable --quiet roku-watcher "
                "&& sudo systemctl restart roku-watcher")

    url = f"http://{pi['address']}:{pi.get('port', 8099)}/health"
    for _ in range(10):
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                print("watcher is up:", resp.read().decode())
                return
        except OSError:
            time.sleep(1)
    sys.exit(f"watcher didn't answer at {url}; check: ssh {target} journalctl -u roku-watcher -n 50")


if __name__ == "__main__":
    main()
