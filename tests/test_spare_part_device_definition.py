"""备件可用设备定义 - API 全流程自动化测试（精简版）"""

import random
import time
import pytest


# ========== API 路径常量 ==========
DEVICE_OPTION_URL = "/linkim-pc/admin-console/main-data/device-definition/get-option"
DEVICE_PART_URL = "/linkim-pc/admin-console/tooling/devicePartRelation"
DEVICE_PART_LIST_URL = "/linkim-pc/admin-console/tooling/devicePartRelation/findList"
SPARE_TYPE_PAGE_URL = "/linkim-pc/admin-console/tooling/sparePartType/page"
SPARE_INFO_PAGE_URL = "/linkim-pc/admin-console/tooling/sparePartInfo/page"
SPARE_DEVICE_URL = "/linkim-pc/admin-console/tooling/sparePartDevice"
SPARE_DEVICE_PAGE_URL = "/linkim-pc/admin-console/tooling/sparePartDevice/page"
SPARE_DEVICE_DELETE_URL = "/linkim-pc/admin-console/tooling/sparePartDevice/batch-delete"

# ========== 集中测试数据配置 ==========
TS = int(time.time())

PAGE_DEFAULT = {"page": 1, "size": 30}
DEVICE_OPTION_PARAMS = {"page": 1, "size": 1000, "enableInd": 1}
SPARE_TYPE_PARAMS = {"enableInd": "1", "page": 1, "size": 100}
SPARE_INFO_PARAMS = {"enableInd": 1, "page": 1, "size": 100}

# 必填字段校验场景
REQUIRED_FIELD_CASES = [
    {"field": "deviceId", "desc": "deviceId"},
    {"field": "devicePartId", "desc": "devicePartId"},
    {"field": "sparePartId", "desc": "sparePartId"},
    {"field": "spareTypeId", "desc": "spareTypeId"},
]

# enableInd 非法值场景
INVALID_ENABLE_IND_CASES = [
    {"value": 2, "check_status": True, "expected": 400, "desc": "enableInd=2"},
    {"value": -1, "check_status": False, "desc": "enableInd=-1"},
]

# 不存在的外键场景
NONEXISTENT_FK_CASES = [
    {"field": "deviceId", "value": "NONEXISTENT_DEVICE_99999", "desc": "不存在 deviceId"},
    {"field": "sparePartId", "value": "NONEXISTENT_SPARE_99999", "desc": "不存在 sparePartId"},
]

# 查询返回期望字段
EXPECTED_FIELDS = [
    "id", "spareTypeId", "sparePartId", "deviceId", "devicePartId",
    "enableInd", "createTime", "updateTime",
]


# ========== Fixture：仅准备依赖数据 ==========

