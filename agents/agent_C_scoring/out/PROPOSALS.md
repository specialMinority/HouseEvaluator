# PROPOSALS (Agent C / Scoring Spec)

## 1) location 벤치마크 키 확장
- 상태: DONE (2026-02-17)
- 내용: `area_access_score_0_100`(0~100)을 V0 canonical derived key로 추가하고, Agent C S2 location feature에서 참조하도록 반영.
- 메모: 프로세스상 R0는 Frozen이므로, 실제 운영 적용 전 오케스트레이터 승인 절차가 필요할 수 있음.

## 2) tradeoff 메시지 키/태그의 표준화
- 제안: tradeoff에 사용할 `tradeoff_tag` / `message_key` 후보군을 V0(또는 별도 vocabulary 섹션)에 **enum 형태로 고정**.
- 이유: 현재 S2의 `tradeoff_rules[].outputs`는 자유 형식이라, 생성기/리포트 템플릿(C1)과의 결합 시 오타/불일치 리스크가 큼.

## 3) benchmark_confidence 관련 canonical 리스크 플래그
- 제안: `benchmark_confidence in [none, low]`일 때 안내용 canonical risk flag(예: BENCHMARK_LOW_CONFIDENCE)를 V0에 추가.
- 이유: 스코어 산정은 중립 처리로 방어 가능하지만, 사용자에게 “비교 근거 약함”을 **명시적으로 노출**할 장치가 현재 canonical risk_flags에는 없음.

## 4) foreigner_adjustment 적용 범위 명시
- 제안: S2에서 `foreign_im_shift_months`/`foreign_reikin_allowance_months`가 **(a) 점수 산정에만 적용**인지, **(b) risk flags에도 적용**인지, **(c) what-if 기본 시나리오에도 반영**인지 문서/계약에 명확히 규정.
- 이유: “일반 벤치마크 view”와 “foreigner-adjusted view”를 병렬로 보여줄 때 일관성이 중요함.
