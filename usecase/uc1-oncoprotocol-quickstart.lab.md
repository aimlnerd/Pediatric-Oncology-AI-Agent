---
id: swg-bspho26-uc1-oncoprotocol-quickstart
summary: Build a zero-hallucination Belgian pediatric hematology-oncology protocol assistant with 2-tier hospital filtering (UZA, UZL, CHU, UCL, HUDERF, UZG), sub-protocol deep search, and Golden Dataset Q1-Q9 verification.
authors: Jerome CG Paquay (@jpaquay),Dr. Ruben van Paemel (UZ Gent),Marina Bosso Bosso (@mbossobosso)
keywords: category:Cloud,product:VertexAI,product:ADK,docType:Codelab
layout: paginated
---

# UC1 Quickstart: Virtual Assistant in Pediatric Hematology & Oncology (OncoProtocol-Assist)

## 1. Introduction & Clinical Challenge

Duration: 02:00

Welcome to **Use Case 1 (OncoProtocol-Assist)** at **Solve with Google: BSPHO 2026 (Wintercircus Ghent)**.

Pediatric hematology and oncology treatment protocols (`ALLTogether1 v6.0`, `SIOP-HR-NBL2`, `CHIP-AML22`, `FaR-RMS v2.0c`, `BEACON2`, `SIOP Umbrella`) are dense, 150-to-300-page PDFs packed with multi-arm stratification flowcharts, age/weight dose modification tables, and cross-referenced sub-protocols. During an acute clinical shift, an oncologist cannot afford a hallucinated dose threshold or an answer drawn from a trial in which their hospital does not participate.

In this quickstart codelab, you will build and validate a **2-Tier Sovereign Clinical Protocol Assistant** in `europe-west1` that:

1. **Filters by Belgian Hospital Participation (Tier 1)**: Restricts retrieval across the **6 Belgian pediatric oncology centers** (`UZA`, `UZL`, `CHU`, `UCL`, `HUDERF`, `UZG`) across **27 master protocols** and **5 `ALLTogether1` sub-protocols**.
2. **Escalates via Sub-Protocol Deep Search (Tier 2)**: Automatically expands search into the 5 `ALLTogether1` sub-protocols (`InO`, `A2G Glucarpidase`, `Maintenance`, `Pancreatitis`, `Thrombosis`) when complications like stroke-like events (SLS), sinus venous thrombosis, or high-dose methotrexate toxicity arise.
3. **Asks Proactive Clarifying Questions**: When a clinician asks an under-specified question (e.g., *"A patient has 150,000 WBC, what should I do?"* or *"A 3-year-old with metastatic neuroblastoma—which arm?"*), the assistant asks for the missing clinical discriminator (AML vs. ALL; MYCN amplification status) before citing protocol logic.
4. **Enforces Verbatim Citations & Out-of-Scope Refusals**: Every clinical answer includes `[Protocol_ID, Version, Page, Section]` citations and strictly refuses adult or non-oncology queries.

```mermaid
flowchart LR
    Doc["Clinician Query + Hospital ID (UZA / UZL / CHU / UCL / HUDERF / UZG)"] --> HSV["HippocraticSafetyValve (NISS PII + Scope Guard)"]
    HSV --> Tier1["Tier 1: Belgian Hospital Protocol Matrix (27 Master Protocols)"]
    Tier1 --> Clarify{"Missing Clinical Discriminator? (AML vs ALL / MYCN / Age)"}
    Clarify -- "Yes" --> Ask["Ask Targeted Clarifying Question"]
    Clarify -- "No" --> Tier2["Tier 2: Multimodal RAG + ALLTogether1 Sub-Protocol Deep Search"]
    Tier2 --> Cite["Verified Response with [Protocol_ID, Version, Page, Section]"]
```

> aside positive
> **Clinical Lead & CE POC**: Dr. Ruben van Paemel (`ruben.vanpaemel@uzgent.be`, UZ Gent) & Marina Bosso Bosso (`@mbossobosso`, Google Cloud).

## 2. Environment & Dataset Setup

Duration: 03:00

All 27 master protocol PDFs, 5 `ALLTogether1` sub-protocol PDFs, the `FaR-RMS` Dutch patient brochure, and the official `SwG-UC1-Golden Dataset and mapping table.xlsx` are pre-staged in Cloud Storage.

### Sync the UC1 Protocol Corpus to Your Workspace

Open **Konsole** (or your terminal) and run the following commands to pull the UC1 dataset and verify all 32 PDFs:

