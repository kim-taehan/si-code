---
description: 사용자의 기능 요청을 받아 분석→이슈→개발→PR→리뷰 루프→회의록까지 전체 파이프라인을 자동 진행
argument-hint: "<기능 설명>"
allowed-tools: Bash(gh *), Bash(git *), Read, Edit, Write, Glob, Grep, Agent
---

# /feature 자동 개발 파이프라인

요청: $ARGUMENTS

## 너(메인 에이전트)의 역할: 오케스트레이터

너는 분석가/개발자/리뷰어/서기 4명의 서브에이전트를 순서대로 호출하면서, 그 사이의 결정론적 작업(git, gh CLI)을 직접 처리한다. 서브에이전트들은 다른 서브에이전트를 호출할 수 없으므로 **모든 흐름 제어는 네가 한다.**

## 사전 점검 (실행 전 한 번)

다음을 병렬로 확인:
- `git status` — working tree clean이 아니면 사용자에게 알리고 중단
- `git branch --show-current` — main이 아니면 사용자에게 알리고 중단
- `gh auth status` — 인증 안 된 상태면 사용자에게 알리고 중단
- `gh repo view --json nameWithOwner` — 원격 저장소 확인

## 파이프라인 단계

### Step 1 — 분석가 호출
`Agent` 도구로 `subagent_type: "analyst"` 호출. 프롬프트는 사용자 요청 원문 + "GitHub 이슈 본문 마크다운으로 출력하라". 결과 마크다운 전체를 변수 `analysis`에 보관.

분석 결과 첫 줄(`# 제목`)에서 제목 추출 → 변수 `title`.

### Step 2 — GitHub 이슈 등록
```bash
gh issue create --title "<title>" --body "<analysis>"
```
출력 URL에서 `/issues/(\d+)` 정규식으로 이슈 번호 추출 → `issue_num`. URL은 `issue_url`.

### Step 3 — feature 브랜치 생성
```bash
git checkout main
git pull --ff-only origin main || true   # 실패해도 진행
git checkout -b agent/issue-<issue_num>
```
변수 `branch = "agent/issue-<issue_num>"`.

### Step 4 — 개발자 호출 (초기 구현)
`Agent` 도구로 `subagent_type: "developer"` 호출. 프롬프트:
> 다음 GitHub 이슈를 구현하세요. 현재 작업 디렉토리는 이미 `<branch>` 브랜치에 체크아웃되어 있습니다.
>
> ## 이슈 본문
>
> <analysis>

개발자가 마지막에 출력한 "변경 요약" 마크다운을 변수 `dev_summary_1`에 보관.

### Step 5 — 변경사항 검증 + 커밋 + 푸시 + PR
```bash
git status --porcelain
```
출력이 비어있으면 → 개발자가 아무것도 안 한 것. 이슈에 코멘트 달고 사용자에게 보고 후 중단:
```bash
gh issue comment <issue_num> --body "개발자 에이전트가 변경을 만들지 못했습니다. 사람의 개입 필요."
```

비어있지 않으면:
```bash
git add -A
git commit -m "feat: <title> (#<issue_num>)

<dev_summary_1>"
git push -u origin <branch>
gh pr create --title "<title> (#<issue_num>)" --body "Closes #<issue_num>

## 변경 요약 (개발자 에이전트 작성)

<dev_summary_1>" --head <branch>
```
출력 URL을 `pr_url`에 보관.

### Step 6 — 리뷰 + 수정 루프 (최대 3회)

`rounds = []` 빈 리스트 (라운드별 기록).
`current_dev_summary = dev_summary_1`.
`final_status = "manual_review_needed"`.

