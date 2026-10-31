# Glossary — BSPHO Protocol Assistant

## Knowledge base

**Protocol**
A master clinical trial protocol PDF (e.g. ALLTogether1, FaR-RMS, CHIP-AML22). The primary source the assistant answers from. Identified by a stable Protocol ID; exactly one Protocol Version is active at a time.
_Avoid_: document, guideline, trial (when meaning the PDF)

**Sub-protocol**
A supporting document that belongs to a parent Protocol (e.g. the ALLTogether Thrombosis guidelines). Not searched in the current scope.
_Avoid_: subprotocol guideline, supporting doc

**Protocol Version**
The amendment level of a Protocol as printed on the document (e.g. v6.0, 07-Mar-2025). An Amendment replaces the previous version; older versions are no longer searchable.

**Amendment**
An updated PDF for an existing Protocol, uploaded by an Admin. Supersedes the previous Protocol Version.

**Center**
One of the six Belgian pediatric hem/onc hospitals: UZA, UZL, CHU, UCL, HUDERF, UZG.
_Avoid_: site, institution

**Participation**
The fact that a Center runs a given Protocol, as recorded in the Mapping Table.

**Mapping Table**
The Center × Protocol participation matrix supplied by BSPHO. Source of truth for Participation.

## Conversation

**Center Scope** (Tier 1)
The set of Protocols a Center participates in. Determined by the clinician's Center; the clinician cannot widen it.
_Avoid_: hospital filter, Tier-1 filter

**Protocol Selection** (Tier 2)
The subset of the Center Scope the clinician has checked for the current question. Always a subset of the Center Scope.
_Avoid_: active protocols, checked sources

**Deep Search**
Extending a search from a Protocol into its Sub-protocols. Deferred — not part of the current scope, and not a "tier".

**Clinical Discriminator**
A patient fact the protocol answer depends on (e.g. AML vs ALL, MYCN amplification, age, risk group, treatment phase). When missing, the assistant asks a Clarifying Question.

**Clarifying Question**
A question the assistant asks the clinician to obtain a missing Clinical Discriminator before answering.

**Citation**
A reference attached to a claim, pointing to a passage the assistant actually retrieved: Protocol, Protocol Version, page and (where available) section.

**Out-of-Scope Refusal**
The assistant's explicit statement that a question is outside pediatric hematology/oncology protocols (e.g. adult cardiology, pediatric asthma).

**Not Covered**
The assistant's statement that the Protocol Selection holds no passage answering an in-scope question. Distinct from an Out-of-Scope Refusal.

## Evaluation

**Golden Dataset**
The BSPHO-authored set of reference questions and expected answers (Q1–Q8 in the spreadsheet, Q9 the Dutch FaR-RMS patient folder) used to judge the assistant.
