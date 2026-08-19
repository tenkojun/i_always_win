# 제3자 저작물 고지 (Third-Party Notices)

## eDEX-UI — GPL-3.0

이 프로젝트의 부팅 화면은 **eDEX-UI** 의 자산과 연출을 사용한다.

- 원저작물 : https://github.com/GitSquared/edex-ui
- 저작자   : GitSquared 및 기여자
- 라이선스 : GNU General Public License v3.0 (저장소 루트 `LICENSE`)
- 사운드   : **IceWolf** 작곡 (eDEX-UI v2.1.x 이상)

### 가져온 것

| 경로 | 내용 |
|---|---|
| `webapp/static/audio/*.wav` | 사운드 13종 (stdout · theme · granted · denied 등) |
| `webapp/static/audio/boot_log.txt` | 부팅 로그 원문 |
| 부팅 줄별 지연 로직 | `src/_renderer.js` 의 `displayLine()` 타이밍표를 이식 |

로그 원문에서 제품명(`eDEX-UI` → `I ALWAYS WIN`, `eDEX` → `IAW`)만
치환했다. 그 외 커널 메시지는 원문 그대로다.

### 이것이 뜻하는 것 — 읽고 넘어갈 것

**GPL-3.0 은 전염성 라이선스다.** 위 자산을 포함한 채로 이 프로그램을
배포하면, 배포되는 결합물 **전체**가 GPL-3.0 조건을 따른다. 구체적으로:

1. 배포 대상에게 **전체 소스코드를 제공**해야 한다 (EXE 만 배포 불가).
2. 파생물도 **같은 GPL-3.0** 으로 배포해야 한다.
3. 이 고지와 라이선스 전문을 함께 배포해야 한다.
4. 사용자에게 프로그램을 수정·재배포할 자유를 보장해야 한다.

즉 **독점(proprietary) 배포와 양립하지 않는다.**

### 원치 않는다면

eDEX-UI 자산 없이도 앱은 완전히 동작한다. 다음을 지우면 된다.

```bash
rm -rf webapp/static/audio
```

이 경우 부팅 사운드는 Web Audio 합성음으로, 부팅 로그는 이 프로젝트가
직접 쓴 70줄로 자동 폴백한다 (`_sfxProbe()` / `_loadBootLog()` 참조).
그 상태에서는 GPL 자산이 하나도 남지 않으므로 원하는 라이선스를
자유롭게 선택할 수 있다.

---

## 그 밖의 의존성

Python 패키지(numpy · pandas · scipy · scikit-learn · Flask · yfinance ·
pywebview · PyInstaller 등)는 각자의 라이선스(BSD · MIT · Apache-2.0)를
따르며, 해당 배포판에 포함된 라이선스 파일에 명시돼 있다.
