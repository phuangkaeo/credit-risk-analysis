# Corporate Credit Risk Analysis — Nord (59)

End-to-end credit risk data platform over a portfolio of **16 French companies**
registered in the Nord department (59), built on a Databricks Medallion
architecture (Bronze / Silver / Gold) with Unity Catalog, a Power BI executive
layer, and a Probability of Default (PD) model.

Capstone project (*projet fil rouge*) for the **M2i Data Analyst** programme,
certified under **RNCP 39103 — Chargé de développement de solutions applicatives
ou logicielles**.

> **Scope disclaimer.** With n = 16 companies, every result in this repository is
> **descriptive and non-generalizable**. Portfolio-level aggregates are the unit of
> analysis; no statistical inference about French SMEs at large is claimed, and
> the PD model is a methodological demonstration, not a production scoring engine.

---

## 1. Portfolio

| Sector | Companies |
|---|---|
| Assurance | ARIAL CNP ASSURANCES, CFE (Caisse Fraternelle d'Épargne) |
| Retail_Distribution | AFIBEL, … |
| Manufacturing_Industrie | YGNIS INDUSTRIE, ENTREPOSE INDUSTRIES, BOONE COMENOR METALIMPEX, CARGILL HAUBOURDIN, … |
| Tech_Services | SOFTTHINKS, PROSYST, KINGFISHER INFORMATION TECHNOLOGY SERVICES (FRANCE), … |
| Food_Beverage | SOURCES DU COL ST JEAN, LIONOR, … |

SIREN list is the single source of truth in `inpi_dowloader.py` (`SIRENS`).
One additional SIREN (`783712045`, a *mutuelle*) is **excluded**: mutuelles fall
outside the RNE scope and return no exploitable *bilan*.

---

## 2. Architecture

```
  INPI RNE API                    Excel (manual capture)
  (identity + bilans-saisis)      financial_data template
        │                                  │
        ▼                                  ▼
  ┌───────────────────────────────────────────────────┐
  │ BRONZE — credit_risk_project_bronze               │
  │   bronze_company_registry   (nested RNE JSON)     │
  │   bronze_financial_data     (flat financials)     │
  │   Volume: /Volumes/.../bronze_raw_files           │
  └───────────────────────────────────────────────────┘
        │
        ▼
  ┌───────────────────────────────────────────────────┐
  │ SILVER — credit_risk_project_silver               │
  │   silver_company     ← 02 Silver Companies        │
  │   silver_bilans      ← 02 Silver Bilans           │
  │   silver_red_flags   ← 02 Silver Red Flags        │
  └───────────────────────────────────────────────────┘
        │
        ▼
  ┌───────────────────────────────────────────────────┐
  │ GOLD — credit_risk_project_gold                   │
  │   gold_credit_risk_summary                        │
  │   (1 row per siren × fiscal_year, denormalized)   │
  └───────────────────────────────────────────────────┘
        │                        │
        ▼                        ▼
   Power BI (BC03)        scikit-learn PD model (BC04)
```

**Platform:** Databricks Free Edition — *serverless compute only*, no classic
clusters. Unity Catalog: catalog `credit_risk_project`, schemas
`credit_risk_project_bronze` / `_silver` / `_gold`.

**Why a wide Gold table and not a star schema?** At 16 companies the fact/dimension
split adds joins without adding value. The decision is documented rather than
hidden; a star schema variant can be produced on demand if the BC01 rubric
requires a normalization demonstration.

---

## 3. Repository layout

```
credit-risk-analysis/
├── README.md
├── .env.example                      # template — never commit a real .env
├── .gitignore
├── inpi_dowloader.py                 # RNE identity JSON  → data/raw/inpi/{siren}/{siren}.json
├── pappers_extract_data.py           # RNE bilans-saisis  → financial_data_extracted.csv
├── notebooks/
│   ├── 01 - Bronze Data Ingestion.ipynb
│   ├── 02 - Silver Companies.ipynb
│   ├── 02 - Silver Bilans.ipynb
│   ├── 02 - Silver Red Flags.ipynb
│   └── 03 - Gold Credit Risk Summary.ipynb
├── data/
│   └── raw/inpi/                     # gitignored — API output
├── powerbi/                          # BC03 .pbix + rapport exécutif
├── model/                            # BC04 PD model, MLflow, Dockerfile
└── docs/                             # BC01 cahier des charges, MCD/MLD
```

---

## 4. Setup

### 4.1 Credentials

Authentication uses the INPI RNE account. **Credentials are never hardcoded and
never printed.** Create a `.env` next to the scripts:

```dotenv
INPI_USERNAME=your_email@example.com
INPI_PASSWORD=your_password
```

`.env` is loaded with `python-dotenv` (`load_dotenv()`) and is listed in
`.gitignore`. Tokens returned by `/sso/login` are held in memory only — they are
never logged, printed, or written to disk.

### 4.2 Python environments

Two virtual environments, deliberately separated:

| Env | Python | Purpose | Key packages |
|---|---|---|---|
| `venv-extract` | 3.14 | Local API extraction | `requests`, `python-dotenv` |
| `venv-databricks` | 3.12 | Run Silver/Gold notebooks from PyCharm | `databricks-connect==18.3` |

`databricks-connect` pins the Python version; extraction scripts have no such
constraint, so keeping them apart avoids a forced downgrade.

```bash
python -m venv venv-extract
venv-extract\Scripts\activate
pip install requests python-dotenv
```

### 4.3 Databricks CLI

```bash
winget install Databricks.DatabricksCLI     # v1.11.0
databricks configure
```

`~/.databrickscfg` must contain:

```ini
[DEFAULT]
host = https://<workspace>.cloud.databricks.com
token = <PAT>
serverless_compute_id = auto
```

> On Free Edition the JetBrains Databricks plugin's cluster dropdown is
> permanently empty. That is expected — `serverless_compute_id = auto` is the
> only compute path.

---

## 5. Running the pipeline

Order matters: Silver notebooks are dependency-aware.

```
1. python inpi_dowloader.py           # RNE identity JSON per SIREN
2. python pappers_extract_data.py     # bilans-saisis → CSV
3. Upload outputs to the Bronze Volume
4. 01 - Bronze Data Ingestion
5. 02 - Silver Companies              # produces capital_social_eur
6. 02 - Silver Bilans                 # depends on Silver Companies
7. 02 - Silver Red Flags              # depends on Silver Bilans
8. 03 - Gold Credit Risk Summary
```

Steps 4–8 are chained as a Databricks Workflows DAG. All schema/table creation
uses `CREATE SCHEMA IF NOT EXISTS` + `mode("overwrite")`, so the pipeline is
**idempotent** — re-running never duplicates rows.

---

## 6. Data model

### Bronze

| Table | Grain | Source |
|---|---|---|
| `bronze_company_registry` | 1 row / SIREN, nested RNE JSON kept intact | INPI `/companies/{siren}` |
| `bronze_financial_data` | 1 row / SIREN, latest public *bilan* | INPI `/bilans-saisis/{id}` + Excel |

Bronze keeps the raw nested structure on purpose — flattening happens in Silver,
so a mapping mistake never forces a re-extraction (and a re-burn of API quota).

### Liasse code mapping

INPI returns Cerfa *liasse* codes, not readable metrics. `pappers_extract_data.py`
maps them per `typeBilan`:

| typeBilan | Statement type | Capitaux propres |
|---|---|---|
| `C` | Comptes complets | `DL` / m1 |
| `K` | Comptes consolidés | `DL` / m1 (résultat via `R8`, fallback `R6`) |
| `S` | Comptes simplifiés | `142` / m3 |
| `B` | Banques | computed as `P3+P4+P5+P6+P7+P8` |
| `A` | Assurance | `P1` / m1 |

Insurance companies (ARIAL CNP, CFE) follow the **Code des assurances**, not the
PCG — different balance-sheet structure entirely, hence a distinct metric map and
a separate Silver treatment. `Provisions_Techniques_Brutes_EUR` only exists for
`typeBilan = A`.

### Gold — `gold_credit_risk_summary`

One row per `(siren, fiscal_year)`, carrying identity, financials, derived
ratios, and:

| Column | Definition |
|---|---|
| `n_red_flags` | count of triggered flags |
| `risk_score` | 0–4, sum of boolean red flags |
| `risk_tier` | `Faible` / `Modéré` / `Élevé` / `Critique` |

### Red flags (BC04 inputs)

| Flag | Trigger | Why it matters |
|---|---|---|
| **Negative capitaux propres** | `capitaux_propres_eur < 0` | Accumulated losses exceed equity. Under French law (art. L223-42 / L225-248) this triggers a mandatory shareholder vote on continuation. **Strongest single distress signal in this portfolio.** |
| Negative net result | `resultat_net_eur < 0` | Loss-making year |
| High leverage | `total_dettes / total_actif` above threshold | Debt-funded balance sheet |
| Weak liquidity | `actif_circulant / total_dettes` below threshold | Short-term repayment strain |

Sector note: **ARIAL CNP reinsures 100 % of its operations** (`Reassurance_Pct`).
Its solvency ratios therefore describe a pass-through vehicle, not a risk-bearing
insurer, and must not be read against the same benchmarks as the others.

---

## 7. Known limitations & open issues

| # | Issue | Impact | Status |
|---|---|---|---|
| 1 | **16 → 15 rows** after the Silver join — one SIREN is lost | One company silently missing from Gold and from BI | Open — suspected join key mismatch |
| 2 | `pappers_extract_data.py` discards `liasse_index` | `raw_liasse_json` stays NULL in Silver; no audit trail back to Cerfa codes | Open |
| 3 | `OUTPUT_DIR = Path("data/raw/data/raw/inpi")` — path segment duplicated in **both** scripts | Files land in an unintended nested folder | Open — easy fix |
| 4 | No `sector` column in Bronze/Silver — only `code_ape` | Sector mapping is a **hardcoded dict** of 16 NAF codes in the Gold notebook | Documented, not resolved |
| 5 | Script name `pappers_extract_data.py` calls the **INPI** API, not Pappers | Misleading for a reviewer | Rename recommended |
| 6 | SIREN typed as integer in `bronze_financial_data`, string in `bronze_company_registry` | Silent join failure | Mitigated by explicit cast to string |
| 7 | Rows with `Status != "ok"` (confidential / no bilan) | Would pollute financial aggregates | Filtered in Silver Bilans |

---

## 8. Deliverables ↔ RNCP 39103 blocks

| Block | Deliverable | State |
|---|---|---|
| **BC01** | Cahier des charges, architecture, MCD/MLD | In progress |
| **BC02** | Extraction scripts, Medallion ETL, front-end | Bronze ✅ · Silver ✅ · Gold ✅ |
| **BC03** | Power BI dashboard (portfolio aggregate) + rapport exécutif | To do |
| **BC04** | PD model (scikit-learn), MLflow tracking, Docker, audit | To do |

PD model training uses the Kaggle *Give Me Some Credit* dataset as a
methodological base; the 16-company portfolio is far too small to fit a model on
directly, and saying so explicitly is part of the deliverable.

---

## 9. Security & data handling rules

- No credential is ever hardcoded — `.env` + `load_dotenv()`, always.
- No token, password, or `Authorization` header is ever logged or printed.
- `.env`, `data/raw/`, and `*.pbix` caches stay out of version control.
- All data is **public legal filing data** (RNE open register). No personal data
  beyond legally published *représentants* is stored or processed.
- API calls are rate-limited to 1 req/s (`SLEEP_BETWEEN_CALLS`) to respect the
  INPI quota; `429` responses are logged as failures rather than retried blindly.

---

## 10. Author

**Phichet Phuangkaeo** — Data Analyst trainee, M2i Formation Lille
Repo: [`phuangkaeo/credit-risk-analysis`](https://github.com/phuangkaeo/credit-risk-analysis)
