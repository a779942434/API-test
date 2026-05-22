from playwright.sync_api import Page, Locator


def _try_selectors(page: Page, selectors: list[str], timeout: int = 8000) -> Locator:
    """依次尝试多个选择器，返回第一个可见元素"""
    last_error = None
    for sel in selectors:
        try:
            locator = page.locator(sel).first
            locator.wait_for(state="visible", timeout=timeout)
            return locator
        except Exception as e:
            last_error = e
            continue
    raise Exception(f"所有选择器均未匹配: {selectors}") from last_error


class ProblemDefinitionPage:
    """问题定义页面对象"""

    def __init__(self, page: Page, base_url: str):
        self.page = page

    def click_add_button(self):
        self.page.locator('button:has-text("新增")').first.click(force=True, timeout=8000)

    def wait_for_form_dialog(self):
        _try_selectors(self.page, ['.el-dialog'])

    def fill_field_by_label(self, label_text: str, value: str):
        field = _try_selectors(self.page, [
            f'//label[contains(text(), "{label_text}")]/following::input[1]',
            f'//label[contains(text(), "{label_text}")]/following::textarea[1]',
            f'//div[contains(@class, "el-form-item")][.//text()[contains(., "{label_text}")]]//input',
            f'input[placeholder*="{label_text}"]',
        ])
        field.fill(value)

    def fill_form(self, category: str, remark1: str, remark2: str):
        self.fill_field_by_label("问题分类", category)
        self.fill_field_by_label("备注1", remark1)
        self.fill_field_by_label("备注2", remark2)

    def submit_form(self):
        _try_selectors(self.page, [
            '.el-dialog__footer .el-button--primary',
            'button:has-text("确定")', 'button:has-text("保存")',
        ]).click(force=True)

    def wait_for_dialog_close(self, timeout=15000):
        self.page.wait_for_selector('.el-dialog', state='detached', timeout=timeout)

    # ========== 子表操作 ==========

    def expand_first_row(self):
        """点击第一行展开图标，选中该行后在页面下方子表中操作"""
        # 点击第一行的展开/选择图标
        expand_icon = self.page.locator('.el-table__expand-icon').first
        if expand_icon.count():
            expand_icon.click(force=True)
        else:
            # 点击第一行第一列
            self.page.locator('.el-table__body tr').first.locator('td').first.click(force=True)

        # 等待子表工具栏出现：下半部分面板有 border-t，里面有第二个「新增」按钮
        self.page.locator('.border-t').locator('button:has-text("新增")').wait_for(state="visible", timeout=5000)

    def click_sub_table_add_button(self):
        """点击子表的「新增」按钮（下半部分 border-t 面板中的按钮）"""
        self.page.locator('.border-t').locator('button:has-text("新增")').click(force=True)

    def fill_sub_table_form(self, repair_time: str, problem_point: str):
        # 子表弹窗中的两个输入框：理论维修时长、问题点
        dialog = _try_selectors(self.page, ['.el-dialog:visible', '.el-dialog'])
        # 只选可编辑的文本输入框，排除 radio、checkbox、hidden、readonly
        inputs = dialog.locator('input:not([readonly]):not([type="radio"]):not([type="checkbox"]):not([type="hidden"])').all()
        if len(inputs) >= 2:
            inputs[0].fill(repair_time)
            inputs[1].fill(problem_point)
        else:
            # 回退：用 label 定位
            self.fill_field_by_label("理论维修时长", repair_time)
            self.fill_field_by_label("问题点", problem_point)

    def submit_sub_table_form(self):
        self.submit_form()
