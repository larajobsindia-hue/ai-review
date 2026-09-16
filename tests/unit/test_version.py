import ai_review

def test_version():
    assert ai_review.__version__
    parts = ai_review.__version__.split(".")
    assert len(parts) >= 2
    assert all(p.isdigit() for p in parts[:2])