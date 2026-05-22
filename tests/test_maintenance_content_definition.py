"""模具修理内容定义 - API 全流程自动化测试"""

import time
import pytest


# ========== API 路径常量 ==========
MAIN_URL = "/linkim-pc/admin-console/tooling/maintenanceContentDefinition"
MAIN_PAGE_URL = "/linkim-pc/admin-console/tooling/maintenanceContentDefinition/page"
MAIN_DELETE_URL = "/linkim-pc/admin-console/tooling/maintenanceContentDefinition/batch-delete"
DETAIL_URL = "/linkim-pc/admin-console/tooling/maintenanceContentDefinitionDetail"
DETAIL_PAGE_URL = "/linkim-pc/admin-console/tooling/maintenanceContentDefinitionDetail/page"
DETAIL_DELETE_URL = "/linkim-pc/admin-console/tooling/maintenanceContentDefinitionDetail/batch-delete"

TS = int(time.time())


@pytest.fixture(scope="class")
def test_data(base_url, login_session):
    """类级别 fixture：统一管理测试数据生命周期 — 创建主表+子表，测试结束后清理"""
    s = login_session
    url = base_url

    # ---- 创建主表1 ----
    main_type = f"API测试-维修类型-{TS}"
    r = s.post(f"{url}{MAIN_URL}", json={
        "enableInd": 1,
        "maintenanceType": main_type,
        "extension": {"remark1": "备注1-API自动化", "remark2": "备注2-API自动化"},
    })
    assert r.json().get("code") == '0', f"fixture: 新增主表1失败 {r.json()}"

    # ---- 创建主表2（禁用状态）----
    disabled_type = f"API测试-维修类型-禁用-{TS}"
    r = s.post(f"{url}{MAIN_URL}", json={
        "enableInd": 0,
        "maintenanceType": disabled_type,
        "extension": {"remark1": "", "remark2": ""},
    })
    assert r.json().get("code") == '0', f"fixture: 新增主表2失败 {r.json()}"

    # ---- 回查所有主表ID ----
    r = s.post(f"{url}{MAIN_PAGE_URL}", json={
        "maintenanceTypeLike": f"API测试-维修类型-{TS}",
        "page": 1, "size": 30,
    })
    records = r.json().get("data", {}).get("records", [])
    id_map = {}
    for rec in records:
        mt = rec.get("maintenanceType", "")
        if disabled_type in mt:
            id_map["disabled_main_id"] = rec["id"]
        elif main_type in mt:
            id_map["main_id"] = rec["id"]

    main_id = id_map.get("main_id")
    disabled_main_id = id_map.get("disabled_main_id")
    assert main_id, f"fixture: 未找到主表ID, records={records}"

    # ---- 创建子表1 ----
    content_name = f"API测试-维修内容-{TS}"
    r = s.post(f"{url}{DETAIL_URL}", json={
        "enableInd": 1,
        "maintenanceContent": content_name,
        "maintenanceContentDefinitionId": main_id,
    })
    assert r.json().get("code") == '0', f"fixture: 新增子表1失败 {r.json()}"

    # ---- 创建子表2（禁用）----
    disabled_content = f"API测试-维修内容-禁用-{TS}"
    r = s.post(f"{url}{DETAIL_URL}", json={
        "enableInd": 0,
        "maintenanceContent": disabled_content,
        "maintenanceContentDefinitionId": main_id,
    })
    assert r.json().get("code") == '0', f"fixture: 新增子表2失败 {r.json()}"

    # ---- 回查子表ID ----
    r = s.post(f"{url}{DETAIL_PAGE_URL}", json={
        "maintenanceContentLike": f"API测试-维修内容-{TS}",
        "page": 1, "size": 30,
        "maintenanceContentDefinitionId": main_id,
    })
    detail_records = r.json().get("data", {}).get("records", [])
    detail_id_map = {}
    for rec in detail_records:
        mc = rec.get("maintenanceContent", "")
        if "禁用" in mc:
            detail_id_map["disabled_detail_id"] = rec["id"]
        else:
            detail_id_map["detail_id"] = rec["id"]

    detail_id = detail_id_map.get("detail_id")
    disabled_detail_id = detail_id_map.get("disabled_detail_id")

    # 打包返回
    data = {
        "main_type": main_type,
        "disabled_type": disabled_type,
        "main_id": main_id,
        "disabled_main_id": disabled_main_id,
        "content_name": content_name,
        "disabled_content": disabled_content,
        "detail_id": detail_id,
        "disabled_detail_id": disabled_detail_id,
    }
    yield data

    # ---- 清理：仅删禁用子表+禁用主表，保留主表1和其子表供手动验证 ----
    if disabled_detail_id:
        s.post(f"{url}{DETAIL_DELETE_URL}", json=[disabled_detail_id])
    if disabled_main_id:
        s.post(f"{url}{MAIN_DELETE_URL}", json=[disabled_main_id])
    # 以下两条保留不删，方便手动在系统里查看：
    #   main_id → 主表 "API测试-维修类型-{TS}"
    #   detail_id → 子表 "API测试-维修内容-{TS}"


