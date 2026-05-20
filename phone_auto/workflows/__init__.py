"""카드사별 결제 워크플로 — 내일 사용자가 각 카드사 라벨 확정 후 모듈 추가.

사용 예 (구현 시):
    from phone_auto.workflows.hyundai import payment
    payment(wf, code7="1234567", pin6="123456")
"""
CARD_REGISTRY: dict = {}  # 카드사 모듈 추가될 때 등록 — 현재 비어있음
