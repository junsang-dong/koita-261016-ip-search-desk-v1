"""KIPRIS Plus resultCode → 오류 종류 매핑표.

⚠️ 명세 9.4가 직접 든 코드는 30(인증키 미등록), 31(기간 만료)뿐이다.
32·22는 공공데이터포털 공통 규약을 따른 **미검증 추정값**이므로, KIPRIS Plus 포털 문서의
resultCode 표를 확인해 이 파일만 고친다.
표에 없는 코드는 api_error로 처리하고 원본 코드를 숨기지 않는다.
"""

NORMAL = {"00", ""}  # 빈 값은 성공 응답에도 쓰일 수 있어 정상으로 본다 (미검증, 실제 응답으로 확인)

AUTH = {
    "30": "등록되지 않은 인증키",  # 명세 9.4
    "31": "인증키 사용 기간 만료",  # 명세 9.4
    "32": "허용되지 않은 IP",  # 미검증
}

QUOTA = {
    "22": "서비스 요청 제한 횟수 초과",  # 미검증
}


def classify(code: str) -> str:
    """'normal' | 'auth' | 'quota' | 'other'"""
    if code in NORMAL:
        return "normal"
    if code in AUTH:
        return "auth"
    if code in QUOTA:
        return "quota"
    return "other"
