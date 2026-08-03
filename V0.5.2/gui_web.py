"""
(호환용 진입점) Material Design 3 웹 UI(web_ui/)를 pywebview로 띄운다.

정식 진입점은 main.py 하나이며, 이 파일은 예전 실행 습관(python gui_web.py)을 위해 남겨둔
얇은 래퍼다. 예전에는 여기에 Api 클래스 합성 목록(mixin 나열)이 main.py와 똑같이 복사돼
있었는데, 새 mixin을 추가할 때 한쪽만 고치면 그 진입점으로 실행한 사용자에게는 해당 탭의
API가 전부 없는 것으로 보였다(브리지 호출이 전부 null 반환) — '보고서' 탭 API를 추가할 때
실제로 그 일이 발생했다. 그래서 이제 Api를 다시 정의하지 않고 main.py의 것을 재사용한다.

실행: python gui_web.py  (권장: python main.py)
"""
from main import Api, main

__all__ = ["Api", "main"]

if __name__ == "__main__":
    main()
