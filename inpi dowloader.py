"""
Extraction des formalités INPI (Registre National des Entreprises) en JSON.

Usage :
    1. Crée un fichier .env à côté de ce script avec :
         INPI_USERNAME=ton_email
         INPI_PASSWORD=ton_mot_de_passe
    2. Renseigne la liste SIRENS ci-dessous (tes 17 entreprises).
    3. python inpi_extract.py

Sortie :
    data/raw/inpi/{siren}/{siren}.json  (un fichier par entreprise)
    data/raw/inpi/extraction_log.csv     (log des succès/échecs)
"""

import os
import csv
import json
import time
from pathlib import Path
from datetime import datetime

import requests
from dotenv import load_dotenv

# ----------------------------------------------------------------------
# CONFIGURATION
# ----------------------------------------------------------------------

load_dotenv()  # Load USERNAME and PASSWORD from .env

BASE_URL = "https://registre-national-entreprises.inpi.fr/api"
LOGIN_URL = f"{BASE_URL}/sso/login"
COMPANY_URL = f"{BASE_URL}/companies/{{siren}}"

OUTPUT_DIR = Path("data/raw/data/raw/inpi")
LOG_FILE = OUTPUT_DIR / "extraction_log.csv"

# 1 second pause between the call
SLEEP_BETWEEN_CALLS_SECONDS = 1.0

# --- RIREN Number ---
SIRENS = [
    "410241657", # ARIAL CNP ASSURANCES
    #"783712045",
    "335306445", # CFE CAISSE FRATERNELLE D EPARGNE
    "455500868",
    "301099537",
    "312668692",
    "314360041",
    "316917392",
    "303527154",
    "379812134",
    "401315999",
    "424323921",
    "433683216",
    "445009798",
    "305507303",
    "317586907",
    "333144889"
]


# ----------------------------------------------------------------------
# AUTHENTIFICATION
# ----------------------------------------------------------------------

def get_token() -> str:
    """Connect with API and return Bearer token."""
    username = os.getenv("INPI_USERNAME")
    password = os.getenv("INPI_PASSWORD")

    if not username or not password:
        raise RuntimeError(
            "INPI_USERNAME / INPI_PASSWORD manquants. "
            "Vérifie ton fichier .env."
        )

    response = requests.post(
        LOGIN_URL,
        json={"username": username, "password": password},
        timeout=30,
    )

    if response.status_code != 200:
        raise RuntimeError(
            f"Échec de connexion à l'API INPI "
            f"(code {response.status_code}) : {response.text[:300]}"
        )

    token = response.json().get("token")
    if not token:
        raise RuntimeError("Réponse de login inattendue : pas de token trouvé.")

    return token


# ----------------------------------------------------------------------
# Extract Data
# ----------------------------------------------------------------------

def fetch_company(siren: str, token: str) -> dict:
    """Receiving JSON from each RIREN."""
    headers = {"Authorization": f"Bearer {token}"}
    url = COMPANY_URL.format(siren=siren)

    response = requests.get(url, headers=headers, timeout=30)

    if response.status_code == 200:
        return {"status": "ok", "data": response.json()}

    if response.status_code == 401:
        return {"status": "error", "message": "401 - Invalid Token or Expired"}

    if response.status_code == 403:
        return {
            "status": "error",
            "message": "403 - SIREN Not Available ",
        }

    if response.status_code == 429:
        return {"status": "error", "message": "429 - Out of Quotas"}

    return {
        "status": "error",
        "message": f"{response.status_code} - {response.text[:200]}",
    }


# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------

def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print("Connecting API's INPI")
    token = get_token()
    print("Successful Connected.\n")

    log_rows = []

    for i, siren in enumerate(SIRENS, start=1):
        print(f"[{i}/{len(SIRENS)}] SIREN {siren} ...", end=" ")

        result = fetch_company(siren, token)

        if result["status"] == "ok":
            siren_dir = OUTPUT_DIR / siren
            siren_dir.mkdir(parents=True, exist_ok=True)
            out_path = siren_dir / f"{siren}.json"
            out_path.write_text(
                json.dumps(result["data"], ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            print("OK")
            log_rows.append(
                {
                    "siren": siren,
                    "status": "success",
                    "message": "",
                    "timestamp": datetime.now().isoformat(timespec="seconds"),
                }
            )
        else:
            print(f"FAILED ({result['message']})")
            log_rows.append(
                {
                    "siren": siren,
                    "status": "failed",
                    "message": result["message"],
                    "timestamp": datetime.now().isoformat(timespec="seconds"),
                }
            )

        time.sleep(SLEEP_BETWEEN_CALLS_SECONDS)

    # write log
    with open(LOG_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["siren", "status", "message", "timestamp"])
        writer.writeheader()
        writer.writerows(log_rows)

    n_ok = sum(1 for r in log_rows if r["status"] == "success")
    n_fail = len(log_rows) - n_ok
    print(f"\nTerminated : {n_ok} Success, {n_fail} Failed.")
    print(f"Log Detailed : {LOG_FILE}")


if __name__ == "__main__":
    main()