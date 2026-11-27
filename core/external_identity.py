"""第三方平台核验身份后交给本站的最小数据契约。"""

from dataclasses import dataclass


class ExternalAuthError(ValueError):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code, self.status = code, status


@dataclass(frozen=True)
class ExternalIdentity:
    provider: str
    app_id: str
    subject: str
    nickname: str = ""
