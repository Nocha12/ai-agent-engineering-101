def test(run, kit):
    out = run({"limit": 5}, kit)
    assert out.get("ok") is True
    assert "posts" in out
