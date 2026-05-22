import pytest
import requests


@pytest.fixture(scope="session")
def base_url():
    return "http://dog.ob.shuyilink.com"

@pytest.fixture(scope="session")
def geer_base_url():
    return "http://t-geerguangxue-3x.ob.shuyilink.com"

@pytest.fixture(scope="session")
def boze_base_url():
    return "http://t-boze.ob.shuyilink.com"

@pytest.fixture(scope="session")
def bird_base_url():
    return "http://bird.ob.shuyilink.com"

@pytest.fixture(scope="session")
def session():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    yield s
    s.close()


@pytest.fixture
def credentials():
    return {"username": "demo", "password": "mes123456"}

@pytest.fixture(scope="session")
def bird_credentials():
    return {"username": "admin", "password": "sygl123456"}


@pytest.fixture(scope="session")
def login_session(session, base_url):
    """登录并返回已认证的 session（session 级别，所有测试复用）"""
    resp = session.post(
        f"{base_url}/auth/auth-login",
        json={"username": "demo", "password": "mes123456"},
        allow_redirects=False,
    )
    assert resp.status_code == 200, f"登录失败，状态码: {resp.status_code}"
    assert resp.json().get("code") == '0', f"登录失败: {resp.json()}"
    return session


@pytest.fixture(scope="session")
def bird_login_session(session, bird_base_url, bird_credentials):
    """bird 登录并返回已认证的 session（session 级别，所有测试复用）"""
    resp = session.post(
        f"{bird_base_url}/auth/auth-login",
        json=bird_credentials,
        allow_redirects=False,
    )
    assert resp.status_code == 200, f"bird 登录失败，状态码: {resp.status_code}"
    assert resp.json().get("code") == '0', f"bird 登录失败: {resp.json()}"
    return session


# ========== UI 自动化 fixture ==========

@pytest.fixture(scope="session")
def ui_base_url():
    return "http://banana.ob.shuyilink.com"


@pytest.fixture(scope="session")
def ui_cookies(ui_base_url):
    """通过 API 预登录获取 Cookie，跳过 Keycloak 流程"""
    s = requests.Session()
    s.post(
        f"{ui_base_url}/auth/auth-login",
        json={"username": "demo", "password": "mes123456"},
        allow_redirects=False,
    )
    return [{"name": c.name, "value": c.value, "domain": c.domain, "path": c.path}
            for c in s.cookies]


@pytest.fixture
def ui_credentials():
    return {"username": "demo", "password": "mes123456"}


@pytest.fixture(scope="session")
def browser_type_launch_args():
    return {"channel": "chrome"}
