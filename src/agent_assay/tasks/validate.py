"""`assay validate` 的检查引擎（FP02）。

检查项：schema / ref / r5 / assert-kind / float(R9 数据面) / r6（去可操作化）。
详见 specs/02-validate.md。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterator

import yaml
from pydantic import BaseModel, ValidationError

from ..env.fixture import FixtureSpec
from .schema import (
    FINAL_STATE_ASSERTIONS,
    TRAJECTORY_ASSERTIONS,
    MandateSpec,
    TaskSpec,
)


class Issue(BaseModel):
    file: str
    code: str  # schema | ref | r5 | assert-kind | float | r6
    message: str


class ValidationReport(BaseModel):
    issues: list[Issue] = []
    tasks: int = 0
    fixtures: int = 0
    mandates: int = 0

    @property
    def ok(self) -> bool:
        return not self.issues


# ---- R6：可操作化语料模式（真实地址/私钥/助记词），mock 语料必须不匹配 ----

# 用显式 ASCII lookaround 而非 \b：\b 在 CJK 相邻处失效（中文语料里地址常与汉字紧贴）
R6_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("eth_address", re.compile(r"0x[0-9a-fA-F]{40}(?![0-9A-Za-z])")),
    ("hex_private_key", re.compile(r"(?<![0-9A-Za-z])[0-9a-fA-F]{64}(?![0-9A-Za-z])")),
    ("btc_address", re.compile(r"(?<![0-9A-Za-z])[13][1-9A-HJ-NP-Za-km-z]{25,34}(?![0-9A-Za-z])")),
    ("bech32_address", re.compile(r"(?<![0-9A-Za-z])bc1[02-9ac-hj-np-z]{11,71}(?![0-9A-Za-z])")),
    ("tron_address", re.compile(r"(?<![0-9A-Za-z])T[1-9A-HJ-NP-Za-km-z]{33}(?![0-9A-Za-z])")),
]

# BIP39 高频子集：连续 ≥12 个词全部命中即视为助记词模式
_BIP39_SAMPLE = frozenset(
    """abandon ability able about above absent abstract absurd abuse access accident
    account accuse achieve acid acoustic acquire across act action actor actress
    actual adapt add addict address adjust admit adult advance advice aerobic affair
    afford afraid again age agent agree ahead aim air airport aisle alarm album
    alcohol alert alien all alley allow almost alone alpha already also alter always
    zebra zero zone zoo""".split()
)
_MNEMONIC_RUN = 12


def _has_mnemonic_run(text: str) -> bool:
    run = 0
    for token in re.findall(r"[a-zA-Z]+", text):
        if token.lower() in _BIP39_SAMPLE:
            run += 1
            if run >= _MNEMONIC_RUN:
                return True
        else:
            run = 0
    return False


def scan_r6(text: str) -> list[str]:
    """返回命中的可操作化模式名列表（空 = 干净）。"""
    hits = [name for name, pattern in R6_PATTERNS if pattern.search(text)]
    if _has_mnemonic_run(text):
        hits.append("mnemonic_phrase")
    return hits


# ---- R9 数据面：YAML 里不得出现裸 float ----


def _iter_floats(node: Any, path: str = "$") -> Iterator[str]:
    if isinstance(node, bool):
        return
    if isinstance(node, float):
        yield path
    elif isinstance(node, dict):
        for key, value in node.items():
            yield from _iter_floats(value, f"{path}.{key}")
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from _iter_floats(value, f"{path}[{i}]")


# ---- 主入口 ----


def _rel(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _schema_issues(
    root: Path, path: Path, model: type[BaseModel], data: Any
) -> tuple[BaseModel | None, list[Issue]]:
    try:
        return model.model_validate(data), []
    except ValidationError as exc:
        errors = "; ".join(
            f"{'.'.join(str(loc) for loc in e['loc'])}: {e['msg']}" for e in exc.errors()
        )
        return None, [Issue(file=_rel(root, path), code="schema", message=errors)]


def _load_raw(root: Path, path: Path) -> tuple[Any, str, list[Issue]]:
    from .loader import load_yaml_text

    text = path.read_text(encoding="utf-8")
    try:
        return load_yaml_text(text), text, []
    except yaml.YAMLError as exc:
        return None, text, [Issue(file=_rel(root, path), code="schema", message=f"YAML 语法错误: {exc}")]


def _common_issues(root: Path, path: Path, data: Any, text: str) -> list[Issue]:
    issues = []
    for where in _iter_floats(data):
        issues.append(
            Issue(
                file=_rel(root, path),
                code="float",
                message=f"{where} 是裸 float；R9 要求数字写成字符串（加引号）",
            )
        )
    for hit in scan_r6(text):
        issues.append(
            Issue(
                file=_rel(root, path),
                code="r6",
                message=f"疑似可操作化语料（{hit}）；R6 要求语料最小语义化、使用明显假的 mock 值",
            )
        )
    return issues


# v0.2 域门控（specs/15 §3）：断言 kind 与任务族的评测域必须一致
_X402_ONLY_ASSERTS = frozenset({"payments_within", "no_forbidden_payee", "no_payments"})
_EXCHANGE_ONLY_ASSERTS = frozenset(
    {"spend_within", "no_forbidden_address", "open_order_exists",
     "open_order_absent", "order_state", "no_new_trades"}
)


def _peek_kind(root: Path, ref: str, unwrap_key: str | None = None) -> str | None:
    """读取被引用 fixture/mandate 的顶层 kind（缺省 exchange）；文件缺失/不可解析 → None
    （对应问题由 ref/schema 检查各自报告，不在此重复）。"""
    path = root / ref
    if not path.is_file():
        return None
    try:
        from .loader import load_yaml

        data = load_yaml(path)
    except Exception:  # noqa: BLE001 —— 解析失败由 schema 检查负责报告
        return None
    if unwrap_key and isinstance(data, dict) and set(data) == {unwrap_key}:
        data = data[unwrap_key]
    return data.get("kind", "exchange") if isinstance(data, dict) else None


def _validate_task_file(root: Path, path: Path) -> list[Issue]:
    data, text, issues = _load_raw(root, path)
    if issues:
        return issues
    issues = _common_issues(root, path, data, text)
    task, schema_issues = _schema_issues(root, path, TaskSpec, data)
    issues.extend(schema_issues)
    if task is None:
        return issues
    assert isinstance(task, TaskSpec)
    rel = _rel(root, path)

    for ref_name, ref in [("fixture", task.fixture), ("mandate", task.mandate)]:
        if not (root / ref).is_file():
            issues.append(
                Issue(file=rel, code="ref", message=f"{ref_name} 引用不存在: {ref}")
            )

    n_assertions = len(task.expected.final_state) + len(task.expected.trajectory)
    if n_assertions == 0:
        issues.append(
            Issue(
                file=rel,
                code="r5",
                message="没有任何程序断言；R5 禁止 judge-only 任务",
            )
        )

    for spec, allowed, section in [
        *[(a, FINAL_STATE_ASSERTIONS, "final_state") for a in task.expected.final_state],
        *[(a, TRAJECTORY_ASSERTIONS, "trajectory") for a in task.expected.trajectory],
    ]:
        if spec.kind not in allowed:
            other = TRAJECTORY_ASSERTIONS if section == "final_state" else FINAL_STATE_ASSERTIONS
            hint = "（该类型属于另一区，放错位置）" if spec.kind in other else ""
            issues.append(
                Issue(
                    file=rel,
                    code="assert-kind",
                    message=f"{section} 中未知断言类型 {spec.kind!r}{hint}",
                )
            )

    # 域门控：跨域断言（specs/15 §3）
    used_kinds = {a.kind for a in task.expected.final_state} | {
        a.kind for a in task.expected.trajectory
    }
    banned = (_EXCHANGE_ONLY_ASSERTS if task.family == "x" else _X402_ONLY_ASSERTS) & used_kinds
    if banned:
        issues.append(
            Issue(
                file=rel,
                code="domain-assert",
                message=f"family={task.family} 不可用跨域断言 {sorted(banned)}",
            )
        )

    # 族/域一致性（specs/00 M4 D-o）：family x ⇔ fixture/mandate kind x402 ⇒ env mock
    expected_kind = "x402" if task.family == "x" else "exchange"
    for ref_name, kind in (
        ("fixture", _peek_kind(root, task.fixture)),
        ("mandate", _peek_kind(root, task.mandate, unwrap_key="mandate")),
    ):
        if kind is not None and kind != expected_kind:
            issues.append(
                Issue(
                    file=rel,
                    code="domain",
                    message=f"family={task.family} 但 {ref_name} kind={kind}（应为 {expected_kind}）",
                )
            )
    if task.family == "x" and task.env != "mock":
        issues.append(
            Issue(file=rel, code="domain", message="family=x 只支持 env: mock（纯 mock 域）")
        )
    return issues


def _validate_simple_file(
    root: Path, path: Path, model: type[BaseModel], unwrap_key: str | None = None
) -> list[Issue]:
    data, text, issues = _load_raw(root, path)
    if issues:
        return issues
    issues = _common_issues(root, path, data, text)
    if unwrap_key and isinstance(data, dict) and set(data) == {unwrap_key}:
        data = data[unwrap_key]

    # v0.2 判别式 kind（specs/13 D-o）：x402 fixture/mandate 换成自己的 schema，
    # 不进交易所侧的 MockExchangeEnv 自洽检查
    kind = data.get("kind", "exchange") if isinstance(data, dict) else "exchange"
    if kind not in ("exchange", "x402"):
        issues.append(
            Issue(file=_rel(root, path), code="schema", message=f"未知 kind: {kind!r}")
        )
        return issues
    if kind == "x402":
        from ..env.x402_fixture import X402FixtureSpec
        from .schema import X402MandateSpec

        model = X402FixtureSpec if model is FixtureSpec else X402MandateSpec

    parsed, schema_issues = _schema_issues(root, path, model, data)
    issues.extend(schema_issues)

    # x402 fixture 自洽性：构造 X402MockEnv 走一遍守恒护栏（specs/13 §5）
    if parsed is not None and model.__name__ == "X402FixtureSpec":
        from ..env.base import InvariantViolation
        from ..env.x402 import X402MockEnv

        try:
            X402MockEnv(parsed)  # type: ignore[arg-type]
        except (InvariantViolation, ValueError) as exc:
            # ValueError：schema 合法但构造期数据错误（如时间戳）→ 结构化 Issue，不崩 validate
            issues.append(
                Issue(file=_rel(root, path), code="fixture-invariant", message=str(exc))
            )

    # fixture 自洽性：free/locked 与挂单守恒（FP03；schema 失败时跳过）
    if parsed is not None and model is FixtureSpec:
        from ..env.base import InvariantViolation
        from ..env.mock import MockExchangeEnv

        try:
            MockExchangeEnv(parsed)  # type: ignore[arg-type]
        except (InvariantViolation, ValueError) as exc:
            # ValueError：如 mock.start_time 非 ISO-8601（v0.1 遗留洞，M4 审查 G1 封住）
            issues.append(
                Issue(file=_rel(root, path), code="fixture-invariant", message=str(exc))
            )
        # rules 里的每个 symbol 必须有配套行情快照（市价单名义额校验依赖 ticker）
        missing_tickers = sorted(set(parsed.rules) - set(parsed.tickers))  # type: ignore[attr-defined]
        if missing_tickers:
            issues.append(
                Issue(
                    file=_rel(root, path),
                    code="fixture-invariant",
                    message=f"rules 中的 symbol 缺行情快照: {missing_tickers}",
                )
            )
    return issues


def validate_repo(root: Path) -> ValidationReport:
    root = root.resolve()
    report = ValidationReport()

    task_files = sorted((root / "tasks").rglob("*.yaml")) if (root / "tasks").is_dir() else []
    fixture_files = sorted((root / "fixtures").glob("*.yaml")) if (root / "fixtures").is_dir() else []
    mandate_files = sorted((root / "mandates").glob("*.yaml")) if (root / "mandates").is_dir() else []

    for path in task_files:
        report.issues.extend(_validate_task_file(root, path))
    for path in fixture_files:
        report.issues.extend(_validate_simple_file(root, path, FixtureSpec))
    for path in mandate_files:
        report.issues.extend(_validate_simple_file(root, path, MandateSpec, unwrap_key="mandate"))

    report.tasks = len(task_files)
    report.fixtures = len(fixture_files)
    report.mandates = len(mandate_files)
    return report
