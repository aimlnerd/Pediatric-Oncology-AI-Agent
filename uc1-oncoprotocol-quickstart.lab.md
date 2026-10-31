---
id: swg-bspho26-uc1-oncoprotocol-quickstart
summary: Build a zero-hallucination Belgian pediatric hematology-oncology protocol assistant with 2-tier hospital filtering (UZA, UZL, CHU, UCL, HUDERF, UZG), sub-protocol deep search, and Golden Dataset Q1-Q9 verification.
authors: Jerome CG Paquay (@jpaquay),Dr. Ruben van Paemel (UZ Gent),Marina Bosso Bosso (@mbossobosso)
keywords: category:Cloud,product:VertexAI,product:ADK,docType:Codelab
layout: paginated
---

# UC1 Quickstart: Virtual Assistant in Pediatric Hematology & Oncology (OncoProtocol-Assist)

> aside negative
> **Not authoritative.** Requirements come from the BSPHO deck (`SwG 2026_UC1_Pediatric Hematology & Oncology.pptx`); participation and expected answers come from `SwG-UC1-Golden Dataset and mapping table.xlsx`. Commands, function names and console outputs below are illustrative, not an API contract. Terminology follows [GLOSSARY.md](GLOSSARY.md): **Tier 1 = Center Scope**, **Tier 2 = Protocol Selection**; **Deep Search** (sub-protocols) is a separate feature and is **deferred**.

## 1. Introduction & Clinical Challenge

Duration: 02:00

Welcome to **Use Case 1 (OncoProtocol-Assist)** at **Solve with Google: BSPHO 2026 (Wintercircus Ghent)**.

Pediatric hematology and oncology treatment protocols (`ALLTogether1 v6.0`, `SIOP-HR-NBL2`, `CHIP-AML22`, `FaR-RMS v2.0c`, `BEACON2`, `SIOP Umbrella`) are dense, 150-to-300-page PDFs packed with multi-arm stratification flowcharts, age/weight dose modification tables, and cross-referenced sub-protocols. During an acute clinical shift, an oncologist cannot afford a hallucinated dose threshold or an answer drawn from a trial in which their hospital does not participate.

In this quickstart codelab, you will build and validate a **2-Tier Sovereign Clinical Protocol Assistant** in `europe-west1` that:

1. **Center Scope (Tier 1)**: Restricts retrieval to the protocols the clinician's center (`UZA`, `UZL`, `CHU`, `UCL`, `HUDERF`, `UZG`) participates in, across **27 master protocols**.
2. **Protocol Selection (Tier 2)**: The clinician checks/unchecks protocols from their Center Scope in the left sidebar to scope each question.
3. *(Deferred)* **Deep Search**: Offering to extend search into the 5 `ALLTogether1` sub-protocols (`InO`, `A2G Glucarpidase`, `Maintenance`, `Pancreatitis`, `Thrombosis`). Not in the current build.
4. **Asks Proactive Clarifying Questions**: When a clinician asks an under-specified question (e.g., *"A patient has 150,000 WBC, what should I do?"* or *"A 3-year-old with metastatic neuroblastoma—which arm?"*), the assistant asks for the missing clinical discriminator (AML vs. ALL; MYCN amplification status) before citing protocol logic.
5. **Enforces Verbatim Citations & Out-of-Scope Refusals**: Every clinical answer includes `[Protocol_ID, Version, Page, Section]` citations and strictly refuses adult or non-oncology queries.

