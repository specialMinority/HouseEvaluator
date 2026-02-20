# Proposals (Agent B)

## 1) Deliver QA notes as a separate artifact (no K0 change)
- Background: `K0_OutputContracts/S1_InputSchema.schema.json` sets top-level `"additionalProperties": false` and does not define `qa_notes`, so embedding `qa_notes` in `S1_InputSchema.json` cannot pass schema validation.
- Proposal: Withdraw the requirement to embed QA notes in `S1_InputSchema.json`, and instead publish QA notes as a separate deliverable in the same folder:
  - `agents/agent_B_inputschema/out/S1_QA_NOTES.md` (or `.json`)
- Benefit: Keeps R0/V0/K0 frozen while still providing the required checklist evidence.