`for round_num in 1..3:`

  6.1. diff 추출:
  ```bash
  git diff origin/main...
  ```

  6.2. `Agent` 도구로 `subagent_type: "reviewer"` 호출. 프롬프트:
  > 다음 PR을 검토하세요.
  >
  > ## 원본 이슈
  >
  > <analysis>
  >
  > ## 변경 내용 (git diff)
  >
  > ```diff
  > <위 git diff 출력>
  > ```

  결과를 `review_text`에 보관. 첫 줄에서 마커 확인:
  - `[APPROVED]` 시작 → `approved = true`
  - `[CHANGES_REQUESTED]` 시작 → `approved = false`

  6.3. `rounds`에 `{dev_summary, review_text, approved}` 추가.

  6.4. PR에 리뷰 코멘트 게시:
  ```bash
  gh pr comment <pr_url> --body "## 자동 리뷰 (라운드 <round_num>)

  <review_text>"
  ```

  6.5. 분기:
  - `approved == true` → `final_status = "approved"`. 루프 탈출.
  - `round_num == 3` → 사람 개입 필요 코멘트 추가, 루프 탈출:
    ```bash
    gh pr comment <pr_url> --body "자동 리뷰 라운드 3회 모두 [CHANGES_REQUESTED]. 사람의 개입이 필요합니다."
    ```
  - 그 외 → 개발자 재호출 (수정 모드):

    `Agent` 도구로 `subagent_type: "developer"` 호출. 프롬프트:
    > 이전에 작성한 코드에 대해 리뷰어가 수정을 요청했습니다.
    >
    > ## 리뷰 피드백
    >
    > <review_text>
    >
    > ## 원본 이슈
    >
    > <analysis>
    >
    > 리뷰의 Critical 항목과 SOLID/테스트 관련 지적사항을 모두 반영하세요. 현재 작업 디렉토리는 이미 `<branch>` 브랜치에 체크아웃되어 있습니다.

    결과 마지막 메시지를 `current_dev_summary`에 갱신.

    추가 변경사항 검증:
    ```bash
    git status --porcelain
    ```
    비어있으면 → 개발자가 피드백 반영 못 한 것. PR 코멘트 달고 루프 탈출:
    ```bash
    gh pr comment <pr_url> --body "개발자 에이전트가 리뷰 피드백을 반영하지 못했습니다. 사람의 개입 필요."
    ```

    비어있지 않으면 커밋 + 푸시:
    ```bash
    git add -A
    git commit -m "fix: 리뷰 라운드 <round_num> 피드백 반영

    <current_dev_summary>"
    git push
    ```

### Step 7 — 서기 호출
`Agent` 도구로 `subagent_type: "secretary"` 호출. 프롬프트는 다음 정보를 모두 포함:
- 사용자 원본 요청 (`$ARGUMENTS`)
- `analysis` (분석가 결과)
- `title`, `issue_num`, `issue_url`, `pr_url`, `branch`
- `final_status`
- `rounds` 리스트 (각 라운드의 dev_summary + review_text + approved)
- 오늘 날짜 (`date +%Y-%m-%d`)

서기는 `docs/runs/<날짜>/issue-<N>.md` 회의록 + `docs/daily/<날짜>.md` 일지를 만든다.

### Step 8 — docs/ 커밋 + 푸시 + PR에 회의록 댓글
```bash
git status --porcelain docs/
```
변경 있으면:
```bash
git add docs/
git commit -m "docs: 서기 회의록·일지 (#<issue_num>)"
git push
```

회의록 본문을 PR에 댓글로:
```bash
gh pr comment <pr_url> --body "## 서기 회의록

$(cat docs/runs/<날짜>/issue-<issue_num>.md)"
```

### Step 9 — 사용자에게 최종 보고
한 단락으로 무엇을 만들었고 어떤 상태(승인/수동검토)로 끝났는지, PR URL과 회의록 경로 명시.

## 주의사항
- 모든 git/gh 명령은 너(메인)가 직접 실행. 서브에이전트는 `gh` / `git push` / `git commit` 사용 금지.
- 서브에이전트 출력에서 변수 추출 시 마크다운/텍스트 형식을 신뢰. 추가 파싱 단계는 최소화.
- 어느 단계에서든 비정상 종료 사유가 있으면 사용자에게 명시적으로 보고하고 중단.
- 파괴적 명령(`git push --force`, `git reset --hard origin/*`, `rm -rf /`) 절대 금지.