```mermaid
flowchart LR
    Doc["Clinician Query + Center (Tier 1) + Protocol Selection (Tier 2)"] --> HSV["Scope Guard (NISS redaction + out-of-scope refusal)"]
    HSV --> Clarify{"Missing Clinical Discriminator? (AML vs ALL / MYCN / Age)"}
    Clarify -- "Yes" --> Ask["Ask Targeted Clarifying Question"]
    Clarify -- "No" --> RAG["Retriever: Vertex AI Search filtered to Protocol Selection"]
    RAG --> Cite["Answerer: grounded response with [Protocol, Version, Page, Section]"]
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

## 3. Tier-1 Center Scope

Duration: 05:00

Not every Belgian hospital participates in every SIOP or ITCC trial. Per the mapping table, **UZ Gent (`UZG`)** participates in all **27** protocols in the dataset, whereas **UZ Antwerpen (`UZA`)** participates in **21** — `UZA` does not run `BEACON2`, `CHIP-AML22`, `EWOG-SAA 2010`, `FaR-RMS`, `HRNBL2` or `ML DS 2018`.

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
UZG Active Protocols: 27
UZA Active Protocols: 21 | Excluded at UZA: ['BEACON2', 'CHIP-AML22', 'EWOG-SAA 2010', 'FaR-RMS', 'HRNBL2', 'ML DS 2018']
```

## 4. Multimodal Protocol Grounding & Golden Dataset Q1–Q9

Duration: 07:00

The UC1 evaluation tests your assistant against the **Golden Dataset** in `SwG-UC1-Golden Dataset and mapping table.xlsx` (`Q1`–`Q8`) plus `Q9` (the reference patient folder PDF). The spreadsheet is authoritative; summaries:

- **`Q1` (Must, `BEACON2` / `HR-NBL2`)**: Haematological and non-haematological conditions before starting **dinutuximab beta**.
- **`Q2` (Must, `HR-NBL2`)**: Staging / extra examinations for a 3-year-old with poorly differentiated neuroblastoma **without MYCN amplification** (SIOPEN-modified INRG; high-risk only if Stage M).
- **`Q3` (Must, `FaR-RMS`)**: Staging investigations required when rhabdomyosarcoma is suspected.
- **`Q4` (Must, `CHIP-AML22`)**: Acute management of a patient with **150,000 WBC** (hyperleukocytosis). The reference answer is CHIP-AML22-based; the AML vs ALL discriminator is a candidate Clarifying Question.
- **`Q5` / `Q6` (Must, negative)**: Adult antihypertensive question and pediatric asthma step-up — both must be refused as out of scope.
- **`Q7` (Should, `ALLTogether1`)**: Stroke-like event under ALLTogether1 — possible causes and treatment per the master protocol. (Sub-protocol escalation belongs to the deferred Deep Search feature.)
- **`Q8` / `Q9` (Could, `FaR-RMS`)**: Dutch patient information folder about FaR-RMS treatment and timepoints (`Golden Dataset_Question9_FaR-RMS_patientenfolder-3.pdf`).

### Run the UC1 ADK Agent on Golden Dataset Scenarios

```bash
# Query Q3 (FaR-RMS staging investigations) via swg-ctl
./bin/swg-ctl agents run \
  --agent uc1_oncoprotocol \
  --query "What staging investigations are required by the FAR-RMS protocol if a patient is suspected of having rhabdomyosarcoma?"

# Query Q4 (150,000 WBC Hyperleukocytosis — test proactive clarification)
./bin/swg-ctl agents run \
  --agent uc1_oncoprotocol \
  --query "Patient presents with 150,000 white blood cells (hyperleukocytosis). What should I do?"

# Query Q7 (ALLTogether1 stroke-like event — master protocol only)
./bin/swg-ctl agents run \
  --agent uc1_oncoprotocol \
  --query "I have a patient treated according to the ALLtogether protocol who developed a stroke-like event, does the protocol recommend anything on the possible causes and treatment?"
```

Compare each answer against the "Suggested shortened response" column of the Golden Dataset; every claim must carry a `[Protocol, Version, Page, Section]` citation.

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
- **Show Center Scope first**: Switch between `UZG` (`27` protocols) and `UZA` (`21` protocols) to prove your assistant respects local Belgian trial participation.
- **Show Protocol Selection**: Uncheck a protocol and show the assistant answering "not covered in your selected protocols" instead of drawing on it.
- **Demonstrate Proactive Clarification (`Q2` / `Q4`)**: Show the assistant asking for the missing Clinical Discriminator before answering.
