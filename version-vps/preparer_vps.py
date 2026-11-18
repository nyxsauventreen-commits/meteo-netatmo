"""Prepares the online version from the PC and sends it to the VPS.

Run it from a PowerShell window:  python version-vps\\preparer_vps.py
The local dashboard can stay open: it is not modified.
"""
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent
WORK = PROJECT / "vps-data"
PORT = 8771
CONNECT_TIMEOUT = 600
REMOTE = "/opt/meteo-perso"
PROGRAM = [
    "app.py", "accounts.py", "netatmo.py", "stations.py", "wind_estimate.py",
    "forecast.py", "barometer.py", "alerts.py", "neighbours.py", "meteoalarm-ca.pem", "stats.py",
    "config.example.json", "web",
]
DATA_FILES = ["config.json", "meteo.db", "token.json"]


def prepare_data(client_id, client_secret):
    """Copy the stations and measures, with the identifiers of the VPS application."""
    WORK.mkdir(exist_ok=True)
    config = json.loads((PROJECT / "config.json").read_text(encoding="utf-8-sig"))
    config.update(client_id=client_id, client_secret=client_secret)
    (WORK / "config.json").write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")
    source = sqlite3.connect(f"file:{PROJECT / 'data' / 'meteo.db'}?mode=ro", uri=True)
    target = sqlite3.connect(WORK / "meteo.db")
    try:
        source.backup(target)  # safe while the local dashboard is running
    finally:
        source.close()
        target.close()


def has_token():
    path = WORK / "token.json"
    return path.exists() and bool(json.loads(path.read_text(encoding="utf-8")).get("refresh_token"))


def connect_netatmo():
    """Run a temporary dashboard on the PC until Netatmo has given its token."""
    print("\nUne page va s'ouvrir : cliquez sur « Connecter mon compte Netatmo » et acceptez.")
    env = {**os.environ, "METEO_DATA": str(WORK)}
    server = subprocess.Popen(
        [sys.executable, str(PROJECT / "app.py"), "--port", str(PORT)],
        cwd=PROJECT, env=env,
    )
    try:
        deadline = time.monotonic() + CONNECT_TIMEOUT
        while time.monotonic() < deadline:
            if has_token():
                print("Connexion Netatmo réussie.")
                return True
            if server.poll() is not None:
                print(f"Le dashboard temporaire s'est arrêté : le port {PORT} est peut-être occupé.")
                return False
            time.sleep(2)
        print("Délai dépassé : relancez ce programme pour réessayer.")
        return False
    finally:
        server.terminate()
        server.wait()


def upload_commands(target):
    return [
        ["ssh", "-t", target, f'sudo mkdir -p {REMOTE}/app {REMOTE}/data && sudo chown -R "$USER" {REMOTE}'],
        ["scp", "-r", *(str(HERE / name) for name in PROGRAM), f"{target}:{REMOTE}/app/"],
        ["scp", *(str(WORK / name) for name in DATA_FILES), f"{target}:{REMOTE}/data/"],
        ["ssh", "-t", target, f"sudo chown -R 1000:1000 {REMOTE}"],
    ]


def upload(target):
    print("\nLe mot de passe du VPS peut être demandé plusieurs fois. Rien ne s'affiche quand vous tapez.")
    for command in upload_commands(target):
        if subprocess.run(command, check=False).returncode != 0:
            print(f"\nÉchec de : {command[0]}. Vérifiez l'accès, puis relancez ce programme.")
            return False
    return True


def main():
    print("Préparation de Météo perso pour le VPS\n")
    if (WORK / "config.json").exists():
        print("Préparation déjà commencée : je reprends à partir du dossier vps-data.")
    else:
        client_id = input("Client ID de l'application « Meteo VPS » : ").strip()
        client_secret = input("Client secret de cette application : ").strip()
        if not client_id or not client_secret:
            raise SystemExit("Les deux valeurs sont nécessaires.")
        prepare_data(client_id, client_secret)
    if not has_token() and not connect_netatmo():
        raise SystemExit(1)
    target = input("\nAccès au VPS, par exemple ubuntu@203.0.113.10 : ").strip()
    if "@" not in target:
        raise SystemExit("Il faut la forme utilisateur@adresse.")
    answer = input("Les données déjà sur le VPS seront remplacées. Continuer ? (o/n) : ").strip().lower()
    if answer != "o":
        raise SystemExit("Envoi annulé.")
    if not upload(target):
        raise SystemExit(1)
    print("\nEnvoi terminé. Créez maintenant la stack dans Portainer.")
    print("Quand le site fonctionne, supprimez le dossier vps-data : il contient des secrets.")


if __name__ == "__main__":
    main()
