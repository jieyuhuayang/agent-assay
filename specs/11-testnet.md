# specs/11 · FP11 Testnet 集成模式（M3）

> 上游：specs/00 §FP11（AC-11a–11e）、KICKOFF §11、§5 D1（mock 正式跑分 / testnet 集成演示）。
> 红线接触面：R1（唯一交易所外联白名单）、R2（key 仅环境变量）、R9（Decimal）。

## 1. 范围与定位

`env/testnet.py` 实现 `ExchangeEnv` 接口，经 ccxt `binance` + `set_sandbox_mode(True)` 接
Binance Spot Testnet（`https://testnet.binance.vision/api`）。**定位是真实性演示与 API 兼容性
验证**（D1）：正式跑分一律 mock；testnet 只跑 `env: both` 的 A/B 抽样，断言放宽为结构正确性，
结果不进 leaderboard。

## 2. 关键决策

### D-i · R1 结构化剪枝

`set_sandbox_mode(True)` 会把 ccxt 的整套 test URL（含 `testnet.binancefuture.com` 等
期货域名）拷入 `urls['api']`。构造时**剪枝**：遍历 `urls['api']`，删除 host 不过
`net.check_url` 的条目——client 结构上无法触达白名单外域名（误用直接 KeyError，而非发包）。
剪枝后必须仍含 spot `public`/`private` 两个入口，否则视为 ccxt 版本不兼容，构造即失败。

### D-j · 错误映射（ccxt → 环境契约）

| ccxt 异常 | 映射 |
|---|---|
| `NetworkError`（含超时/DNS/断连） | `TestnetUnavailableError`——**非模型过错**：不是 `ExchangeError`，registry 兜底记 INTERNAL_ERROR（error_kind=None，不进 schema/semantic 指标）；消息必须含「改用 --env mock」提示（AC-11d） |
| `BadSymbol` | `ExchangeError("INVALID_SYMBOL")` |
| `InsufficientFunds` | `ExchangeError("INSUFFICIENT_BALANCE")` |
| `OrderNotFound` | `ExchangeError("UNKNOWN_ORDER")` |
| `InvalidOrder` | `ExchangeError("INVALID_ORDER")` |
| 其余 `ExchangeError` | `ExchangeError("EXCHANGE_ERROR", 原文)` |

捕获顺序 NetworkError 先于 ExchangeError（ccxt 继承关系）。

### D-k · testnet 结构评分模式

`assay run --env testnet`：任务筛选改为 `env ∈ {testnet, both}`；**不跑任务断言**（fixture 期望值
对实时行情无意义），`record.scoring = {"mode": "structural", "passed": status=="done" ∧
schema_errors==0, "assertions": [], "stats": {tool_calls, steps, schema_errors,
semantic_errors}, "judge": null, "judge_model": null, "judge_error": null}`。
mock 路径的评分输出**逐字节不变**（R4 不受扰动；mock scoring 不加 mode 字段）。
CLI 起跑前先 `ping()`（fetch_time）：网络不可达 → 明确报错并提示 `--env mock`，exit 2。

### D-l · 能力裁剪（v0.1）

- `withdraw`：**永不发真实请求**（testnet 无真实提币；AC-11c）——不触碰 ccxt client，
  直接返回 `WithdrawReceipt(simulated=True, transfer_id="SIM-<n>")`；
- `place_order` 仅支持 market / limit；`stop_limit` → `ExchangeError("UNSUPPORTED")`
  （抽样清单避开 A05/A12）；market 的 `quote_qty` 经 binance `quoteOrderQty` 参数；
- `get_transfer_history` → `ExchangeError("UNSUPPORTED")`（spot testnet 无 sapi 充提接口，
  如实报错不装作空账单）；`get_my_trades` 需给 symbol（binance 约束），缺省 →
  `ExchangeError("INVALID_SYMBOL", "testnet 查询成交需要 symbol")`；
- `export_state()`：balances / open_orders 实取（结构合法），`new_trades`/`new_transfers`
  恒 `[]`——终态增量对账只在 mock 有意义，结构模式不运行相关断言。

### D-m · key 纪律（R2）

仅 `secrets.get_secret("OH_TESTNET_API_KEY"/"OH_TESTNET_API_SECRET")`。任一缺失 →
`TestnetConfigError`，消息**同时点名两个环境变量名**（AC-11b），绝不读文件/配置/参数。

## 3. 模块契约（env/testnet.py）

```python
class TestnetConfigError(RuntimeError): ...      # key 缺失（AC-11b）
class TestnetUnavailableError(RuntimeError): ... # 网络不可达（AC-11d）

class TestnetExchangeEnv(ExchangeEnv):
    def __init__(self, client=None): ...  # client 注入仅供测试；缺省走 ccxt + 剪枝
    def ping(self) -> None: ...           # fetch_time；失败 raise TestnetUnavailableError
```

- ccxt 只允许本文件 import（`_CCXT_ALLOWLIST`，既有守护）；
- markets 懒加载（首次需要 symbol 映射时 `load_markets()`）——构造不发包，守护测试可离线跑；
- 一切对外数值 `Decimal(str(x))`（R9）；ccxt 缺失字段按 `0` 兜底（结构模式不比对金额）。

## 4. `env: both` 抽样清单（AC-11e 定稿）

A01 / A02 / A03 / A06 / A11 / B01 / B03 / B07 —— 共 8 条。
入选标准：不依赖 fixture 特定历史与预置挂单；不故意触发精度/名义额陷阱（A07/A08 排除）；
不用 stop_limit（A05/A12 排除）；不查充提历史（B04/B10 排除）。
冒烟口径（integration，缺 key/网络时 skip）：每条任务用测试内联的结构化脚本跑
`run_episode`（真实 testnet 调用），断言 `schema_errors == 0 ∧ status == "done"`；
下过的限价单一律撤掉（`cancel_order` 清理，行情价 50% 深度外挂单不会成交）。

## 5. 测试映射

| AC | 测试 | 要点 |
|----|------|------|
| AC-11a | `test_redlines.py::test_r1_testnet_client_uses_whitelisted_base` | dummy key 构造真实 ccxt client（离线），剪枝后 `urls['api']` 全部过 `check_url` 且含 spot public/private |
| AC-11b | `test_testnet.py::test_keys_only_from_env` | 双缺/单缺 → `TestnetConfigError`，消息含两个 env 变量名 |
| AC-11c | `test_testnet.py::test_withdraw_simulated` | client 换成「任何属性访问即炸」的哨兵仍能提币成功，`simulated=True` |
| AC-11d | `test_testnet.py::test_network_failure_graceful_degradation` | stub client 抛 `ccxt.NetworkError` → `TestnetUnavailableError`，消息含 mock 提示 |
| 补充 | `test_testnet.py::test_ccxt_error_mapping` / `::test_structural_scoring_mode` | D-j 映射表逐条；D-k 评分形态与 mock 不受扰 |
| AC-11e | `test_testnet_smoke.py::test_sampled_tasks_structural` | §4 口径；`@pytest.mark.integration` |
