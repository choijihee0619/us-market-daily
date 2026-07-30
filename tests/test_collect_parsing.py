"""수집기 파싱 회귀 테스트 (외부망 없음).

첫 실제 백필(2026-07-30)에서 조용히 실패한 두 지점을 고정한다. 둘 다 데이터는
정상적으로 내려왔는데 **파싱 단계에서 죽어** 빈 결과가 되고, 로그 경고 한 줄만
남기고 파이프라인은 계속 돌아갔다. 이런 실패가 가장 위험하다 -- 리포트가
나오기 때문에 문제가 있다는 걸 모른다.

  1. Ken French 모멘텀 일간 파일은 모든 데이터 줄이 쉼표로 끝난다.
     필드가 하나 더 잡혀 컬럼명 할당에서 Length mismatch가 났다.
     -> UMD가 통째로 결측이 되고, risk_model=ff5_umd 의 모멘텀 통제가 사라진다.
  2. pd.read_html(url) 은 내부적으로 urllib을 쓴다. macOS framework Python은
     시스템 인증서를 참조하지 않아 CERTIFICATE_VERIFY_FAILED가 났다.
     -> 구성종목이 0개가 되고 유니버스가 ETF 25개로 쪼그라든다.
"""
from __future__ import annotations

import io
import sys
import zipfile
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.collect import factors as F  # noqa: E402
from src.collect import prices as P  # noqa: E402