@pytest.fixture(scope="class")
def test_data(bird_base_url, bird_login_session):
    """准备依赖数据：设备 → 设备部位 → 备件类型/备件。不创建备件设备定义。"""
    s = bird_login_session
    url = bird_base_url

    # Step 1: 获取设备
    r = s.post(f"{url}{DEVICE_OPTION_URL}", json=DEVICE_OPTION_PARAMS)
    assert r.status_code == 200, f"获取设备列表失败 {r.status_code}"
    result = r.json()
    assert result.get("code") == '0', f"获取设备列表业务失败 {result}"
    devices = result.get("data", [])
    if isinstance(devices, dict):
        devices = devices.get("records", [])
    assert len(devices) > 0, "系统中无可用设备"
    device = random.choice(devices)
    device_id = device.get("id")
    device_name = device.get("deviceName")
    assert device_id, f"设备记录缺少 id: {device}"

    # Step 2: 创建设备部位
    part_name = f"API测试-设备部位-{TS}"
    r = s.post(f"{url}{DEVICE_PART_URL}", json={
        "deviceId": device_id,
        "devicePartName": part_name,
        "enableInd": 1,
    })
    assert r.json().get("code") == '0', f"新增设备部位失败 {r.json()}"

    r = s.post(f"{url}{DEVICE_PART_LIST_URL}", json={"deviceIdEq": device_id})
    assert r.json().get("code") == '0', f"查询设备部位失败 {r.json()}"
    parts = r.json().get("data", [])
    if isinstance(parts, dict):
        parts = parts.get("records", [])
    device_part_id = next(
        (p.get("id") or p.get("devicePartId") for p in parts if p.get("devicePartName") == part_name),
        None,
    )
    assert device_part_id, "未找到新建的设备部位"

    # Step 3: 获取备件类型和备件
    r = s.post(f"{url}{SPARE_TYPE_PAGE_URL}", json=SPARE_TYPE_PARAMS)
    assert r.json().get("code") == '0', f"获取备件类型失败 {r.json()}"
    type_records = r.json().get("data", {}).get("records", [])
    assert len(type_records) > 0, "系统中无备件类型"
    random.shuffle(type_records)

    spare_type_id = None
    spare_type_name = ""
    spare_records = []
    for st in type_records:
        tid = st.get("id")
        r = s.post(f"{url}{SPARE_INFO_PAGE_URL}", json={
            **SPARE_INFO_PARAMS, "spareTypeIdEq": tid,
        })
        if r.json().get("code") != '0':
            continue
        candidates = r.json().get("data", {}).get("records", [])
        if len(candidates) > 0:
            spare_type_id = tid
            spare_type_name = st.get("spareTypeName")
            spare_records = candidates
            break
    assert spare_type_id, "所有备件类型下均无备件"

    spare = spare_records[0]
    spare_part_id = spare.get("id") or spare.get("sparePartId")
    spare_part_name = spare.get("sparePartName") or spare.get("name", "")
    spare_part_code = spare.get("sparePartCode") or spare.get("code", "")

    spare_part_id_2 = None
    spare_part_name_2 = ""
    if len(spare_records) >= 2:
        spare2 = spare_records[1]
        spare_part_id_2 = spare2.get("id") or spare2.get("sparePartId")
        spare_part_name_2 = spare2.get("sparePartName") or spare2.get("name", "")

    data = {
        "device_id": device_id,
        "device_name": device_name,
        "device_part_id": device_part_id,
        "device_part_name": part_name,
        "spare_type_id": spare_type_id,
        "spare_type_name": spare_type_name,
        "spare_part_id": spare_part_id,
        "spare_part_name": spare_part_name,
        "spare_part_code": spare_part_code,
        "spare_part_id_2": spare_part_id_2,
        "spare_part_name_2": spare_part_name_2,
        # 以下由测试方法填充
        "spare_device_id": None,
    }

    print(f"\n{'='*60}"
          f"\n[依赖数据] 设备: {device_name} (ID: {device_id})"
          f"\n[依赖数据] 设备部位: {part_name} (ID: {device_part_id})"
          f"\n[依赖数据] 备件类型: {spare_type_name} (ID: {spare_type_id})"
          f"\n[依赖数据] 备件1: {spare_part_name}/{spare_part_code} (ID: {spare_part_id})"
          f"\n[依赖数据] 备件2: {spare_part_name_2} (ID: {spare_part_id_2})"
          f"\n{'='*60}")

    yield data
    # 不执行清理，保留正常数据供页面验证


# ========== 测试类 ==========

