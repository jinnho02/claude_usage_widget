# Claude Usage Widget

claude.ai 구독(Pro/Max)의 사용 한도를 Windows 바탕화면에 아이콘 칸 크기로 띄우는 위젯입니다.

- 현재 세션(5시간)과 주간 한도를 1분마다 갱신
- 바탕화면 아이콘 격자에 맞춰 2×2, 2×3, 3×2 크기로 고정
- 클릭하면 Claude 앱(또는 claude.ai) 실행, 드래그로 이동
- 다른 창 뒤에 깔리며, Windows 시작 시 자동 실행
- Claude 앱 오른쪽 위에 계기판 아이콘 표시, 마우스를 올리면 사용량 패널이 펼쳐짐

## 요구 사항

- Windows 10 / 11
- Python 3.11 이상 (설치 시 "Add python.exe to PATH" 체크)

## 설치

1. 이 저장소를 내려받습니다 (Code → Download ZIP 후 압축 해제, 또는 `git clone`).
2. `install.bat`을 더블클릭합니다.
3. 처음 실행하면 세션 키를 묻습니다. claude.ai에 로그인한 브라우저에서
   F12 → Application → Cookies → `https://claude.ai` → `sessionKey` 값을 붙여 넣습니다.

세션 키는 Windows 자격 증명 관리자에만 저장되며 저장소에는 포함되지 않습니다.

## 사용법

- 클릭: Claude 열기
- 드래그: 이동 (놓으면 가장 가까운 칸에 붙음)
- 오른쪽 클릭: 새로고침, 위젯/계기판 표시, 크기, 자동 실행, 세션 키 바꾸기, 종료
- Claude 앱의 계기판: 마우스를 올리면 사용량 표시, 클릭하면 새로고침, 드래그로 위치 조정

## 제거

1. 위젯을 오른쪽 클릭 → 종료
2. 오른쪽 클릭 메뉴에서 자동 실행을 미리 끄거나, 설정 → 앱 → 시작 프로그램에서 끔
3. 다음 항목 삭제
   - `%LOCALAPPDATA%\Programs\ClaudeUsage`
   - `%APPDATA%\ClaudeUsageWidget`
   - 시작 메뉴의 `Claude Usage` 바로 가기
   - 자격 증명 관리자 → Windows 자격 증명의 `ClaudeUsageWidget`

## 참고

claude.ai 사용량 페이지가 쓰는 비공식 엔드포인트를 호출하므로, 사이트 구조가 바뀌면 동작하지 않을 수 있습니다. 세션 키는 로그인 정보와 같으니 공유하지 마세요.