```bash
export PROJECT_ID=$(gcloud config get-value project)
export GOOGLE_CLOUD_LOCATION="europe-west1"
mkdir -p /config/Desktop/use-cases

# Pull from your project bucket (or sovereign Ghent mirror)
gcloud storage cp -r "gs://use-cases-${PROJECT_ID}/1. Virtual Assistant" /config/Desktop/use-cases/ 2>/dev/null || \
  gcloud storage cp -r "gs://swg-bspho26-ghent-uc1-protocols/*" "/config/Desktop/use-cases/1. Virtual Assistant/"

ls -1 "/config/Desktop/use-cases/1. Virtual Assistant/protocols/" | wc -l
ls -1 "/config/Desktop/use-cases/1. Virtual Assistant/subprotocols/" | wc -l
```

You should see output similar to:

```console
27
5
```

> aside negative
> **Data Governance Guardrail**: Never upload real patient names, hospital MRNs, or Belgian National Register numbers (`NISS` / `INSZ`) to any prompt or storage bucket. All hackathon cases use 100% synthetic vignettes.

## 3. Tier-1 Belgian Hospital Protocol Filtering & Sub-Protocol Deep Search

Duration: 05:00

Not every Belgian hospital participates in every SIOP or ITCC trial. For example, **UZ Gent (`UZG`)** participates in all **27** protocols in the dataset, whereas **UZ Antwerpen (`UZA`)** participates in **21** (e.g., `UZA` does not open `ALCL-99`, `BEACON2`, `EsPhALL2017`, `HEM-iSMART`, or `Inter-B-NHL ritux 2010`).

### Create the Hospital Filter & Golden Dataset Runner

Run the following `cat << 'EOF'` block in your terminal inside `/usr/local/google/home/jpaquay/dev/cloud-gtm/SwG-hackthehack` (or your project directory) to test the Tier-1 hospital matrix and Tier-2 sub-protocol escalation:

```bash
cat << 'EOF' > test_uc1_quickstart.py
from agents.uc1_oncoprotocol.agent import (
    filter_protocols_by_hospital,
    answer_golden_protocol_question,
)

# 1. Compare active protocol counts between UZG (Ghent) and UZA (Antwerp)
uzg = filter_protocols_by_hospital("UZG", include_subprotocols=True)
uza = filter_protocols_by_hospital("UZA", include_subprotocols=True)

print(f"UZG Active Protocols: {uzg['active_protocol_count']} (+ {uzg['active_subprotocol_count']} ALLTogether1 sub-protocols)")
print(f"UZA Active Protocols: {uza['active_protocol_count']} | Excluded at UZA: {uza['excluded_at_this_center']}")
EOF
python3 test_uc1_quickstart.py
```

You should see output similar to:

```console
UZG Active Protocols: 27 (+ 5 ALLTogether1 sub-protocols)
UZA Active Protocols: 21 | Excluded at UZA: ['ALCL-99', 'BEACON2', 'EsPhALL2017', 'HEM-iSMART', 'ICC APL study 02', 'SIOP-HR-NBL2']
```

## 4. Multimodal Protocol Grounding & Golden Dataset Q1–Q9

Duration: 07:00

The UC1 evaluation rubric tests your assistant against **9 Golden Dataset scenarios (`Q1`–`Q9`)** authored by Dr. Ruben van Paemel:

- **`Q1` (`BEACON2` vs `HR-NBL2`)**: Starting criteria (`PLT`, `ANC`, liver transaminases, neurological status, pain premedication) before initiating **dinutuximab beta**.
- **`Q2` (`HR-NBL2` Staging)**: A 3-year-old with metastatic neuroblastoma (`>18 months`, Stage M) requires **MYCN amplification status** clarification before assigning high-risk arm specifics.
- **`Q3` (`FaR-RMS v2.0c` Section 12.1)**: A child with rhabdomyosarcoma has **3 subpleural lung nodules `<5 mm`**. The protocol explicitly states **no lung biopsy is indicated** (`<=4 nodules <5 mm` = negative pulmonary metastasis; avoid thoracotomy).
- **`Q4` (`150,000 WBC` Hyperleukocytosis)**: Must ask whether the patient has **AML (`CHIP-AML22`)** or **ALL (`ALLTogether1`)**, then return cytoreduction/hydration/rasburicase rules and warn against packed RBC transfusion due to hyperviscosity/leukostasis.
- **`Q7` (`ALLTogether1` Stroke-Like Event + Sub-Protocol Deep Search)**: Retrieves Section 11.4.4 from the `ALLTogether1 v6.0` master protocol **and** escalates to `subprotocols/ALLTogether_Thrombosis _Guidelines_FINAL Version 1.0_05AUG19.pdf` (`LMWH enoxaparin`, `antithrombin III >80%` during PEGasparaginase).
- **`Q8` & `Q9` (`FaR-RMS` Dutch Patient Brochure)**: Generates a 13-year-old friendly Dutch explanation (`FaR-RMS_patientenfolder-3.pdf`).