def _french_zip(body: str, filename: str = "F-F_test_daily.csv") -> bytes:
    """실제 French 파일 구조를 축약해 만든 zip (머리말 + 데이터 + 저작권 꼬리말)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(filename, body)
    return buf.getvalue()


HEAD = ("This file was created by using the 202605 CRSP database.  It,,\n"
        "contains a momentum factor, constructed from six value-weight portfolios,\n"
        "\n")
TAIL = ",,\nCopyright 2026 Eugene F. Fama and Kenneth R. French,,\n"

# 줄 끝 쉼표가 있는 모멘텀 형식 (실측 형태)
MOM_BODY = HEAD + "19261103,0.35,\n19261104,-0.61,\n20260529,-1.68,\n" + TAIL
# 줄 끝 쉼표가 없는 FF5 형식
FF5_BODY = (HEAD + "19630701,-0.67,0.02,-0.35,0.03,0.13,0.012\n"
                   "19630702,0.79,-0.28,0.28,-0.08,-0.21,0.012\n"
                   "20260529,-1.20,0.41,0.15,-0.05,0.22,0.017\n" + TAIL)


class _FakeResp:
    def __init__(self, content: bytes):
        self.content = content

    def raise_for_status(self):
        pass


def test_french_trailing_comma(monkeypatch_requests):
    """줄 끝 쉼표가 있는 파일과 없는 파일을 같은 코드로 읽어야 한다."""
    monkeypatch_requests(_french_zip(MOM_BODY))
    mom = F._read_french_zip("http://x/mom.zip")
    assert list(mom.columns) == ["date", 1], f"컬럼 수 불일치: {list(mom.columns)}"
    assert len(mom) == 3, f"행수 불일치: {len(mom)}"
    # French는 % 단위로 주므로 100으로 나눈 값이어야 한다
    assert abs(float(mom[1].iloc[0]) - 0.0035) < 1e-9, mom[1].iloc[0]
    mom.columns = ["date", "umd"]        # 실패했던 바로 그 할당
    print(f"  모멘텀 형식(꼬리 쉼표) {len(mom)}행, 컬럼명 할당 성공")

    monkeypatch_requests(_french_zip(FF5_BODY))
    ff = F._read_french_zip("http://x/ff5.zip")
    assert len(ff.columns) == 7, f"FF5 컬럼 수 불일치: {len(ff.columns)}"
    ff.columns = ["date", "mkt_rf", "smb", "hml", "rmw", "cma", "rf"]
    assert abs(float(ff["mkt_rf"].iloc[0]) + 0.0067) < 1e-9
    print(f"  FF5 형식(꼬리 쉼표 없음) {len(ff)}행, 컬럼 7개 유지")


def test_french_merge_keeps_umd(monkeypatch_requests):
    """fetch_french_daily이 UMD를 결측 없이 붙이는지."""
    calls = {"n": 0}

    def fake_get(url, **kw):
        calls["n"] += 1
        return _FakeResp(_french_zip(FF5_BODY if "5_Factors" in url else MOM_BODY))

    F.requests.get = fake_get                      # type: ignore[attr-defined]
    ff = F.fetch_french_daily()
    assert calls["n"] == 2, "FF5와 모멘텀 두 파일을 받아야 한다"
    assert not ff.empty and "umd" in ff.columns
    # 공통 날짜(20260529)에는 UMD가 반드시 있어야 한다
    row = ff[pd.to_datetime(ff["date"]) == pd.Timestamp("2026-05-29")]
    assert len(row) == 1 and pd.notna(row["umd"].iloc[0]), "UMD 결측 -- 모멘텀 통제가 사라진다"
    assert abs(float(row["umd"].iloc[0]) + 0.0168) < 1e-9
    print(f"  FF5+UMD 병합 {len(ff)}행, 공통일 UMD={row['umd'].iloc[0]:.4f}")


WIKI_HTML = """
<html><body>
<table class="wikitable"><tr><th>Symbol</th><th>Security</th>
<th>GICS Sector</th><th>GICS Sub-Industry</th></tr>
<tr><td>MMM</td><td>3M</td><td>Industrials</td><td>Industrial Conglomerates</td></tr>
<tr><td>BRK.B</td><td>Berkshire Hathaway</td><td>Financials</td><td>Multi-Sector</td></tr>
</table></body></html>
"""


def test_constituents_uses_requests(monkeypatch_requests):
    """requests로 받아 문자열을 파싱해야 한다 (urllib 경유 시 macOS에서 SSL 실패).

    read_html에 URL을 직접 넘기는 구현으로 되돌아가면 이 테스트가 잡는다.
    fake는 requests만 대체하므로, urllib을 쓰면 실제 네트워크로 나가 실패한다.
    """
    class _TextResp:
        text = WIKI_HTML

        def raise_for_status(self):
            pass

    seen = {}

    def fake_get(url, **kw):
        seen["url"] = url
        seen["ua"] = (kw.get("headers") or {}).get("User-Agent")
        return _TextResp()

    import requests as _rq
    orig = _rq.get
    _rq.get = fake_get
    try:
        df = P.sp500_constituents()
    finally:
        _rq.get = orig

    assert "wikipedia.org" in seen.get("url", ""), "requests로 위키피디아를 받지 않았다"
    assert seen.get("ua"), "User-Agent 미지정 -- 위키피디아가 차단할 수 있다"
    assert len(df) == 2, f"행수 불일치: {len(df)}"
    assert list(df.columns) == ["ticker", "name", "sector", "industry", "snapshot_date"]
    # BRK.B -> BRK-B (yfinance 표기)
    assert "BRK-B" in set(df["ticker"]), f"티커 변환 실패: {list(df['ticker'])}"
    # 생존편향 대응용 스냅샷 일자가 반드시 박혀야 한다
    assert pd.notna(df["snapshot_date"].iloc[0])
    print(f"  구성종목 {len(df)}건, BRK.B -> BRK-B 변환, snapshot_date 기록")


def _monkeypatch_requests_factory():
    """F.requests.get 을 고정 응답으로 바꿔주는 도우미 (pytest 없이 동작)."""
    orig = F.requests.get

    def setter(content: bytes):
        F.requests.get = lambda url, **kw: _FakeResp(content)  # type: ignore[assignment]

    def restore():
        F.requests.get = orig                                  # type: ignore[assignment]

    return setter, restore


if __name__ == "__main__":
    setter, restore = _monkeypatch_requests_factory()
    try:
        print("\n[1] Ken French 줄 끝 쉼표 (모멘텀 파싱 실패 회귀)")
        test_french_trailing_comma(setter)
        print("\n[2] FF5 + UMD 병합")
        test_french_merge_keeps_umd(setter)
        print("\n[3] 구성종목 수집 경로 (macOS SSL 회귀)")
        test_constituents_uses_requests(setter)
    finally:
        restore()
    print("\n전체 통과")
