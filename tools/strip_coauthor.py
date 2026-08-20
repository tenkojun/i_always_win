# -*- coding: utf-8 -*-
"""
커밋 메시지에서 Co-Authored-By 트레일러를 지운다.

git filter-branch 의 --msg-filter 로 쓴다. 메시지를 stdin 으로 받아
stdout 으로 돌려준다.

    FILTER_BRANCH_SQUELCH_WARNING=1 git filter-branch -f \
      --msg-filter "python tools/strip_coauthor.py" \
      --tag-name-filter cat -- --all

    git push --force origin main
    git push --force origin --tags

주의 — 히스토리 재작성이다
--------------------------
전 커밋의 해시가 바뀐다. 태그 34개도 같이 옮겨진다(--tag-name-filter).
GitHub 릴리스는 **태그 이름**에 붙어 있어서 태그를 옮겨도 릴리스와
첨부 zip 은 그대로 남는다. 태그를 지웠다 다시 만들면 릴리스가 초안으로
떨어지니, 지우지 말고 force 로 **옮겨야** 한다.

되돌리려면 재작성 전에 만들어 둔 backup-pre-rewrite 브랜치를 쓴다.
"""
import re
import sys

s = sys.stdin.buffer.read().decode("utf-8", "replace")
s = re.sub(r"\n*^Co-Authored-By: Claude[^\n]*\n?", "\n", s, flags=re.M)
sys.stdout.buffer.write((s.rstrip() + "\n").encode("utf-8"))
