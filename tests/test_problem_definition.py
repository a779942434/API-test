"""问题定义 - UI 自动化测试"""

import time
import random

from pages.login_page import LoginPage
from pages.problem_definition_page import ProblemDefinitionPage


CATEGORY_NAME = f"UI自动化测试问题点分类-{int(time.time())}"
REPAIR_TIME = f"{random.uniform(1, 10):.2f}"
PROBLEM_NAME = f"UI自动化测试问题点-{int(time.time())}"


class TestProblemDefinition:
    """问题定义 UI 自动化测试"""

    def test_create_problem_definition(self, page, ui_base_url, ui_cookies):
        """新增问题定义：主表新增 → 展开子表 → 子表新增"""
        login_page = LoginPage(page, ui_base_url)
        problem_page = ProblemDefinitionPage(page, ui_base_url)

        # 通过 API Cookie 直接进入目标页面，跳过 Keycloak
        login_page.login_via_cookies(
            target_path="/master-data/tool-management/problem-definition/index",
            cookies=ui_cookies,
        )

        # 主表新增
        problem_page.click_add_button()
        problem_page.wait_for_form_dialog()
        problem_page.fill_form(
            category=CATEGORY_NAME,
            remark1="UI自动化测试备注1",
            remark2="UI自动化测试备注2",
        )
        problem_page.submit_form()
        problem_page.wait_for_dialog_close()

        # 展开第一行（新增记录在最上面）→ 子表新增
        problem_page.expand_first_row()

        # 子表新增
        problem_page.click_sub_table_add_button()
        problem_page.wait_for_form_dialog()
        problem_page.fill_sub_table_form(
            repair_time=REPAIR_TIME,
            problem_point=PROBLEM_NAME,
        )
        problem_page.submit_sub_table_form()
        problem_page.wait_for_dialog_close()