class TestSparePartDeviceDefinition:
    """备件可用设备定义 API 全流程测试（新增→编辑→查询→删除→保留数据）"""

    # ==================== 1. 新增 + 查询验证 ====================

    def test_01_create_and_query(self, bird_base_url, bird_login_session, test_data):
        """新增备件设备定义 → 回查验证数据一致 → 分页结构 → 字段完整性 → 备件信息带出"""
        s = bird_login_session
        url = bird_base_url

        # 新增
        payload = {
            "enableInd": 1,
            "spareTypeId": test_data["spare_type_id"],
            "sparePartId": test_data["spare_part_id"],
            "deviceId": test_data["device_id"],
            "devicePartId": test_data["device_part_id"],
        }
        resp = s.post(f"{url}{SPARE_DEVICE_URL}", json=payload)
        result = resp.json()
        assert result.get("code") == '0', f"新增失败: {result}"

        # 回查验证
        r = s.post(f"{url}{SPARE_DEVICE_PAGE_URL}", json={
            **PAGE_DEFAULT,
            "deviceIdEq": test_data["device_id"],
            "devicePartIdEq": test_data["device_part_id"],
        })
        assert r.json().get("code") == '0', f"回查失败: {r.json()}"
        records = r.json().get("data", {}).get("records", [])
        assert len(records) > 0, "应至少存在1条记录"

        target = next(
            (rec for rec in records
             if str(rec.get("sparePartId") or rec.get("sparePartId")) == str(test_data["spare_part_id"])),
            None,
        )
        assert target is not None, f"未找到刚创建的记录: {records}"
        spare_device_id = target.get("id")
        assert spare_device_id, f"记录缺少 id: {target}"
        test_data["spare_device_id"] = spare_device_id

        # 分页结构
        r2 = s.post(f"{url}{SPARE_DEVICE_PAGE_URL}", json=PAGE_DEFAULT)
        data = r2.json().get("data", {})
        assert "records" in data, "返回体应含 records"
        assert "pages" in data or "total" in data, "返回体应含 pages 或 total"

        # 字段完整性
        for field in EXPECTED_FIELDS:
            assert field in target, f"记录缺少字段: {field}"

        # 备件信息带出
        spare_name = target.get("sparePartName") or target.get("spareName")
        spare_code = target.get("sparePartCode") or target.get("spareCode")
        assert spare_name, f"应包含备件名称: {target}"
        assert spare_code, f"应包含备件编码: {target}"

        print(f"\n[test_01] 创建成功 spare_device_id={spare_device_id}")

    # ==================== 2. 编辑 + 验证 ====================

    def test_02_edit_and_verify(self, bird_base_url, bird_login_session, test_data):
        """PUT 编辑切换 enableInd: 1→0 → 回查验证 → 0→1 恢复 → 回查验证"""
        sd_id = test_data.get("spare_device_id")
        if not sd_id:
            pytest.skip("依赖 test_01 创建的 spare_device_id")

        s = bird_login_session
        url = bird_base_url

        def _query_enable():
            r = s.post(f"{url}{SPARE_DEVICE_PAGE_URL}", json={
                **PAGE_DEFAULT,
                "deviceIdEq": test_data["device_id"],
                "devicePartIdEq": test_data["device_part_id"],
            })
            for rec in r.json().get("data", {}).get("records", []):
                if str(rec.get("id")) == str(sd_id):
                    return rec.get("enableInd")
            return None

        edit_body = {
            "id": sd_id,
            "enableInd": 0,
            "spareTypeId": test_data["spare_type_id"],
            "sparePartId": test_data["spare_part_id"],
            "deviceId": test_data["device_id"],
            "devicePartId": test_data["device_part_id"],
        }

        # 1→0
        resp = s.put(f"{url}{SPARE_DEVICE_URL}", json=edit_body)
        assert resp.json().get("code") == '0', f"编辑失败(1→0): {resp.json()}"
        enable = _query_enable()
        assert enable in (0, "0"), f"enableInd 应为0，实际: {enable}"

        # 0→1 恢复
        edit_body["enableInd"] = 1
        resp = s.put(f"{url}{SPARE_DEVICE_URL}", json=edit_body)
        assert resp.json().get("code") == '0', f"编辑失败(0→1): {resp.json()}"
        enable = _query_enable()
        assert enable in (1, "1"), f"enableInd 应恢复为1，实际: {enable}"

        print(f"\n[test_02] 编辑通过 enableInd 1→0→1 切换正常")

    # ==================== 3. 反向用例 ====================

    def test_03_negative_cases(self, bird_base_url, bird_login_session, test_data):
        """反向用例：重复新增、必填为空、enableInd 非法值、不存在外键"""
        s = bird_login_session
        url = bird_base_url

        base = {
            "enableInd": 1,
            "spareTypeId": test_data["spare_type_id"],
            "sparePartId": test_data["spare_part_id"],
            "deviceId": test_data["device_id"],
            "devicePartId": test_data["device_part_id"],
        }

        # 重复新增
        resp = s.post(f"{url}{SPARE_DEVICE_URL}", json=base)
        assert resp.json().get("code") != '0', f"重复新增应返回业务错误: {resp.json()}"

        # 必填字段为空
        for case in REQUIRED_FIELD_CASES:
            payload = {**base, case["field"]: ""}
            resp = s.post(f"{url}{SPARE_DEVICE_URL}", json=payload)
            assert resp.json().get("code") != '0', \
                f"{case['desc']} 为空应返回业务错误: {resp.json()}"

        # enableInd 非法值
        for case in INVALID_ENABLE_IND_CASES:
            payload = {**base, "enableInd": case["value"]}
            resp = s.post(f"{url}{SPARE_DEVICE_URL}", json=payload)
            if case["check_status"]:
                assert resp.status_code == case["expected"], \
                    f"{case['desc']} status={resp.status_code} != {case['expected']}"
            else:
                assert resp.json().get("code") != '0', \
                    f"{case['desc']}: {resp.json()}"

        # 不存在的外键
        for case in NONEXISTENT_FK_CASES:
            payload = {**base, case["field"]: case["value"]}
            resp = s.post(f"{url}{SPARE_DEVICE_URL}", json=payload)
            assert resp.json().get("code") != '0', \
                f"{case['desc']}: {resp.json()}"

        print(f"\n[test_03] 所有反向用例通过")

    # ==================== 4. 删除 + 校验 ====================

    def test_04_delete_and_verify(self, bird_base_url, bird_login_session, test_data):
        """新增临时记录 → 批量删除 → 回查确认不存在"""
        if not test_data.get("spare_part_id_2"):
            pytest.skip("需要第二个备件才能执行删除测试")

        s = bird_login_session
        url = bird_base_url

        # 创建待删除记录（用备件2，避免与 test_01 的唯一约束冲突）
        resp = s.post(f"{url}{SPARE_DEVICE_URL}", json={
            "enableInd": 0,
            "spareTypeId": test_data["spare_type_id"],
            "sparePartId": test_data["spare_part_id_2"],
            "deviceId": test_data["device_id"],
            "devicePartId": test_data["device_part_id"],
        })
        assert resp.json().get("code") == '0', f"创建待删除记录失败: {resp.json()}"

        # 回查找 ID
        r = s.post(f"{url}{SPARE_DEVICE_PAGE_URL}", json={
            **PAGE_DEFAULT,
            "deviceIdEq": test_data["device_id"],
            "devicePartIdEq": test_data["device_part_id"],
        })
        records = r.json().get("data", {}).get("records", [])
        delete_id = None
        for rec in records:
            rid = str(rec.get("sparePartId") or rec.get("sparePartId"))
            if rid == str(test_data["spare_part_id_2"]) and rec.get("enableInd") in (0, "0"):
                candidate = rec.get("id")
                if str(candidate) != str(test_data.get("spare_device_id")):
                    delete_id = candidate
                    break
        assert delete_id, f"未找到待删除记录: {records}"

        # 删除
        del_resp = s.post(f"{url}{SPARE_DEVICE_DELETE_URL}", json=[delete_id])
        assert del_resp.json().get("code") == '0', f"删除失败: {del_resp.json()}"

        # 回查确认不存在
        r = s.post(f"{url}{SPARE_DEVICE_PAGE_URL}", json={
            **PAGE_DEFAULT,
            "deviceIdEq": test_data["device_id"],
            "devicePartIdEq": test_data["device_part_id"],
        })
        remaining = [str(rec.get("id")) for rec in r.json().get("data", {}).get("records", [])]
        assert str(delete_id) not in remaining, f"删除后记录仍存在: {delete_id}"

        print(f"\n[test_04] 删除+校验通过 ID={delete_id}")

    # ==================== 5. 保留数据汇总 ====================

    def test_05_keep_data_summary(self, test_data):
        """打印保留的正常数据摘要，供页面手动验证"""
        print(f"\n{'='*60}"
              f"\n[保留数据-页面验证]"
              f"\n  设备: {test_data['device_name']} (ID: {test_data['device_id']})"
              f"\n  设备部位: {test_data['device_part_name']} (ID: {test_data['device_part_id']})"
              f"\n  备件类型: {test_data['spare_type_name']} (ID: {test_data['spare_type_id']})"
              f"\n  备件1: {test_data['spare_part_name']}/{test_data['spare_part_code']} (ID: {test_data['spare_part_id']})"
              f"\n  备件设备定义ID: {test_data.get('spare_device_id')}"
              f"\n  以上数据已保留，可在 bird 页面手动查看验证"
              f"\n{'='*60}")
