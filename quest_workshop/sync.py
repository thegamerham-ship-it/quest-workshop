"""영구 저장: 승인 퀘스트·기획 지침·버린 요청을 GitHub 저장소의 별도 브랜치(quest-data)에도 저장한다.

왜 필요한가
  인터넷 버전(Streamlit Cloud)은 서버가 재시작하면 서버 안의 파일이 사라진다.
  그래서 파일을 쓸 때마다 GitHub에도 올려 두고, 서버가 켜질 때 GitHub에서 다시 받아 온다.
왜 main이 아니라 별도 브랜치인가
  Streamlit Cloud는 main이 바뀌면 새 코드로 재시작한다. 승인할 때마다 재시작되면 화면의 카드가 날아간다.

켜는 법(환경 변수 또는 Streamlit Secrets)
  GITHUB_TOKEN        Contents 읽기·쓰기 권한이 있는 토큰. 없으면 이 기능은 꺼지고 로컬 파일만 쓴다.
  QUEST_GITHUB_REPO   기본 thegamerham-ship-it/quest-workshop
  QUEST_GITHUB_BRANCH 기본 quest-data
브랜치 안에서는 quests/ 폴더에 승인 폴더(save_dir)의 .json 파일을 그대로 둔다.

PC에서 받아 보기:  python -m quest_workshop.sync pull   (GITHUB_TOKEN이 있어야 함)
"""
from __future__ import annotations

import base64
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

REMOTE_DIR = "quests"


class GitHubSync:
    def __init__(self, token: str, repo: str = "thegamerham-ship-it/quest-workshop", branch: str = "quest-data"):
        self.token, self.repo, self.branch = token, repo, branch
        self._branch_ready = False

    # 테스트에서 이 함수만 바꿔 끼운다
    def _api(self, method: str, path: str, body: dict | None = None) -> dict | list | None:
        req = urllib.request.Request(
            f"https://api.github.com/repos/{self.repo}{path}", method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Authorization": f"Bearer {self.token}", "Accept": "application/vnd.github+json",
                     "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "quest-workshop"})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.loads(r.read() or b"null")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            raise

    def ensure_branch(self) -> None:
        if self._branch_ready:
            return
        if self._api("GET", f"/git/ref/heads/{self.branch}") is None:
            main = self._api("GET", "/git/ref/heads/main")
            self._api("POST", "/git/refs", {"ref": f"refs/heads/{self.branch}", "sha": main["object"]["sha"]})
        self._branch_ready = True

    def push(self, local: Path, message: str = "") -> None:
        self.ensure_branch()
        remote = f"{REMOTE_DIR}/{local.name}"
        cur = self._api("GET", f"/contents/{remote}?ref={self.branch}")
        body = {"message": message or f"작업실 저장: {local.name}", "branch": self.branch,
                "content": base64.b64encode(local.read_bytes()).decode()}
        if cur:
            body["sha"] = cur["sha"]
        self._api("PUT", f"/contents/{remote}", body)

    def pull(self, save_dir: Path) -> int:
        """브랜치의 quests/ 파일을 save_dir로 받아 온다. 받은 파일 수를 돌려준다."""
        listing = self._api("GET", f"/contents/{REMOTE_DIR}?ref={self.branch}")
        if not listing:
            return 0
        save_dir.mkdir(parents=True, exist_ok=True)
        n = 0
        for item in listing:
            if item.get("type") != "file" or not item["name"].endswith(".json"):
                continue
            f = self._api("GET", f"/contents/{REMOTE_DIR}/{item['name']}?ref={self.branch}")
            (save_dir / item["name"]).write_bytes(base64.b64decode(f["content"]))
            n += 1
        return n


_sync: GitHubSync | None = None
_configured = False


def configure(sync: GitHubSync | None = None) -> GitHubSync | None:
    """환경 변수로 켠다. 테스트는 sync를 직접 넘긴다."""
    global _sync, _configured
    if sync is not None:
        _sync = sync
    else:
        token = os.getenv("GITHUB_TOKEN")
        _sync = GitHubSync(token, os.getenv("QUEST_GITHUB_REPO", "thegamerham-ship-it/quest-workshop"),
                           os.getenv("QUEST_GITHUB_BRANCH", "quest-data")) if token else None
    _configured = True
    return _sync


def enabled() -> bool:
    if not _configured:
        configure()
    return _sync is not None


def persist(path: str | Path) -> str:
    """파일을 쓴 직후 부른다. GitHub에도 올리고 결과를 한 줄로 돌려준다(꺼져 있으면 빈 문자열)."""
    if not enabled():
        return ""
    try:
        _sync.push(Path(path))
        return "GitHub에도 저장"
    except Exception as e:  # 저장 실패가 승인 자체를 막지 않게
        return f"GitHub 저장 실패: {e}"


def pull_all(save_dir: str | Path) -> str:
    """서버가 켜질 때 부른다. GitHub에 저장된 것을 로컬 폴더로 받아 온다."""
    if not enabled():
        return ""
    try:
        return f"GitHub에서 {_sync.pull(Path(save_dir))}개 파일을 불러옴"
    except Exception as e:
        return f"GitHub에서 불러오기 실패: {e}"


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "pull":
        target = sys.argv[2] if len(sys.argv) > 2 else os.getenv("QUEST_SAVE_DIR", "output/quests")
        print(pull_all(target) or "GITHUB_TOKEN이 없어 불러오지 않았어요.")
    else:
        print("사용법: python -m quest_workshop.sync pull [폴더]")
