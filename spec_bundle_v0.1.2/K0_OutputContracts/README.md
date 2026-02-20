# K0 Output Contracts (v0.1.0)
- Purpose: 에이전트 병렬 작업 산출물이 “조립 가능”하도록 강제하는 계약(JSON Schema)
- Rule: 모든 산출물은 아래 스키마를 통과해야 하며, 키/enum은 V0_Vocabulary.yml을 따른다.

## Files
- S1_InputSchema.schema.json : 입력 폼/검증 스펙 산출물 계약
- S2_ScoringSpec.schema.json : 점수/등급/플래그/what-if 룰 산출물 계약
- D1_BenchmarkSpec.schema.json : 벤치마크 데이터 스펙/폴백/품질 규칙 계약
- C1_ReportTemplates.schema.json : 트레이드오프 문장/협상/대안 템플릿 계약

## Notes
- `when` 조건식은 JSONLogic 호환 오브젝트를 사용한다.
- 템플릿 문자열은 `{placeholder}` 토큰을 사용한다. 허용 토큰은 V0의 report_placeholders를 따른다.