### Run the UC1 ADK Agent on Golden Dataset Scenarios

```bash
# Query Q3 (FaR-RMS lung nodules) via swg-ctl
./bin/swg-ctl agents run \
  --agent uc1_oncoprotocol \
  --query "UZG patient with rhabdomyosarcoma (FaR-RMS) has 3 small lung nodules under 4mm on chest CT. Should we biopsy the lung lesions?"

# Query Q4 (150,000 WBC Hyperleukocytosis — test proactive clarification)
./bin/swg-ctl agents run \
  --agent uc1_oncoprotocol \
  --query "Patient presents with 150,000 white blood cells (hyperleukocytosis). What should I do?"

# Query Q7 (ALLTogether1 Stroke-Like Syndrome + Thrombosis sub-protocol deep search)
./bin/swg-ctl agents run \
  --agent uc1_oncoprotocol \
  --query "ALLTogether1 patient develops a stroke-like event and suspected sinus venous thrombosis after PEGasparaginase. What is the diagnostic and LMWH management?"
```

You should see output similar to:

```console
[uc1_oncoprotocol] Status: VERIFIED_CITATION
Citation: [FaR-RMS v2.0c (21-Mar-2024), Section 12.1 Pulmonary Metastases Assessment]
Action: DO NOT BIOPSY. In FaR-RMS v2.0c, <=4 pulmonary micronodules <5 mm are classified as indeterminate/negative for pulmonary metastasis. Lung biopsy is NOT indicated.
```

## 5. Negative Safety Testing & Out-of-Scope Refusals

Duration: 04:00

A clinical decision support assistant is judged as much by what it **refuses** to answer as by what it retrieves. Golden Dataset questions **`Q5`** (65-year-old adult with myocardial infarction and hypertension) and **`Q6`** (child with asthma exacerbation) must be deterministically rejected as out-of-scope.

### Execute the Out-of-Scope & PII Redaction Battery

```bash
cat << 'EOF' > test_uc1_negative.py
from agents.uc1_oncoprotocol.agent import answer_golden_protocol_question

for q in [
    "What is the first-line antihypertensive medication for a 65-year-old?",
    "What is the best maintenance inhaler step-up for a child with asthma?",
]:
    res = answer_golden_protocol_question(q, hospital_code="UZG")
    print(f"Query: {q}")
    print(f"  -> Matched ID: {res['matched_golden_id']} | Out of Scope: {res['out_of_scope']}")
    print(f"  -> Action: {res['answer'][:95]}...\n")
EOF
python3 test_uc1_negative.py
rm -f test_uc1_quickstart.py test_uc1_negative.py
```

You should see output similar to:

```console
Query: What is the first-line antihypertensive medication for a 65-year-old?
  -> Matched ID: Q5 | Out of Scope: True
  -> Action: OUT OF SCOPE REFUSAL: This question concerns adult internal medicine / cardiology...

Query: What is the best maintenance inhaler step-up for a child with asthma?
  -> Matched ID: Q6 | Out of Scope: True
  -> Action: OUT OF SCOPE REFUSAL: This question concerns general pediatric pulmonology (asthma)...
```

> aside positive
> **Negative Gate Verified**: Both `Q5` and `Q6` trigger `OUT_OF_SCOPE_REFUSAL` without hallucinating non-oncology clinical advice.

## 6. Verification & Jury Showcase Checklist

Duration: 02:00

Run the automated UC1 test suite to confirm all protocol parser, hospital filter, and Golden Dataset assertions pass:

```bash
pytest use_cases/01_oncoprotocol/tests/ tests/test_swg_platform.py -k "uc1 or protocol" -v
```

You should see output similar to:

```console
============================== passed in 0.45s ==============================
```

### 3-Minute Wintercircus Jury Demo Tips
- **Show Tier-1 Hospital Filtering first**: Switch between `UZG` (`27` protocols) and `UZA` (`21` protocols) to prove your assistant respects local Belgian hospital trial activation.
- **Demonstrate Proactive Clarification (`Q2` / `Q4`)**: Show the assistant asking whether `150,000 WBC` is AML (`CHIP-AML22`) or ALL (`ALLTogether1`) before recommending cytoreduction.
- **Show Sub-Protocol Deep Search (`Q7`)**: Highlight automatic escalation from `ALLTogether1 v6.0` into `ALLTogether_Thrombosis_Guidelines_v1.0.pdf`.
