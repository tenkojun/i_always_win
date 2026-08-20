# 릴리스 발행 절차

앱의 자동 업데이트는 **GitHub Releases** 를 본다. 태그만 올려서는 안 되고
릴리스를 만들고 **윈도우 배포본 zip 을 자산으로 첨부**해야 한다.

---

## 한 줄로 하기

```bash
python tools/release.py --build
```

이 도구가 순서대로 한다.

1. `CHANGELOG.md` 에 현재 버전 절이 있는지 확인 — 없으면 **중단**
2. 커밋되지 않은 변경이 있으면 **중단**
3. 빌드 (`--build` 일 때)
4. `dist/Plutus/.data` **삭제** — 키·계정 DB가 공개 자산에 섞이지 않게
5. zip 포장
6. **업데이터 검증 로직에 통과시켜 본다** — 올린 뒤에 형식 문제를
   발견하면 사용자가 받아 가는 중에 고쳐야 한다
7. 태그 푸시 → `gh release create ... --latest`

발행 전에 확인만:

```bash
python tools/release.py --build --dry-run
```

이미 있는 태그면 중복 발행을 막는다.

---

## 손으로 할 때

## 1. 버전 올리기

`version.py` 의 `__version__` 하나만 고친다. 나머지(창 제목·설정 화면·
EXE 속성·업데이트 비교)는 전부 여기서 파생된다.

```python
__version__ = "3.1.0"
```

`CHANGELOG.md` 맨 위에 항목을 추가한다.

## 2. 빌드

```bash
python tools/make_version_info.py     # 윈도우 버전 리소스 갱신
pyinstaller app.spec --noconfirm      # → dist/Plutus/
```

## 3. 배포본 압축 — `.data` 를 절대 넣지 말 것

`dist/Plutus/` 를 한 번이라도 실행했다면 그 안에 `.data/` 가 생겨 있다.
**여기엔 당신의 API 키와 계정 DB가 들어 있다.**

```powershell
Remove-Item -Recurse -Force dist\Plutus\.data -ErrorAction SilentlyContinue
Compress-Archive -Path dist\Plutus -DestinationPath Plutus-win-x64.zip -Force
```

> 앱 쪽에도 방어가 두 겹 있다 — 압축 해제 단계에서 `.data` 를 지우고,
> 교체 스크립트도 `.data` 를 건너뛴다. 그래도 **애초에 넣지 않는 것**이
> 맞다. 자산이 공개되면 키가 그대로 노출된다.

압축 전에 한 번 확인:

```powershell
Get-ChildItem -Force dist\Plutus | Select-Object Name
```

`.data` 가 보이면 지우고 다시 압축한다.

## 4. 릴리스 만들기

```bash
git tag v3.1.0
git push origin v3.1.0

gh release create v3.1.0 Plutus-win-x64.zip \
  --title "v3.1.0" \
  --notes-file release-notes.md
```

또는 GitHub 웹에서 **Releases → Draft a new release** → 태그 선택 →
`Plutus-win-x64.zip` 첨부 → Publish.

---

## 자산 이름 규칙

업데이터는 자산 중에서 이렇게 고른다.

1. 이름에 `win` 또는 `plutus` 가 들어간 `.zip`
2. 없으면 아무 `.zip`
3. 그것도 없으면 "릴리스에 윈도우 배포본(zip)이 없습니다" 로 안내하고
   릴리스 페이지를 열어 준다

`Plutus-win-x64.zip` 을 권장한다.

## zip 구조

압축 안 어딘가에 `Plutus.exe` 가 있으면 된다. 한 겹 더 싸여 있어도
업데이터가 찾아낸다.

```
Plutus-win-x64.zip
└── Plutus/
    ├── Plutus.exe
    └── _internal/
```

---

## 사용자 쪽에서 벌어지는 일

1. 로그인 6초 뒤 조용히 확인 (설정 → 시스템에서 수동 확인도 가능)
2. 새 버전이 있으면 **"새 버전이 있습니다"** 모달
   — 버전 대비 · 릴리스 노트 · 유지되는 항목 안내
3. `업데이트` → 내려받기 → 검증 → 압축 해제 (여기까지는 교체 없음)
4. `지금 재시작` → 확인 창 → 앱 종료 → 교체 스크립트 동작 → 자동 재기동

교체 대상은 `Plutus.exe` 와 `_internal/` 뿐이다. 기존 파일은 `.old` 로
백업해 두고, 새 버전이 6초 안에 기동하지 못하면 **자동으로 되돌린다.**
로그는 `.data/update/apply.log`.

'나중에' 를 누르면 그 버전은 다시 묻지 않는다(localStorage 기억).
설정 화면의 `업데이트 확인` 은 그 기억을 무시하고 항상 보여 준다.

---

## 소스로 실행 중일 때

자동 교체를 하지 않는다. 로컬 수정본을 덮어쓸 수 있어서 판단이 필요하다.

```bash
git pull
```


---

## 과거 버전 소급 발행

```bash
python tools/backfill_releases.py --dry-run   # 확인
python tools/backfill_releases.py             # 발행
```

`version.py` 가 바뀐 커밋을 전부 찾아 태그를 만들고 CHANGELOG 절을
노트로 붙인다. 이미 있는 릴리스는 건너뛴다.

**바이너리는 붙이지 않는다.** 과거 EXE 를 만들려면 커밋마다 체크아웃해
다시 빌드해야 하는데 26개면 두 시간이 넘고, 자동 업데이트는 최신
릴리스만 본다. GitHub 이 자동으로 붙여 주는 소스 아카이브로 충분하다.
특정 버전이 꼭 필요하면 그때 골라 빌드해 올리면 된다.

모든 소급 릴리스는 `--latest=false` 로 만든다.
