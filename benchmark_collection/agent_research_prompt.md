# 일본 임대 벤치마크 부족분 조사 프롬프트

## 배경

일본 임대 물건을 평가하는 서비스를 만들고 있으며, 평가 시에 "이 지역/간마도리/건물구조 조합의 기준 임대료(벤치마크)" 데이터가 필요합니다. CHINTAI 스크레이핑으로 1차 수집을 완료했으나, 전체 940개 조합 중 **505개(54%)가 데이터 부족(N<20)** 상태입니다.

- **도쿄 23구**: 약 218개 조합 누락
- **오사카 24구**: 약 287개 조합 누락

## 목표

누락된 각 조합에 대해 **"해당 지역 + 간마도리 + 건물구조의 평균/중앙 월세(엔)"** 를 웹 조사를 통해 수집하라.

## 누락 조합 목록

별첨 파일 `missing_tasks.csv`에 505건의 누락 조합이 기재되어 있다.

| 컬럼 | 설명 | 예시 |
|------|------|------|
| prefecture | 도도부현 (Tokyo / Osaka) | Tokyo |
| municipality | 시구정촌 (일본어) | 新宿区 |
| layout | 간마도리 | 1K |
| structure | 건물 구조 (영문 코드) | wood |

### 건물 구조 코드 ↔ 일본어 매핑

| 영문 코드 | 일본어 | 설명 |
|-----------|--------|------|
| wood | 木造 | 목조 |
| light_steel | 軽量鉄骨造 | 경량철골조 |
| steel | 鉄骨造 | 철골조 |
| rc | 鉄筋コンクリート造 (RC造) | 철근콘크리트 |
| src | 鉄骨鉄筋コンクリート造 (SRC造) | 철골철근콘크리트 |

### 누락 건수 (구조별)

| 구조 | 누락 건수 | 비고 |
|------|----------|------|
| light_steel | 167 | 가장 많이 누락, 소규모 구에 리스팅 자체가 적음 |
| src | 153 | 고급 구조, 1R/1K 비율 낮음 |
| wood | 103 | 도심 구에서 누락 많음 |
| steel | 67 | - |
| rc | 15 | 대부분 오사카 소규모 구 |

## 조사 방법 가이드

### 우선 소스 (조사 순서)

1. **SUUMO (スーモ)** — https://suumo.jp/chintai/
   - 지역별 + 간마도리별 + 건물구조별 검색 가능
   - 검색 결과 상단에 "○○件" 표시 → 리스팅 수가 20건 이상이면 가격대를 기록
   - URL 패턴: `https://suumo.jp/chintai/tokyo/sc_{ward_code}/`

2. **HOMES (ホームズ)** — https://www.homes.co.jp/chintai/
   - "家賃相場" 섹션에서 구별 상장 데이터 제공
   - https://www.homes.co.jp/chintai/price/ 에서 구별 평균 임대료 조회 가능

3. **at home (アットホーム)** — https://www.athome.co.jp/chintai/souba/
   - "家賃相場" 페이지에서 구별+간마도리별 상장 데이터 직접 제공
   - 건물구조별 필터 가능

4. **CHINTAI (チンタイ)** — https://www.chintai.net/
   - 우리가 1차로 사용한 소스. 리스팅 수가 20 미만이어서 누락된 조합임

5. **공적 통계** (최후 수단)
   - 총무성 통계국 주택・토지 통계조사: https://www.stat.go.jp/data/jyutaku/
   - 국토교통성 부동산정보: https://www.land.mlit.go.jp/

### 검색 키워드 예시

| 목적 | 검색어 (Google / Yahoo Japan) |
|------|------------------------------|
| 구별 상장 | `{구이름} {간마도리} {구조} 家賃相場` |
| 예시 | `新宿区 1K 木造 家賃相場` |
| 평균 | `{구이름} {間取り} 平均家賃` |

## 출력 형식

조사 결과를 **아래 CSV 형식으로 출력**하라. 파일명: `agent_research_results.csv`

```csv
prefecture,municipality,layout,building_structure,avg_rent_yen,median_rent_yen,sample_count,source_name,source_url,confidence,notes
Tokyo,新宿区,1K,wood,72000,70000,25,SUUMO,https://suumo.jp/...,high,리스팅 25건 기반 중앙값
```

### 컬럼 설명

| 컬럼 | 필수 | 설명 |
|------|------|------|
| prefecture | ✅ | Tokyo / Osaka |
| municipality | ✅ | 일본어 구 이름 (例: 新宿区) |
| layout | ✅ | 1R / 1K / 1DK / 1LDK |
| building_structure | ✅ | wood / light_steel / steel / rc / src |
| avg_rent_yen | ✅ | 평균 월세 (엔). 없으면 빈칸 |
| median_rent_yen | ⬜ | 중앙값 월세 (엔). 없으면 빈칸 |
| sample_count | ⬜ | 조사에 사용된 물건 수 |
| source_name | ✅ | SUUMO / HOMES / at_home / CHINTAI / 공적통계 등 |
| source_url | ⬜ | 참조 URL |
| confidence | ✅ | high (N≥20) / medium (5≤N<20) / low (N<5) / estimate |
| notes | ⬜ | 특이사항 (예: "この区にはSRC物件がほぼない") |

## 우선순위 기준

505건 전부 조사하기 어려우면 **아래 우선순위로 진행**하라.

### 우선순위 1: RC 누락 (15건) — 반드시 채우기
RC는 가장 보편적 구조. 누락 15건은 대부분 오사카 소규모 구이므로 SUUMO/HOMES로 빠르게 채울 수 있음.

### 우선순위 2: Steel 누락 (67건)
철골조도 비교적 보편적. 도쿄 38건 + 오사카 29건.

### 우선순위 3: Wood 누락 (103건)
목조는 교외/서민 구에서 많고. 도심 구에서 리스팅이 적을 수 있음.

### 우선순위 4: SRC 누락 (153건)
SRC는 타워맨션급 고급 구조. 1R/1K에 SRC가 매우 드물며, 데이터가 없으면 `confidence=estimate`로 "해당 구 RC 임대료 × 1.05~1.10" 추정치도 허용.

### 우선순위 5: Light Steel 누락 (167건)
경량철골은 데이터가 가장 적은 카테고리. 없으면 `confidence=estimate`로 "해당 구 wood 임대료 × 1.02~1.05" 추정치도 허용.

## 주의사항

1. **관리비(管理費/共益費)는 제외**하고 순수 월세(賃料)만 기록할 것
2. **상한가 아닌 실거래 수준**의 데이터여야 함 (家賃相場 = 시세)
3. 조사 불능 시 `confidence=estimate`로 추정값을 기입하되, `notes`에 추정 근거를 반드시 명시
4. 같은 조합에 여러 소스가 있으면 **리스팅 수가 더 많은 소스** 우선
5. URL은 조사 시점 URL을 가능한 한 정확하게 기록
6. 1건당 최대 3분 이내로 조사. 오래 걸리면 다음 조합으로 이동

## 완료 기준

- `agent_research_results.csv` 파일 출력 완료
- `confidence=high` 비율이 전체의 50% 이상이면 성공
- 조사 불능 조합은 `confidence=estimate` + 추정 근거 notes
- 최종 커버리지 목표: 전체 940 조합 중 **80% 이상** (현재 46% → 80%)
