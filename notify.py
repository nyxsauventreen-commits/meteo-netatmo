"""Notifications: what triggers them, and sending by email (the site's own notifications are in webpush.py).

Each person chooses the events and the channels. An event is sent once when it starts,
and again only after it has ended: a frost night gives one message, not one per poll.
"""
import email.message
import re
import smtplib
import ssl

ADDRESS = re.compile(r"^[^@\s<>\"',;]{1,64}@[^@\s<>\"',;]{1,190}\.[A-Za-z]{2,}$")
TIMEOUT = 15
STALE = 3600  # a reading older than this is not used for the weather events

# key -> (label shown on the page, default threshold, unit); None: no threshold.
EVENTS = {
    "vigilance": ("Vigilance orange ou rouge", None, ""),
    "frost": ("Gel", 0.0, "°C"),
    "heat": ("Chaleur", 35.0, "°C"),
    "rain": ("Forte pluie", 10.0, "mm/h"),
    "wind": ("Rafales", 70.0, "km/h"),
    "offline": ("Station muette", 3.0, "h"),
}
LIMITS = {"frost": (-20, 10), "heat": (20, 50), "rain": (1, 100), "wind": (20, 200), "offline": (1, 48)}
DEFAULT_EVENTS = ["vigilance", "frost", "rain", "wind", "offline"]
STRONG = {"orange", "red"}
LEVEL_NAMES = {"orange": "orange", "red": "rouge"}


class NotifyError(Exception):
    """A setting is refused or a message could not be sent; the text is shown to the person."""


def default_settings():
    return {
        "email": False,
        "address": "",
        "events": list(DEFAULT_EVENTS),
        "thresholds": {key: value for key, (_, value, _) in EVENTS.items() if value is not None},
    }


def clean_settings(form, current):
    """Check what the page sends."""
    if not isinstance(form, dict):
        raise NotifyError("Réglages refusés")
    address = str(form.get("address", "")).strip()
    wants_email = form.get("email") is True
    if wants_email and not ADDRESS.match(address):
        raise NotifyError("Adresse e-mail incorrecte")
    if address and not ADDRESS.match(address):
        raise NotifyError("Adresse e-mail incorrecte")
    events = form.get("events", [])
    if not isinstance(events, list) or any(event not in EVENTS for event in events):
        raise NotifyError("Événement inconnu")
    thresholds = dict(current["thresholds"])
    given = form.get("thresholds", {})
    if not isinstance(given, dict):
        raise NotifyError("Seuils refusés")
    for key, value in given.items():
        if key not in LIMITS or isinstance(value, bool) or not isinstance(value, (int, float)):
            raise NotifyError("Seuils refusés")
        low, high = LIMITS[key]
        if not low <= value <= high:
            raise NotifyError(f"{EVENTS[key][0]} : un seuil de {low} à {high} {EVENTS[key][2]}")
        thresholds[key] = float(value)
    return {
        **current,
        "email": wants_email,
        "address": address,
        "events": [key for key in EVENTS if key in events],
        "thresholds": thresholds,
    }


def number(value):
    return f"{value:.1f}".rstrip("0").rstrip(".").replace(".", ",")


