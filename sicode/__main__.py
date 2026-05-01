"""``python -m sicode`` 실행 진입점.

``sicode.main:main`` 을 호출해 종료 코드를 그대로 ``SystemExit`` 으로 전달한다.
모듈을 단순 import 했을 때는 부수효과가 없어야 하므로 가드를 둔다.
"""

from __future__ import annotations

from sicode.main import main

if __name__ == "__main__":  # pragma: no cover - 실행 진입점
    raise SystemExit(main())
