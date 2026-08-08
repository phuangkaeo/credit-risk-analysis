"""
INPI Bilans-Saisis Extractor
=============================
ดึงข้อมูลงบการเงินแบบ structured JSON (ไม่ใช่ PDF) จาก INPI แล้วแปลง
code liasse (Cerfa) เป็นตัวเลขทางการเงินที่อ่านง่าย ตรงกับคอลัมน์ใน
Excel template (Total_Actif_EUR, Capitaux_Propres_EUR, ฯลฯ)

รองรับ 5 ประเภทงบ (typeBilan): C (complet), S (simplifié), K (consolidé),
B (banque), A (assurance) — โครงสร้าง code ต่างกันตามประเภท

Usage:
    1. ใช้ .env เดียวกับสคริปต์ก่อนหน้า (INPI_USERNAME / INPI_PASSWORD)
    2. python extract_financial_data.py

Output:
    data/raw/inpi/financial_data_extracted.csv
"""

import os
import csv
import time
from pathlib import Path
from datetime import datetime

import requests
from dotenv import load_dotenv

load_dotenv()

BASE_URL = "https://registre-national-entreprises.inpi.fr/api"
OUTPUT_DIR = Path("data/raw/data/raw/inpi")
OUTPUT_CSV = OUTPUT_DIR / "financial_data_extracted.csv"

SLEEP_BETWEEN_CALLS = 1.0

# ใส่เลข SIREN 17 บริษัทของคุณ (เหมือนสคริปต์ก่อนหน้า)
SIRENS = [
    "410241657", "335306445", "455500868", "301099537", "312668692",
    "314360041", "316917392", "303527154", "379812134", "401315999",
    "424323921", "433683216", "445009798", "305507303", "317586907",
    "333144889",
]

# ---------------------------------------------------------------------
# Metric maps: (code_liasse, colonne) ต่อประเภทงบ
# colonne คือ m1/m2/m3/m4 ตามที่เอกสาร INPI ระบุไว้สำหรับแต่ละ block
# ---------------------------------------------------------------------

METRIC_MAPS = {
    "C": {  # comptes complets
        "total_actif": ("CO", "m3"),
        "actif_immobilise": ("BJ", "m3"),
        "actif_circulant": ("CJ", "m3"),
        "total_passif": ("EE", "m1"),
        "capitaux_propres": ("DL", "m1"),
        "provisions_risques_charges": ("DR", "m1"),
        "total_dettes": ("EC", "m1"),
        "resultat_net": ("DI", "m1"),
        "chiffre_affaires": ("FJ", "m3"),
    },
    "K": {  # comptes consolidés (structure actif identique à C, résultat diffère)
        "total_actif": ("CO", "m3"),
        "actif_immobilise": ("BJ", "m3"),
        "actif_circulant": ("CJ", "m3"),
        "total_passif": ("EE", "m1"),
        "capitaux_propres": ("DL", "m1"),
        "provisions_risques_charges": ("DR", "m1"),
        "total_dettes": ("EC", "m1"),
        "resultat_net": ("R8", "m1"),  # résultat net part du groupe ; fallback R6
        "resultat_net_fallback": ("R6", "m1"),
        "chiffre_affaires": ("FJ", "m3"),
    },
    "S": {  # comptes simplifiés (codes numériques)
        "total_actif": ("110", "m3"),
        "actif_immobilise": ("044", "m3"),
        "actif_circulant": ("096", "m3"),
        "total_passif": ("180", "m3"),
        "capitaux_propres": ("142", "m3"),
        "provisions_risques_charges": ("154", "m3"),
        "total_dettes": ("176", "m3"),
        "resultat_net": ("136", "m3"),
        "chiffre_affaires": None,  # ไม่มี total เดียวสำเร็จรูป ต้องรวมหลาย code เอง
    },
    "B": {  # comptes de banques
        "total_actif": ("A3", "m1"),
        "actif_immobilise": None,
        "actif_circulant": None,
        "total_passif": ("P9", "m1"),
        "capitaux_propres": None,  # ต้องรวม P3..P8 เอง (ดูฟังก์ชัน extra)
        "provisions_risques_charges": None,
        "total_dettes": None,  # ต้องรวม P1+P2 เอง
        "resultat_net": ("P8", "m1"),
        "chiffre_affaires": None,
    },
    "A": {  # comptes d'assurance
        "total_actif": ("A2", "m1"),
        "actif_immobilise": None,
        "actif_circulant": None,
        "total_passif": ("P3", "m1"),
        "capitaux_propres": ("P1", "m1"),
        "provisions_risques_charges": None,
        "total_dettes": None,
        "resultat_net": ("R4", "m1"),
        "chiffre_affaires": None,
        "provisions_techniques_brutes": ("P2", "m1"),
    },
}