class TestMaintenanceContentDefinition:
    """模具修理内容定义 主表 + 子表 API 全流程测试"""

    # ==================== 主表 - 查询 ====================

    def test_001_page_query_main_all(self, base_url, login_session):
        """主表/查询：分页查询全部，验证返回结构含 records/pages"""
        resp = login_session.post(
            f"{base_url}{MAIN_PAGE_URL}",
            json={"maintenanceTypeLike": "", "page": 1, "size": 30},
        )
        assert resp.status_code == 200
        result = resp.json()
        assert result.get("code") == '0', f"分页查询失败: {result}"
        data = result.get("data", {})
        assert "records" in data, "返回体应含 records"
        assert "pages" in data or "total" in data, "返回体应含 pages 或 total"

    def test_002_page_query_main_filter(self, base_url, login_session, test_data):
        """主表/查询：maintenanceTypeLike 模糊搜索，应匹配对应记录"""
        resp = login_session.post(
            f"{base_url}{MAIN_PAGE_URL}",
            json={"maintenanceTypeLike": test_data["main_type"], "page": 1, "size": 30},
        )
        assert resp.status_code == 200
        records = resp.json().get("data", {}).get("records", [])
        assert len(records) > 0, f"模糊搜索应匹配记录"
        for r in records:
            assert test_data["main_type"] in r.get("maintenanceType", "")

    def test_003_page_query_main_pagination(self, base_url, login_session):
        """主表/查询：分页 size=1，验证第1页和第2页数据不同"""
        page1 = login_session.post(
            f"{base_url}{MAIN_PAGE_URL}",
            json={"maintenanceTypeLike": "", "page": 1, "size": 1},
        )
        page2 = login_session.post(
            f"{base_url}{MAIN_PAGE_URL}",
            json={"maintenanceTypeLike": "", "page": 2, "size": 1},
        )
        r1 = page1.json().get("data", {}).get("records", [])
        r2 = page2.json().get("data", {}).get("records", [])
        if r1 and r2:
            assert r1[0].get("id") != r2[0].get("id"), "第1页和第2页数据应不同"

    # ==================== 主表 - 编辑 ====================

    def test_004_edit_main(self, base_url, login_session, test_data):
        """主表/正向：编辑已有主表，修改维修类型名称和备注"""
        main_id = test_data["main_id"]
        edited_type = f"{test_data['main_type']}-已编辑"
        data = {
            "id": main_id,
            "enableInd": 1,
            "maintenanceType": edited_type,
            "extension": {"remark1": "编辑后备注1", "remark2": "编辑后备注2"},
        }
        resp = login_session.post(f"{base_url}{MAIN_URL}", json=data)
        result = resp.json()
        assert result.get("code") == '0', f"编辑主表失败: {result}"

        # 回查验证
        r = login_session.post(
            f"{base_url}{MAIN_PAGE_URL}",
            json={"maintenanceTypeLike": edited_type, "page": 1, "size": 10},
        )
        records = r.json().get("data", {}).get("records", [])
        assert len(records) > 0, "编辑后应能查到记录"

    # ==================== 主表 - 唯一性校验 ====================

    def test_005_create_main_duplicate_type(self, base_url, login_session, test_data):
        """主表/反向：maintenanceType 重复（唯一性校验），应返回业务错误"""
        data = {
            "enableInd": 1,
            "maintenanceType": test_data["main_type"],
            "extension": {"remark1": "", "remark2": ""},
        }
        resp = login_session.post(f"{base_url}{MAIN_URL}", json=data)
        result = resp.json()
        assert result.get("code") != '0', f"重复 maintenanceType 应失败: {result}"

    # ==================== 主表 - 边界值 ====================

    def test_006_create_main_type_length_200(self, base_url, login_session):
        """主表/边界：maintenanceType 长度=200（临界合法值），应成功"""
        type_200 = "A" * 200
        resp = login_session.post(f"{base_url}{MAIN_URL}", json={
            "enableInd": 1,
            "maintenanceType": type_200,
            "extension": {"remark1": "", "remark2": ""},
        })
        result = resp.json()
        assert result.get("code") == '0', f"长度200应成功: {result}"

        # 回查并立即清理
        r = login_session.post(
            f"{base_url}{MAIN_PAGE_URL}",
            json={"maintenanceTypeLike": type_200, "page": 1, "size": 10},
        )
        records = r.json().get("data", {}).get("records", [])
        if records:
            login_session.post(f"{base_url}{MAIN_DELETE_URL}", json=[records[0]["id"]])

    def test_007_create_main_remark_length_255(self, base_url, login_session):
        """主表/边界：remark1 长度=255（临界值），应成功"""
        remark_255 = "R" * 255
        boundary_type = f"API测试-备注边界-{TS}"
        resp = login_session.post(f"{base_url}{MAIN_URL}", json={
            "enableInd": 1,
            "maintenanceType": boundary_type,
            "extension": {"remark1": remark_255, "remark2": ""},
        })
        result = resp.json()
        assert result.get("code") == '0', f"remark1长度255应成功: {result}"

        # 清理
        r = login_session.post(
            f"{base_url}{MAIN_PAGE_URL}",
            json={"maintenanceTypeLike": boundary_type, "page": 1, "size": 10},
        )
        records = r.json().get("data", {}).get("records", [])
        if records:
            login_session.post(f"{base_url}{MAIN_DELETE_URL}", json=[records[0]["id"]])

    # ==================== 主表 - enableInd 验证 ====================

    def test_008_verify_main_disabled(self, base_url, login_session, test_data):
        """主表/验证：enableInd=0 的主表记录已正确存储"""
        r = login_session.post(
            f"{base_url}{MAIN_PAGE_URL}",
            json={"maintenanceTypeLike": test_data["disabled_type"], "page": 1, "size": 10},
        )
        records = r.json().get("data", {}).get("records", [])
        assert len(records) > 0
        enable = records[0].get("enableInd")
        assert enable in (0, "0"), f"enableInd 应为0: {records[0]}"

    # ==================== 子表 - 关联查询（验证子表不空） ====================

    def test_009_detail_records_exist_under_main(self, base_url, login_session, test_data):
        """子表/关联：主表下应查到子表记录（验证子表不为空）"""
        resp = login_session.post(
            f"{base_url}{DETAIL_PAGE_URL}",
            json={
                "maintenanceContentLike": "",
                "page": 1,
                "size": 30,
                "maintenanceContentDefinitionId": test_data["main_id"],
            },
        )
        assert resp.status_code == 200
        result = resp.json()
        assert result.get("code") == '0', f"子表查询失败: {result}"
        records = result.get("data", {}).get("records", [])
        assert len(records) > 0, f"主表 {test_data['main_id']} 下应有子表记录，实际为空"

    def test_010_detail_records_filter_by_content(self, base_url, login_session, test_data):
        """子表/查询：按 maintenanceContentLike 模糊搜索子表"""
        resp = login_session.post(
            f"{base_url}{DETAIL_PAGE_URL}",
            json={
                "maintenanceContentLike": test_data["content_name"],
                "page": 1,
                "size": 30,
                "maintenanceContentDefinitionId": test_data["main_id"],
            },
        )
        records = resp.json().get("data", {}).get("records", [])
        assert len(records) > 0
        for r in records:
            assert test_data["content_name"] in r.get("maintenanceContent", "")

    # ==================== 子表 - enableInd 验证 ====================

    def test_011_verify_detail_disabled(self, base_url, login_session, test_data):
        """子表/验证：enableInd=0 的子表记录已正确存储"""
        r = login_session.post(
            f"{base_url}{DETAIL_PAGE_URL}",
            json={
                "maintenanceContentLike": test_data["disabled_content"],
                "page": 1,
                "size": 10,
                "maintenanceContentDefinitionId": test_data["main_id"],
            },
        )
        records = r.json().get("data", {}).get("records", [])
        if records:
            enable = records[0].get("enableInd")
            assert enable in (0, "0"), f"enableInd 应为0: {records[0]}"

    # ==================== 子表 - 编辑 ====================

    def test_012_edit_detail(self, base_url, login_session, test_data):
        """子表/正向：编辑已有子表记录"""
        if not test_data.get("detail_id"):
            pytest.skip("依赖前置：无子表ID")
        edited_content = f"{test_data['content_name']}-已编辑"
        data = {
            "id": test_data["detail_id"],
            "enableInd": 1,
            "maintenanceContent": edited_content,
            "maintenanceContentDefinitionId": test_data["main_id"],
        }
        resp = login_session.post(f"{base_url}{DETAIL_URL}", json=data)
        result = resp.json()
        assert result.get("code") == '0', f"编辑子表失败: {result}"

        # 回查验证
        r = login_session.post(
            f"{base_url}{DETAIL_PAGE_URL}",
            json={
                "maintenanceContentLike": edited_content,
                "page": 1,
                "size": 10,
                "maintenanceContentDefinitionId": test_data["main_id"],
            },
        )
        records = r.json().get("data", {}).get("records", [])
        assert len(records) > 0, "编辑后应能查到记录"

    # ==================== 子表 - 唯一性校验 ====================

    def test_013_create_detail_duplicate_content(self, base_url, login_session, test_data):
        """子表/反向：maintenanceContent 重复（唯一性校验），应返回业务错误"""
        data = {
            "enableInd": 1,
            "maintenanceContent": test_data["content_name"],
            "maintenanceContentDefinitionId": test_data["main_id"],
        }
        resp = login_session.post(f"{base_url}{DETAIL_URL}", json=data)
        result = resp.json()
        assert result.get("code") != '0', f"重复 maintenanceContent 应失败: {result}"

    # ==================== 子表 - 边界值 ====================

    def test_014_create_detail_content_length_200(self, base_url, login_session, test_data):
        """子表/边界：maintenanceContent 长度=200（临界合法值），应成功"""
        content_200 = "C" * 200
        resp = login_session.post(f"{base_url}{DETAIL_URL}", json={
            "enableInd": 1,
            "maintenanceContent": content_200,
            "maintenanceContentDefinitionId": test_data["main_id"],
        })
        result = resp.json()
        assert result.get("code") == '0', f"长度200应成功: {result}"

        # 清理
        r = login_session.post(
            f"{base_url}{DETAIL_PAGE_URL}",
            json={
                "maintenanceContentLike": content_200,
                "page": 1,
                "size": 10,
                "maintenanceContentDefinitionId": test_data["main_id"],
            },
        )
        records = r.json().get("data", {}).get("records", [])
        if records:
            login_session.post(f"{base_url}{DETAIL_DELETE_URL}", json=[records[0]["id"]])

    # ==================== 主表字段验证 ====================

    def test_015_verify_main_response_fields(self, base_url, login_session, test_data):
        """主表/验证：分页查询返回字段完整性（维修类型、是否启用、创建人、创建时间、更新人、更新时间）"""
        resp = login_session.post(
            f"{base_url}{MAIN_PAGE_URL}",
            json={"maintenanceTypeLike": test_data["main_type"], "page": 1, "size": 10},
        )
        records = resp.json().get("data", {}).get("records", [])
        assert len(records) > 0
        r = records[0]
        expected_fields = ["maintenanceType", "enableInd", "creatorName", "createTime",
                           "updatorName", "updateTime"]
        for field in expected_fields:
            assert field in r, f"主表记录缺少字段: {field}"

    # ==================== 子表字段验证 ====================

    def test_016_verify_detail_response_fields(self, base_url, login_session, test_data):
        """子表/验证：子表查询返回字段完整性"""
        resp = login_session.post(
            f"{base_url}{DETAIL_PAGE_URL}",
            json={
                "maintenanceContentLike": test_data["content_name"],
                "page": 1,
                "size": 10,
                "maintenanceContentDefinitionId": test_data["main_id"],
            },
        )
        records = resp.json().get("data", {}).get("records", [])
        assert len(records) > 0, f"子表查询应有结果"
        r = records[0]
        for field in ["maintenanceContent", "enableInd", "maintenanceContentDefinitionId"]:
            assert field in r, f"子表记录缺少字段: {field}"