def weather_events(settings, station, reading, warnings, now, active):
    """Return {key: (title, message)} for what is happening now at one station.

    `active` holds the keys already sent: a slightly lower level keeps them going, so that a
    value hovering around the threshold does not send a message at each poll.
    """
    wanted = set(settings["events"])
    limits = settings["thresholds"]
    name = station["name"]
    found = {}
    if "vigilance" in wanted:
        for warning in warnings:
            if warning["level"] in STRONG and warning["end"] > now:
                key = f"vigilance:{warning['hazard']}:{warning['level']}:{warning['start']}"
                title = f"Vigilance {LEVEL_NAMES[warning['level']]} {warning['hazard']} : {name}"
                found[key] = (title, f"{warning['icon']} {warning['area']} : vigilance {LEVEL_NAMES[warning['level']]} "
                                     f"{warning['hazard']}. Consultez les sources officielles.")
    age = now - reading["ts"] if reading else None
    if "offline" in wanted and age is not None:
        silent = limits["offline"] * 3600
        if age >= silent or ("offline" in active and age >= silent / 2):
            hours = round(age / 3600)
            found["offline"] = (f"{name} ne répond plus", f"Aucune mesure depuis {hours} h environ.")
    if reading is None or age > STALE:
        return found
    temperature = reading.get("temperature")
    rain = reading.get("rain_60min")
    gust = reading.get("gust_strength")
    checks = (
        ("frost", temperature, lambda value, limit, on: value <= limit + (1 if on else 0),
         lambda value: (f"Gel : {name}", f"{number(value)} °C en ce moment.")),
        ("heat", temperature, lambda value, limit, on: value >= limit - (1 if on else 0),
         lambda value: (f"Chaleur : {name}", f"{number(value)} °C en ce moment.")),
        ("rain", rain, lambda value, limit, on: value >= limit * (0.5 if on else 1),
         lambda value: (f"Forte pluie : {name}", f"{number(value)} mm sur la dernière heure.")),
        ("wind", gust, lambda value, limit, on: value >= limit * (0.8 if on else 1),
         lambda value: (f"Rafales : {name}", f"Rafales à {number(value)} km/h.")),
    )
    for key, value, is_on, text in checks:
        if key in wanted and value is not None and is_on(value, limits[key], key in active):
            found[key] = text(value)
    return found


def send_email(smtp, address, title, message):
    """Send through an SMTP server over TLS, e.g. Gmail with an app password."""
    if not smtp:
        raise NotifyError("L'envoi d'e-mails n'est pas configuré sur ce serveur")
    mail = email.message.EmailMessage()
    mail["Subject"] = title
    mail["From"] = f"{smtp.get('sender_name', 'Météo')} <{smtp['user']}>"
    mail["To"] = address
    mail.set_content(f"{message}\n\n-- \nMessage automatique de votre tableau de bord météo. "
                     "Les notifications se règlent dans la page, menu « Mes notifications ».")
    try:
        with smtplib.SMTP_SSL(smtp.get("server", "smtp.gmail.com"), int(smtp.get("port", 465)),
                              context=ssl.create_default_context(), timeout=TIMEOUT) as server:
            server.login(smtp["user"], smtp["password"])
            server.send_message(mail)
    except smtplib.SMTPAuthenticationError:
        raise NotifyError("Connexion refusée par le serveur d'e-mails : vérifiez le mot de passe d'application") from None
    except (OSError, smtplib.SMTPException) as err:
        raise NotifyError(f"E-mail non envoyé : {err}") from None


def check_mailbox(user, password):
    """Try to sign in to Gmail with an app password; the page saves the mailbox only if it works."""
    user, password = user.strip(), password.replace(" ", "")  # Google shows it in groups of four
    if not ADDRESS.match(user):
        raise NotifyError("Adresse Gmail incorrecte")
    if not 8 <= len(password) <= 64:
        raise NotifyError("Collez le mot de passe d'application de 16 lettres donné par Google")
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context(), timeout=TIMEOUT) as server:
            server.login(user, password)
    except smtplib.SMTPAuthenticationError:
        raise NotifyError(
            "Google refuse la connexion : il faut un mot de passe d'application, pas le mot de passe du compte"
        ) from None
    except (OSError, smtplib.SMTPException) as err:
        raise NotifyError(f"Gmail injoignable : {err}") from None
    return {"server": "smtp.gmail.com", "port": 465, "user": user, "password": password, "sender_name": "Météo"}


def smtp_settings(config, environ, saved=None):
    """The mailbox used to send: config.json ("smtp"), then the one saved from the page,
    then METEO_SMTP_USER / METEO_SMTP_PASSWORD."""
    smtp = dict(config.get("smtp") or {})
    if saved and saved.get("user") and saved.get("password"):
        smtp = dict(saved)
    if environ.get("METEO_SMTP_USER"):
        smtp["user"] = environ["METEO_SMTP_USER"]
    if environ.get("METEO_SMTP_PASSWORD"):
        smtp["password"] = environ["METEO_SMTP_PASSWORD"]
    return smtp if smtp.get("user") and smtp.get("password") else None
