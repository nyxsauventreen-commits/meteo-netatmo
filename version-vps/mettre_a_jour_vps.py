"""Sends the program to the VPS and restarts it. The data on the VPS is not touched.

Double-click mettre-a-jour-vps.bat in the project folder.
"""
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
REMOTE = "/opt/meteo-perso/app"
CONTAINER = "meteo-perso"
PROGRAM = [
    "app.py", "accounts.py", "netatmo.py", "stations.py", "wind_estimate.py",
    "forecast.py", "barometer.py", "alerts.py", "neighbours.py", "meteoalarm-ca.pem", "stats.py",
    "config.example.json", "web",
]


def commands(target):
    return [
        ["ssh", "-t", target, f'sudo chown -R "$USER" {REMOTE}'],
        ["scp", "-r", *(str(HERE / name) for name in PROGRAM), f"{target}:{REMOTE}/"],
        ["ssh", "-t", target, f"sudo chown -R 1000:1000 {REMOTE} && sudo docker restart {CONTAINER}"],
    ]


def update(target):
    print("\nLe mot de passe du VPS sera demandé plusieurs fois. Rien ne s'affiche quand vous tapez.")
    for command in commands(target):
        if subprocess.run(command, check=False).returncode != 0:
            print(f"\nÉchec de : {command[0]}. Rien n'a été perdu, vous pouvez relancer.")
            return False
    return True


def main():
    print("Mise à jour de Météo perso sur le VPS (les données ne sont pas touchées)\n")
    target = input("Accès au VPS, par exemple ubuntu@203.0.113.10 : ").strip()
    if "@" not in target:
        raise SystemExit("Il faut la forme utilisateur@adresse.")
    if update(target):
        print("\nMise à jour terminée. Rechargez la page du site.")


if __name__ == "__main__":
    main()
