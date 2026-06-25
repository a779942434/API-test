"""CLI 无头模式 — 不依赖 Streamlit，从命令行执行 Pipeline 测试。

用法:
    # 从保存的会话文件执行
    python -m api_test_workbench.cli --load saves/my_pipeline.json

    # 指定环境 + 报告输出
    python -m api_test_workbench.cli --load saves/regression.json --env staging --junit report.xml

    # 查看所有存档
    python -m api_test_workbench.cli --list

    # 查看帮助
    python -m api_test_workbench.cli --help

退出码: 0=全部通过, 1=有失败/错误
"""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Optional

from api_test_workbench.engine.logger import setup_logger
from api_test_workbench.engine.session_store import load as load_session, list_saves
from api_test_workbench.engine.environment import load_environment, resolve_env_variables
from api_test_workbench.engine.runner import get_auth_session, execute_pipeline
from api_test_workbench.engine.models import Pipeline, PipelineResult, TestCase, ApiStep, ApiConfig
from api_test_workbench.engine.reporter import generate_html_report, generate_json_report, generate_junit_report

log = setup_logger("cli")


def _build_pipeline_from_json(data: dict) -> tuple[Pipeline, dict, dict, dict]:
    """从 JSON 定义文件构建 Pipeline + 测试用例 + 认证信息。

    期望格式:
    {
        "pipeline": { "name": "...", "steps": [...] },
        "pipeline_test_cases_by_step": { "0": [...], "1": [...] },
        "auth_url": "...",
        "auth_username": "...",
        "auth_password": "...",
        "auth_tenant_id": "..."
    }
    """
    from api_test_workbench.engine.session_store import _deserialize_pipeline, _deserialize_test_cases

    pipeline = _deserialize_pipeline(data.get("pipeline", {}))
    test_cases_by_step = _deserialize_test_cases(data.get("pipeline_test_cases_by_step", {}))
    auth_info = {
        "auth_url": data.get("auth_url", ""),
        "auth_username": data.get("auth_username", ""),
        "auth_password": data.get("auth_password", ""),
        "auth_tenant_id": data.get("auth_tenant_id", ""),
    }
    env_vars = data.get("env_variables", {})
    return pipeline, test_cases_by_step, auth_info, env_vars


def _print_summary(result: PipelineResult):
    """打印执行摘要到终端。"""
    total = 0
    passed = 0
    failed = 0
    total_time = 0.0

    for sr in result.step_results:
        n = len(sr.test_results)
        p = sum(1 for t in sr.test_results if t.passed)
        f = n - p
        total += n
        passed += p
        failed += f
        step_time = sum(t.response_time_ms for t in sr.test_results)
        total_time += step_time
        status = "✅" if not f and not sr.skipped else ("⚠️ 跳过" if sr.skipped else "❌")
        print(f"  {status} Step {sr.step_index}: {sr.step_name} — {p}/{n} 通过 ({step_time:.0f}ms)")

    print(f"\n{'='*50}")
    print(f"  Pipeline: {result.pipeline_name}")
    print(f"  总计: {total} | 通过: {passed} | 失败: {failed} | 耗时: {total_time:.0f}ms")
    if result.stopped_at_step >= 0:
        print(f"  ⛔ 在第 {result.stopped_at_step + 1} 步中止")
    print(f"{'='*50}")


