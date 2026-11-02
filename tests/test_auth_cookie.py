from auth.iaaa import _cookie_is_valid


class _Resp:
    def __init__(self, status, text=""):
        self.status_code = status
        self.text = text


class _Client:
    def __init__(self, rest, page=None):
        self.rest = rest
        self.page = page

    def get(self, url, **kwargs):
        return self.rest if "users/me" in url else self.page


def test_cookie_valid_on_200():
    assert _cookie_is_valid(_Client(_Resp(200))) is True


def test_cookie_invalid_on_401():
    assert _cookie_is_valid(_Client(_Resp(401))) is False


def test_cookie_invalid_on_login_page_fallback():
    assert _cookie_is_valid(_Client(_Resp(404), _Resp(200, "用户名 登录"))) is False


def test_cookie_valid_on_homework_page_fallback():
    assert _cookie_is_valid(_Client(_Resp(404), _Resp(200, "作业列表"))) is True