def get_token() -> str:
    username = os.getenv("INPI_USERNAME")
    password = os.getenv("INPI_PASSWORD")
    if not username or not password:
        raise RuntimeError("INPI_USERNAME / INPI_PASSWORD ไม่พบใน .env")

    resp = requests.post(
        f"{BASE_URL}/sso/login",
        json={"username": username, "password": password},
        timeout=30,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Login ล้มเหลว (code {resp.status_code})")
    return resp.json()["token"]


def get_attachments(siren: str, token: str) -> dict:
    resp = requests.get(
        f"{BASE_URL}/companies/{siren}/attachments",
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    if resp.status_code != 200:
        print(f"  [WARN] attachments {siren}: {resp.status_code}")
        return {}
    return resp.json()


def get_bilan_saisi(bilan_id: str, token: str) -> dict:
    resp = requests.get(
        f"{BASE_URL}/bilans-saisis/{bilan_id}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    if resp.status_code != 200:
        print(f"  [WARN] bilans-saisis/{bilan_id}: {resp.status_code}")
        return {}
    return resp.json()


def parse_amount(raw: str):
    """แปลง string จาก liasse (เช่น '-000000001127414') เป็น int (บาท/ยูโรเต็มหน่วย)"""
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def build_liasse_index(bilan_detail: dict) -> dict:
    """รวม liasses จากทุกหน้าให้เป็น dict เดียว: code -> {m1,m2,m3,m4}"""
    index = {}
    for page in bilan_detail.get("pages", []):
        for liasse in page.get("liasses", []):
            code = liasse.get("code")
            if code:
                index[code] = {
                    "m1": parse_amount(liasse.get("m1")),
                    "m2": parse_amount(liasse.get("m2")),
                    "m3": parse_amount(liasse.get("m3")),
                    "m4": parse_amount(liasse.get("m4")),
                }
    return index


def extract_metrics(liasse_index: dict, type_bilan: str) -> dict:
    metric_map = METRIC_MAPS.get(type_bilan)
    result = {}
    if metric_map is None:
        result["_warning"] = f"typeBilan '{type_bilan}' ไม่รู้จัก ข้าม mapping"
        return result

    for metric_name, spec in metric_map.items():
        if metric_name.endswith("_fallback"):
            continue
        if spec is None:
            result[metric_name] = None
            continue
        code, col = spec
        val = liasse_index.get(code, {}).get(col)
        if val is None and f"{metric_name}_fallback" in metric_map:
            fb_code, fb_col = metric_map[f"{metric_name}_fallback"]
            val = liasse_index.get(fb_code, {}).get(fb_col)
        result[metric_name] = val

    # กรณีพิเศษ: type B ต้องรวมหลาย code เอง
    if type_bilan == "B":
        p1 = liasse_index.get("P1", {}).get("m1")
        p2 = liasse_index.get("P2", {}).get("m1")
        result["total_dettes"] = (p1 or 0) + (p2 or 0) if (p1 or p2) else None
        p3 = liasse_index.get("P3", {}).get("m1") or 0
        p4 = liasse_index.get("P4", {}).get("m1") or 0
        p5 = liasse_index.get("P5", {}).get("m1") or 0
        p6 = liasse_index.get("P6", {}).get("m1") or 0
        p7 = liasse_index.get("P7", {}).get("m1") or 0
        p8 = liasse_index.get("P8", {}).get("m1") or 0
        result["capitaux_propres"] = p3 + p4 + p5 + p6 + p7 + p8

    return result


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print("กำลังเชื่อมต่อ API ของ INPI...")
    token = get_token()
    print("เชื่อมต่อสำเร็จ\n")

    rows = []

    for i, siren in enumerate(SIRENS, start=1):
        print(f"[{i}/{len(SIRENS)}] SIREN {siren} ...")
        attachments = get_attachments(siren, token)
        time.sleep(SLEEP_BETWEEN_CALLS)

        bilans_saisis = attachments.get("bilansSaisis", [])
        if not bilans_saisis:
            print("  ไม่มี bilans-saisis (อาจเป็น confidentiel หรือไม่มีข้อมูล)")
            rows.append({"SIREN": siren, "Status": "no_bilan_saisi_found"})
            continue

        # เลือก bilan ล่าสุด (เรียงตาม dateCloture)
        bilans_saisis_sorted = sorted(
            bilans_saisis, key=lambda b: b.get("dateCloture", ""), reverse=True
        )
        latest = bilans_saisis_sorted[0]

        if latest.get("confidentiality") != "Public":
            print(f"  bilan ล่าสุด confidentiality={latest.get('confidentiality')} - ข้าม")
            rows.append({"SIREN": siren, "Status": f"confidential_{latest.get('confidentiality')}"})
            continue

        bilan_id = latest["id"]
        detail_resp = get_bilan_saisi(bilan_id, token)
        time.sleep(SLEEP_BETWEEN_CALLS)

        bilan_saisi = detail_resp.get("bilanSaisi", {}).get("bilan", {})
        identite = bilan_saisi.get("identite", {})
        detail = bilan_saisi.get("detail", {})
        type_bilan = detail_resp.get("typeBilan") or identite.get("codeTypeBilan", "C")

        liasse_index = build_liasse_index(detail)
        metrics = extract_metrics(liasse_index, type_bilan)

        row = {
            "SIREN": siren,
            "Company_Name": detail_resp.get("denomination", ""),
            "Type_Bilan": type_bilan,
            "Fiscal_Year": (detail_resp.get("dateCloture") or "")[:4],
            "Date_Cloture": detail_resp.get("dateCloture", ""),
            "Total_Actif_EUR": metrics.get("total_actif"),
            "Actif_Immobilise_EUR": metrics.get("actif_immobilise"),
            "Actif_Circulant_EUR": metrics.get("actif_circulant"),
            "Capitaux_Propres_EUR": metrics.get("capitaux_propres"),
            "Provisions_Risques_Charges_EUR": metrics.get("provisions_risques_charges"),
            "Total_Dettes_EUR": metrics.get("total_dettes"),
            "Chiffre_Affaires_EUR": metrics.get("chiffre_affaires"),
            "Resultat_Net_EUR": metrics.get("resultat_net"),
            "Provisions_Techniques_Brutes_EUR": metrics.get("provisions_techniques_brutes"),
            "Bilan_Saisi_Id": bilan_id,
            "Status": "ok",
        }
        rows.append(row)
        print(f"  OK - type={type_bilan}, Total_Actif={row['Total_Actif_EUR']}")

    # เขียน CSV
    fieldnames = [
        "SIREN", "Company_Name", "Type_Bilan", "Fiscal_Year", "Date_Cloture",
        "Total_Actif_EUR", "Actif_Immobilise_EUR", "Actif_Circulant_EUR",
        "Capitaux_Propres_EUR", "Provisions_Risques_Charges_EUR",
        "Total_Dettes_EUR", "Chiffre_Affaires_EUR", "Resultat_Net_EUR",
        "Provisions_Techniques_Brutes_EUR", "Bilan_Saisi_Id", "Status",
    ]
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in fieldnames})

    n_ok = sum(1 for r in rows if r.get("Status") == "ok")
    print(f"\nเสร็จสิ้น: ดึงข้อมูลได้ {n_ok}/{len(SIRENS)} บริษัท")
    print(f"บันทึกที่: {OUTPUT_CSV.resolve()}")


if __name__ == "__main__":
    main()