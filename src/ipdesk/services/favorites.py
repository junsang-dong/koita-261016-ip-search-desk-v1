from ipdesk.domain.models import Favorite, PatentRecord, normalize_application_number
from ipdesk.repo.repositories import Repository
from ipdesk.services.clock import now_kst


class FavoriteService:
    def __init__(self, repo: Repository):
        self.repo = repo

    def list(self) -> list[tuple[Favorite, PatentRecord]]:
        return self.repo.list_favorites()

    def get(self, application_number: str) -> Favorite | None:
        return self.repo.get_favorite(normalize_application_number(application_number)[0])

    def save(self, application_number: str, note: str, run_id: int | None = None) -> bool:
        """즐겨찾기 추가 또는 메모 수정. 먼저 검색·상세로 patents에 저장된 문헌이어야 한다."""
        number = normalize_application_number(application_number)[0]
        if self.repo.get_patent(number) is None:
            return False
        self.repo.upsert_favorite(number, note.strip(), now_kst(), run_id)
        return True

    def remove(self, application_number: str) -> bool:
        return self.repo.delete_favorite(normalize_application_number(application_number)[0])
