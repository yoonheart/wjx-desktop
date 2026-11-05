"""响应判定。

所有「这段文本算成功还是失败」的判断只在这一个类里，其它层不得再做字符串匹配。
顺序很重要：先判失败特征，再判成功特征——失败文案里常混着成功字样。
"""

from __future__ import annotations

from wjxdesk.domain.enums import FailureKind, Verdict
from wjxdesk.protocol.contracts import RawResponse, SubmissionOutcome
from wjxdesk.protocol.manifest import Manifest


class WjxClassifier:
    # 服务端返回纯数字代码时的含义（线上观测）
    CODE_SUCCESS = {"10"}
    CODE_NEED_VERIFY = {"22"}

    def __init__(self, manifest: Manifest) -> None:
        self._spec = manifest.verdict

    def classify(self, response: RawResponse) -> SubmissionOutcome:
        # 407 = 代理要求账号密码。这是配置问题，重试再多次也不会好，
        # 必须单独归类，让上层立刻停批并提示用户改认证方式。
        if response.status_code == 407 or "407" in str(response.error or ""):
            return SubmissionOutcome(
                Verdict.UNKNOWN,
                FailureKind.PROXY_AUTH,
                "代理要求认证（407）：请在代理后台切换白名单模式，或让提取链接返回账号密码",
            )
        if response.error:
            return SubmissionOutcome(Verdict.UNKNOWN, FailureKind.NETWORK, response.error)
        if response.status_code == 0:
            return SubmissionOutcome(Verdict.UNKNOWN, FailureKind.NETWORK, "无响应")
        if response.status_code >= 500:
            return SubmissionOutcome(
                Verdict.UNKNOWN, FailureKind.NETWORK, f"服务端 {response.status_code}"
            )
        if response.status_code in (401, 403):
            return SubmissionOutcome(
                Verdict.NEED_VERIFY, FailureKind.NEED_VERIFY, f"状态码 {response.status_code}"
            )

        text = (response.text or "").strip()
        code = _leading_code(text)

        if code in self.CODE_NEED_VERIFY or self._hit(text, self._spec.need_verify):
            return SubmissionOutcome(Verdict.NEED_VERIFY, FailureKind.NEED_VERIFY, _snippet(text))
        if code in self.CODE_SUCCESS:
            return SubmissionOutcome(Verdict.SUCCESS)
        if self._hit(text, self._spec.limit):
            return SubmissionOutcome(Verdict.LIMIT, FailureKind.LIMIT, _snippet(text))
        if self._hit(text, self._spec.closed):
            return SubmissionOutcome(Verdict.CLOSED, FailureKind.CLOSED, _snippet(text))
        if self._hit(text, self._spec.success):
            return SubmissionOutcome(Verdict.SUCCESS)
        if len(text) <= 2:
            return SubmissionOutcome(Verdict.UNKNOWN, FailureKind.UNKNOWN, _snippet(text) or "响应为空")
        return SubmissionOutcome(Verdict.UNKNOWN, FailureKind.UNKNOWN, _snippet(text))

    @staticmethod
    def _hit(text: str, markers: tuple[str, ...]) -> bool:
        return any(marker and marker in text for marker in markers)


def _leading_code(text: str) -> str:
    """取出响应开头的数字代码，如「22，请完成验证」→「22」。"""
    digits = ""
    for char in text:
        if char.isdigit():
            digits += char
        else:
            break
    return digits


def _snippet(text: str, limit: int = 120) -> str:
    flat = " ".join((text or "").split())
    return flat[:limit]
