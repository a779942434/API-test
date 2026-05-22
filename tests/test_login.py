import pytest


class TestLogin:
    """登录接口测试"""

    LOGIN_URL = "/auth/auth-login"

    def test_login_success(self, base_url, session, credentials):
        """正常登录：正确的用户名和密码应返回成功"""
        resp = session.post(
            f"{base_url}{self.LOGIN_URL}",
            json=credentials,
            allow_redirects=False,
        )
        assert resp.status_code == 200, f"期望200，实际{resp.status_code}"
        data = resp.json()
        assert data.get("code") == '0', f"登录失败: {data}"

    def test_login_wrong_password(self, base_url, session, credentials):
        """密码错误应返回失败"""
        credentials["password"] = "wrong_pwd"
        resp = session.post(
            f"{base_url}{self.LOGIN_URL}",
            json=credentials,
            allow_redirects=False,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("code") != 0, "错误密码不应登录成功"

    def test_login_empty_username(self, base_url, session):
        """空用户名应返回失败"""
        resp = session.post(
            f"{base_url}{self.LOGIN_URL}",
            json={"username": "", "password": "mes123456"},
            allow_redirects=False,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("code") != 0, "空用户名不应登录成功"

    def test_login_empty_password(self, base_url, session, credentials):
        """空密码应返回失败"""
        resp = session.post(
            f"{base_url}{self.LOGIN_URL}",
            json={"username": credentials["username"], "password": ""},
            allow_redirects=False,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("code") != 0, "空密码不应登录成功"
