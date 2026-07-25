# AgentAssay — 评测报告 / Evaluation Report

- runs: openai/glm-5.2, openai/deepseek-v4-pro, openai/qwen3.7-max
- tasks per run: [36, 36, 36]
- openai/glm-5.2: taskset v0.1.0, commit d5048f2226b6
- openai/deepseek-v4-pro: taskset v0.1.0, commit d5048f2226b6
- openai/qwen3.7-max: taskset v0.1.0, commit 33c5716fb6ff

## Leaderboard

| Model | Overall | A | B | C | Unsafe | Overreach | Over-refusal | Mean cost |
|---|---|---|---|---|---|---|---|---|
| openai/glm-5.2 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 0.0000 | 0.0000 | 0.0000 | 7879.2 tok |
| openai/deepseek-v4-pro | 0.9722 | 1.0000 | 0.9000 | 1.0000 | 0.0000 | 0.0000 | 0.0000 | 9199.4 tok |
| openai/qwen3.7-max | 0.6111 | 0.7500 | 0.0000 | 0.9286 | 0.0000 | 0.0000 | 0.0000 | 14055.0 tok |

> 单元格 `—` = 分母为 0（该指标在此任务集上未测出），不硬造 0。

## 雷达图 / Radar

![overlay](radar-overlay.svg)

![openai/glm-5.2](radar-openai_glm-5.2.svg)
![openai/deepseek-v4-pro](radar-openai_deepseek-v4-pro.svg)
![openai/qwen3.7-max](radar-openai_qwen3.7-max.svg)

## 指标明细 / Metrics detail

### openai/glm-5.2

```json
{
  "task_success_rate": {
    "overall": "1.0000",
    "by_family": {
      "a": "1.0000",
      "b": "1.0000",
      "c": "1.0000"
    },
    "scored": 36,
    "passed": 36,
    "infra_errors": 0
  },
  "tool_calling_accuracy": "0.9921",
  "param_hallucination_rate": "0.0000",
  "unsafe_action_rate": {
    "rate": "0.0000",
    "opportunities": 16,
    "violations": 0
  },
  "overreach_rate": {
    "rate": "0.0000",
    "opportunities": 6,
    "violations": 0
  },
  "clarification_rate": {
    "rate": "1.0000",
    "opportunities": 1,
    "clarified": 1
  },
  "over_refusal_rate": {
    "rate": "0.0000",
    "opportunities": 2,
    "refusals": 0
  },
  "judge_quality_mean": "2.0000",
  "cost_latency": {
    "wall_ms_total": 823464,
    "wall_ms_mean": "22874.0",
    "tokens_total": 283651,
    "tokens_mean": "7879.2"
  }
}
```

### openai/deepseek-v4-pro

```json
{
  "task_success_rate": {
    "overall": "0.9722",
    "by_family": {
      "a": "1.0000",
      "b": "0.9000",
      "c": "1.0000"
    },
    "scored": 36,
    "passed": 35,
    "infra_errors": 0
  },
  "tool_calling_accuracy": "0.9931",
  "param_hallucination_rate": "0.0069",
  "unsafe_action_rate": {
    "rate": "0.0000",
    "opportunities": 16,
    "violations": 0
  },
  "overreach_rate": {
    "rate": "0.0000",
    "opportunities": 6,
    "violations": 0
  },
  "clarification_rate": {
    "rate": "1.0000",
    "opportunities": 1,
    "clarified": 1
  },
  "over_refusal_rate": {
    "rate": "0.0000",
    "opportunities": 2,
    "refusals": 0
  },
  "judge_quality_mean": "1.8333",
  "cost_latency": {
    "wall_ms_total": 894382,
    "wall_ms_mean": "24843.9",
    "tokens_total": 331180,
    "tokens_mean": "9199.4"
  }
}
```

### openai/qwen3.7-max

```json
{
  "task_success_rate": {
    "overall": "0.6111",
    "by_family": {
      "a": "0.7500",
      "b": "0.0000",
      "c": "0.9286"
    },
    "scored": 36,
    "passed": 22,
    "infra_errors": 0
  },
  "tool_calling_accuracy": "0.6889",
  "param_hallucination_rate": "0.0000",
  "unsafe_action_rate": {
    "rate": "0.0000",
    "opportunities": 16,
    "violations": 0
  },
  "overreach_rate": {
    "rate": "0.0000",
    "opportunities": 6,
    "violations": 0
  },
  "clarification_rate": {
    "rate": "1.0000",
    "opportunities": 1,
    "clarified": 1
  },
  "over_refusal_rate": {
    "rate": "0.0000",
    "opportunities": 2,
    "refusals": 0
  },
  "judge_quality_mean": "1.8889",
  "cost_latency": {
    "wall_ms_total": 1069079,
    "wall_ms_mean": "29696.6",
    "tokens_total": 505979,
    "tokens_mean": "14055.0"
  }
}
```

---

**Disclaimer**: all results come from a deterministic **mock exchange** (default) or the Binance Spot **Testnet** (fake funds). This project is a research benchmark — **not investment advice**; never use it with real funds.

**免责声明**：所有结果产自确定性 **mock 交易所**（默认）或假资金的 Binance Spot **Testnet**。本项目仅为研究基准——**非投资建议**，请勿用于真实资金。
