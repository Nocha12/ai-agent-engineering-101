import os, json

def test(run, kit):
    p = "logs/lost_deals.jsonl"
    try:
        os.remove(p)
    except FileNotFoundError:
        pass
    out = run({"customer":"test-customer", "item":"계란", "offered_price":9675, "grade_stock":"2개: C 1개(예약), B 1개", "wanted":"2개와 유효한 결제서", "last_message":"두 개 구매를 다시 확인하겠습니다", "reason":"기타(결제서 예약)"}, kit)
    assert out.get("ok") is True
    with open(p, encoding="utf-8") as f:
        row = json.loads(f.readline())
    assert row["customer"] == "test-customer" and row["offered_price"] == 9675
    assert "예약" in row["reason"] and row["item"] == "계란"
    os.remove(p)
