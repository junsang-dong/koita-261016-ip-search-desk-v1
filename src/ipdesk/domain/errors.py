class SourceError(Exception):
    """데이터 소스(KIPRIS 등) 호출 실패. status는 search_runs.status에 그대로 기록된다."""

    status = "api_error"
    default_message = "데이터 소스 오류"

    def __init__(self, message: str | None = None, result_code: str | None = None):
        self.message = message or self.default_message
        self.result_code = result_code
        super().__init__(self.message)


class NetworkError(SourceError):
    status = "network_error"
    default_message = "KIPRIS에 연결하지 못했습니다"


class UpstreamError(SourceError):
    status = "upstream_error"
    default_message = "KIPRIS 서버 오류"


class ParseError(SourceError):
    status = "parse_error"
    default_message = "응답 형식을 해석하지 못했습니다"


class AuthError(SourceError):
    status = "auth_error"
    default_message = "API 키 또는 서비스 활용신청을 확인하세요"


class QuotaError(SourceError):
    status = "quota_error"
    default_message = "이번 달 호출 한도를 초과했습니다. 샘플 모드로 전환하세요"


class ApiError(SourceError):
    status = "api_error"
    default_message = "KIPRIS가 오류를 반환했습니다"
