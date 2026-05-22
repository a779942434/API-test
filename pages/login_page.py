from playwright.sync_api import Page


class LoginPage:
    """登录页面对象 — 支持 API Cookie 注入，跳过 Keycloak"""

    def __init__(self, page: Page, base_url: str):
        self.page = page
        self.base_url = base_url

    def login_via_cookies(self, target_path: str, cookies: list[dict]):
        """通过预注入的 Cookie 直接进入目标页面，跳过 Keycloak 登录"""
        self.page.context.add_cookies(cookies)
        self.page.goto(f"{self.base_url}{target_path}", wait_until="domcontentloaded")
        self.page.wait_for_selector('button:has-text("查询")', timeout=30000)
