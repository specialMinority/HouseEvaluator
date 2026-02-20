# S1 QA Notes (Agent B)

Generated at: 2026-02-17

## Checklist

- Required field count is 10~12:
  - `mvp_required_fields` count = 12 (OK)

- All keys/enums match `V0_Vocabulary.yml`:
  - Keys used are only from `V0_Vocabulary.yml -> keys -> input` (OK)
  - Enum tokens used match `V0_Vocabulary.yml -> enums` exactly (OK)

- Units do not conflict with V0:
  - Fields with V0-specified units use the same unit: `station_walk_min=min`, `area_sqm=sqm`, `building_built_year=year`, cost fields are `yen`, contract months fields are `months` (OK)
  - Unit-less fields use `unit: none` per `K0_OutputContracts/S1_InputSchema.schema.json` (OK)

- Dependency 표현:
  - When `hub_station == "other"`, `hub_station_other_name` is shown via `depends_on` (OK)