def main():
    parser = argparse.ArgumentParser(
        description="API 测试工作台 — CLI 无头模式",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  %(prog)s --load saves/my_pipeline.json
  %(prog)s --load saves/regression.json --env staging --junit report.xml
  %(prog)s --pipeline definition.json --env prod --html report.html
  %(prog)s --list
        """,
    )

    # 输入源（二选一）
    input_group = parser.add_mutually_exclusive_group()
    input_group.add_argument("--load", type=str, metavar="FILE",
                             help="从保存的会话 JSON 文件加载并执行")
    input_group.add_argument("--pipeline", type=str, metavar="FILE",
                             help="从 pipeline 定义 JSON 文件执行（格式见文档）")
    input_group.add_argument("--list", action="store_true",
                             help="列出所有已保存的会话")

    # 执行选项
    parser.add_argument("--env", type=str, default="", metavar="NAME",
                        help="指定运行环境名称（如 dev/staging/prod）")
    parser.add_argument("--tags", type=str, default="", metavar="TAGS",
                        help="按标签筛选用例（逗号分隔，预留）")

    # 报告输出
    parser.add_argument("--junit", type=str, default="", metavar="FILE",
                        help="输出 JUnit XML 报告到指定文件")
    parser.add_argument("--html", type=str, default="", metavar="FILE",
                        help="输出 HTML 报告到指定文件")
    parser.add_argument("--json-report", type=str, default="", metavar="FILE",
                        help="输出 JSON 报告到指定文件")

    # 其他
    parser.add_argument("--workers", type=int, default=5, metavar="N",
                        help="并行执行线程数（默认 5，1=串行）")
    parser.add_argument("--timeout", type=int, default=30, metavar="SEC",
                        help="请求超时秒数（默认 30）")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="显示详细日志")

    args = parser.parse_args()

    # --list: 列出存档
    if args.list:
        saves = list_saves()
        if not saves:
            print("没有找到已保存的会话")
            return 0
        print(f"已保存的会话 ({len(saves)}):\n")
        for s in saves:
            print(f"  {s['name']}")
            print(f"    管道: {s['pipeline_name']}")
            print(f"    保存时间: {s['saved_at']}")
            print(f"    路径: {s['path']}")
            print()
        return 0

    # 验证输入源
    if not args.load and not args.pipeline:
        parser.print_help()
        print("\n错误: 必须指定 --load 或 --pipeline", file=sys.stderr)
        return 1

    # ── 加载 Pipeline ──
    if args.load:
        filepath = args.load
        if not os.path.isabs(filepath):
            # 尝试在 saves 目录中查找
            from api_test_workbench.engine.session_store import SAVE_DIR
            candidate = SAVE_DIR / filepath
            if candidate.exists():
                filepath = str(candidate)
            elif not os.path.exists(filepath):
                print(f"错误: 存档文件不存在: {filepath}", file=sys.stderr)
                return 1

        data = load_session(filepath)
        if data is None:
            print(f"错误: 无法加载存档: {filepath}", file=sys.stderr)
            return 1

        pipeline = data["pipeline"]
        test_cases_by_step = data["pipeline_test_cases_by_step"]
        auth_info = {
            "auth_url": data.get("auth_url", ""),
            "auth_username": data.get("auth_username", ""),
            "auth_password": data.get("auth_password", ""),
            "auth_tenant_id": data.get("auth_tenant_id", ""),
        }
        env_vars = {}
        log.info("已加载存档: %s (%d 步骤)", filepath, len(pipeline.steps))

    else:  # --pipeline
        with open(args.pipeline, "r", encoding="utf-8") as f:
            pipeline_data = json.load(f)
        pipeline, test_cases_by_step, auth_info, env_vars = _build_pipeline_from_json(pipeline_data)
        log.info("已加载 Pipeline 定义: %s (%d 步骤)", args.pipeline, len(pipeline.steps))

    # ── 环境变量 ──
    if args.env:
        env_data = load_environment(args.env)
        if env_data is None:
            print(f"错误: 环境 '{args.env}' 不存在", file=sys.stderr)
            return 1
        # 合并环境变量
        env_vars = {**env_vars, **env_data.get("variables", {})}
        # 如果环境中定义了认证端点，优先使用
        if env_data.get("auth_endpoint") and not auth_info["auth_url"]:
            auth_info["auth_url"] = env_data["auth_endpoint"]
        if env_data.get("auth_body") and not auth_info.get("auth_username"):
            ab = env_data["auth_body"]
            auth_info["auth_username"] = ab.get("username", "")
            auth_info["auth_password"] = ab.get("password", "")
        log.info("已加载环境: %s", args.env)

    # 环境变量替换 Pipeline URL 中的 {{VAR}}
    if env_vars:
        for step in pipeline.steps:
            step.config.url = resolve_env_variables(step.config.url, env_vars)
            if isinstance(step.config.headers, dict):
                step.config.headers = resolve_env_variables(step.config.headers, env_vars)

    # ── 认证 ──
    session = None
    auth_url = auth_info.get("auth_url", "")
    if auth_url:
        auth_body = {
            "username": auth_info.get("auth_username", ""),
            "password": auth_info.get("auth_password", ""),
        }
        tenant_id = auth_info.get("auth_tenant_id", "")
        if tenant_id:
            auth_body["tenantId"] = tenant_id

        # 环境变量替换认证 URL
        if env_vars:
            auth_url = resolve_env_variables(auth_url, env_vars)

        log.info("正在登录: %s", auth_url)
        try:
            session = get_auth_session(auth_url, auth_body, tenant_id)
            log.info("登录成功")
        except Exception as e:
            print(f"错误: 登录失败 — {e}", file=sys.stderr)
            return 1
    else:
        import requests
        session = requests.Session()
        log.warning("未配置认证 URL，使用未认证 Session")

    # ── 执行 ──
    log.info("开始执行 Pipeline: %s", pipeline.name)

    def _progress(step_idx, total_steps, step_result):
        """CLI 进度回调"""
        passed = sum(1 for t in step_result.test_results if t.passed)
        total = len(step_result.test_results)
        status = "✅" if step_result.passed else "❌"
        print(f"  [{step_idx+1}/{total_steps}] {status} {step_result.step_name}: {passed}/{total}")

    try:
        result = execute_pipeline(
            pipeline=pipeline,
            session=session,
            test_cases_by_step=test_cases_by_step,
            progress_callback=_progress,
            env_variables=env_vars,
            max_workers=args.workers,
        )
    except Exception as e:
        print(f"错误: Pipeline 执行异常 — {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1

    # ── 输出摘要 ──
    _print_summary(result)

    # ── 生成报告 ──
    if args.junit:
        try:
            xml = generate_junit_report(result, pipeline.name)
            with open(args.junit, "w", encoding="utf-8") as f:
                f.write(xml)
            print(f"\n📄 JUnit 报告已保存: {args.junit}")
        except Exception as e:
            print(f"警告: JUnit 报告生成失败 — {e}", file=sys.stderr)

    if args.html:
        try:
            html = generate_html_report(result, pipeline)
            with open(args.html, "w", encoding="utf-8") as f:
                f.write(html)
            print(f"📄 HTML 报告已保存: {args.html}")
        except Exception as e:
            print(f"警告: HTML 报告生成失败 — {e}", file=sys.stderr)

    if getattr(args, 'json_report', None):
        try:
            report = generate_json_report(result)
            with open(args.json_report, "w", encoding="utf-8") as f:
                json.dump(report, f, ensure_ascii=False, indent=2)
            print(f"📄 JSON 报告已保存: {args.json_report}")
        except Exception as e:
            print(f"警告: JSON 报告生成失败 — {e}", file=sys.stderr)

    # ── 退出码 ──
    if not result.overall_passed:
        print("\n❌ 存在失败用例", file=sys.stderr)
        return 1

    print("\n✅ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
