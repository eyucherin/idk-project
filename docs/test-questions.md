# 핵심 사용자 질문 5개

### Q1. WT-01이 올해 생산한 전력량을 알려주세요
**활용 기술:** Python tool (`calculate_turbine_energy`)

---

###  Q2. O&M 관리자가 따라야 할 안전 관리 지침을 알려주세요
**활용 기술:** watsonx.data Milvus 기반 Knowledge Base RAG 문서에서 관련 구절을 검색하여 원문 인용 및 출처(문서명, 페이지, chunk_id)와 함께 반환

---

###  Q3. 풍력발전과 관련하여 어떤 사고들이 발생했나요? 주요 사고 유형을 알려주세요
**활용 기술:** watsonx.data Milvus 기반 Knowledge Base RAG `해외_풍력발전기_사고_예방_안전_점검_기술.pdf` 등 등록된 문서에서 안전 점검 절차 관련 구절을 검색하여 인용 형태로 반환.

---

###  Q4. WT-03의 상태와 관련 안전 절차 알려주세요
**활용 기술:** Python tool + RAG 복합 사용

1. `get_turbine_status` → 현재 센서값 및 CRITICAL/WARNING 분류
2. `get_recommendations` → 이상 센서 목록 및 권고 조치(`suggested_action`) 반환
3. documentation_specialist → 이상 센서 유형을 기반으로 RAG 검색 후 관련 안전 절차 인용

---

###  Q5. WT-03를 즉시 정지해 주세요
**활용 기술:** Python tool 연동 action 실행

1. action_specialist가 `get_turbine_status`로 현재 상태 확인 후 사용자에게 표시
2. 정지 효과(RPM → 0, 전력 출력 → 0, 수익 손실 위험) 안내 및 확인 요청
3. 사용자 확인 후 `shutdown_turbine` 호출 → 터빈 상태 즉시 업데이트

---






