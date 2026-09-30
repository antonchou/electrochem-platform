"""业务错误。接口层按类型映射 HTTP 状态码；消息直接给实验者看，用中文写清楚原因。"""


class NotFound(LookupError):
    """404：记录不存在。"""


class Conflict(RuntimeError):
    """409：与当前状态冲突（如已有测量在进行）。"""


class Invalid(ValueError):
    """422：请求内容不满足业务条件（如标定用的测量判稳未通过）。"""


class Unavailable(RuntimeError):
    """503：依赖暂时不可用（设备未连接、数据库写不进去）。"""
