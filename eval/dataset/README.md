# Synthetic evaluation set

**Everything here is synthetic.** The companies, people, VAT IDs, IBANs, purchase orders and invoices are generated. See "Identifiers" below for why none of them can collide with real ones.

## Regenerate
```bash
python -m rechnungsfluss.synthetic.generate --out eval/dataset   # seed 20261019
```
- `files/`, `manifest.jsonl` and `purchase_orders.json` are all **committed**. The committed documents are the canonical eval inputs, and the LLM cassettes are keyed by their exact bytes.
- CI regenerates the set and fails if the labels or POs differ (`test_committed_manifest_matches_the_generator`). It also checks every committed file against its label hash (`test_committed_files_match_their_labels`).
- **Scans** are image-only and are rasterized with FreeType, blur and JPEG. That isn't guaranteed byte-identical across operating systems, so their hashes are excluded from the regeneration comparison and checked against the committed files instead.

## Contents
There are 132 documents across 2 tenants:

| Category | Docs | What is wrong | Expected outcome |
|---|---|---|---|
| `clean` | 44 | nothing | KoSIT accept → auto-approve |
| `noisy` | 16 | 10–25 lines, umlauts, `&`, `<`, `"`, decimal quantities | KoSIT accept → auto-approve |
| `wrong_totals` | 18 | grand total, line sum, or VAT amount off | KoSIT reject (BR-CO-15 / BR-CO-10+13 / BR-CO-17+S-09) → review |
| `missing_fields` | 18 | see below | → review |
| `po_mismatch` | 18 | invoiced price 5–15 % above the PO, or quantity above the PO | KoSIT accept → review (our 2-way match rule) |
| `duplicate` | 18 | exact re-send; same invoice in another format; fuzzy (new number, date +1–3 days) | exact: dropped by idempotency; others → review |

The `missing_fields` variants are:

| Variant | Official result | Route |
|---|---|---|
| No seller VAT ID | BR-CO-26, BR-S-02 (+ BR-DE-16 under XRechnung) | review |
| No due date | **Accepted.** BR-CO-25 is only *monitored* in the pinned KoSIT configuration (see its CHANGELOG) | review (our rule `missing_due_date`) |
| No PO reference | Accepted | review (our rule `no_po_reference`) |
| No buyer reference | BR-DE-15. XRechnung only, so `nordwerk-de` CII/UBL only | review |
| ZUGFeRD MINIMUM profile | Not an e-invoice (⚠️ legal basis unverified, see `docs/standards.md`) | review |

**Formats:**
- `nordwerk-de`: XRechnung 3.0 CII and UBL, ZUGFeRD (EN 16931 profile), plain PDF.
- `nordwerk-at`: EN 16931 CII and UBL, ZUGFeRD, plain PDF. XRechnung is German.

## Plain-PDF layouts (input for LLM extraction)
| Layout | What it is |
|---|---|
| `classic` | Vector PDF with a text layer, seller top-left, header data below the buyer |
| `compact` | Vector PDF with a different arrangement: seller top-right, header data in a right-hand box, totals left-aligned |
| `scan` | The classic arrangement drawn as a 150-dpi grayscale image, rotated up to ±1.2°, blurred, with seeded grain and JPEG compression. **No text layer.** |

- Noisy invoices are always scans. Other plain PDFs are split 40/30/30 by a seeded random choice.
- Every PDF prints every reviewable field of the truth (addresses with country, e-mail addresses, the seller contact, and the PO-line column for BT-132), so extraction is scored only on what is on the page.
- All three layouts come from one layout description, so they can't show different data.

## Purchase orders and BT-132
- Each PO in `purchase_orders.json` lists its lines, plus the supplier VAT ID and name.
- Invoice lines reference PO lines through **BT-132**:
  - always on noisy invoices,
  - on 70 % of other invoices.
- The remaining invoices exercise the description fallback of 2-way matching (ADR-0014).

## Label schema (`manifest.jsonl`, one JSON object per line)

| Field | Meaning |
|---|---|
| `doc_id`, `tenant`, `file`, `format` | identity; `format` ∈ `cii`, `ubl`, `zugferd`, `pdf` |
| `category`, `variant` | see the tables above |
| `sha256` | SHA-256 of the file. For ZUGFeRD it is the hash of the *embedded XML*, because factur-x stamps PDF metadata with the generation time. |
| `specification_id` | BT-24 inside the document (`null` for plain PDFs) |
| `expected_validation` | `accept`, `reject` or `not_an_e_invoice` |
| `expected_rule_ids` | **exact** set of KoSIT error rule IDs |
| `expected_route` | `auto_approve`, `review` or `skip_duplicate_delivery` |
| `review_reasons` | our reason codes (`validation_failed`, `no_po_reference`, `po_price_mismatch`, `po_quantity_mismatch`, `duplicate_suspected`, `missing_due_date`, `not_an_e_invoice`) |
| `duplicate_of` | `doc_id` of the original, for duplicates |
| `purchase_order` | BT-13 in the document |
| `truth` | the canonical invoice the document encodes (`Invoice.model_validate(truth)`); `null` for the MINIMUM profile |

**How labels are verified:** `tests/integration/test_dataset_labels.py` runs every document through the real KoSIT validator. It requires the *same verdict and the same set of rule IDs* as the label. For plain PDFs, it validates what a perfect extraction would produce: the truth serialized as EN 16931 CII.

The two-way-match and routing labels (`expected_route`, `review_reasons`) describe the behaviour the W3 pipeline must implement. They can't be verified until that pipeline exists.

## Identifiers
- **VAT IDs** carry a deliberately wrong check digit, so they fail the official checksum and can't be issued IDs. A unit test checks this for 200 seeds. The EN 16931 and XRechnung rules don't check VAT ID checksums.
- **IBANs** have valid ISO 13616 checksums, which XRechnung BR-DE-19 checks, and an all-zero bank code.
- **Domains** use the reserved `.example` TLD (RFC 2606).

## Known limits
- The data is cleaner and more uniform than real supplier invoices: 13 catalogue items, fixed layouts, and one VAT category (`S`) per line.
- Plain PDFs come from one renderer with three layouts. Real supplier PDFs vary far more: logos, tables without ruling, multi-column text, handwriting, stamps and photos of paper.
- No credit notes (type 381), allowances, charges or prepayments.
