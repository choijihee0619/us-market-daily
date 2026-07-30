#!/usr/bin/env bash
# 최초 1회 세팅. 프로젝트 루트에서 실행:  bash scripts/bootstrap.sh
set -euo pipefail
cd "$(dirname "$0")/.."

echo "==> 1. Python 버전"
python3 -V

echo
echo "==> 2. 가상환경"
if [ ! -d .venv ]; then
  python3 -m venv .venv
  echo "  .venv 생성"
else
  echo "  .venv 이미 있음"
fi
# shellcheck disable=SC1091
source .venv/bin/activate

echo
echo "==> 3. 의존성 설치"
python -m pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt
echo "  완료"

echo
echo "==> 4. .env 확인"
if [ ! -f .env ]; then
  cp .env.example .env
  echo "  .env 생성 — 키를 채워야 한다"
else
  python - <<'PY'
for line in open(".env"):
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    k, _, v = line.partition("=")
    print(f"  {k.strip():<32} {'설정됨' if v.strip() else '비어있음'}")
PY
fi

echo
echo "==> 5. 한글 폰트 확인"
python - <<'PY'
import matplotlib.font_manager as fm
import sys
sys.path.insert(0, "src")
CAND = ["AppleGothic", "Apple SD Gothic Neo", "Malgun Gothic", "NanumGothic",
        "NanumBarunGothic", "Noto Sans CJK KR", "Noto Sans KR", "Noto Sans CJK JP"]
have = {f.name for f in fm.fontManager.ttflist}
hit = [c for c in CAND if c in have]
print(f"  사용 가능: {hit[0] if hit else '없음 -> 차트 라벨이 영문으로 나온다'}")
PY

echo
echo "==> 6. 오프라인 테스트"
for t in test_pipeline test_publish test_weekly_analytics test_url_registry test_site_audit \
         test_collect_parsing; do
  if python "tests/$t.py" > "/tmp/$t.log" 2>&1; then
    echo "  $t  통과"
  else
    echo "  $t  실패 — /tmp/$t.log 확인"
  fi
done

echo
echo "==> 7. git"
if git rev-parse --is-inside-work-tree > /dev/null 2>&1; then
  echo "  이미 git 저장소"
else
  git init -q
  git add .
  git commit -q -m "init: 미국 시장 일간 기록 파이프라인"
  echo "  init + 첫 커밋 완료"
  echo
  echo "  다음: GitHub에 레포 만들고"
  echo "    git remote add origin https://github.com/<USER>/us-market-daily.git"
  echo "    git branch -M main && git push -u origin main"
  echo "  그리고 config.yaml 의 repo_url 을 교체할 것"
fi

echo
echo "==> 완료. 다음 단계:"
echo "  source .venv/bin/activate"
echo "  python scripts/run_daily.py --backfill 500   # 10~20분"
echo "  python scripts/run_daily.py"
