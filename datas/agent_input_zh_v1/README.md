# Agent 输入侧中文精选测试集 v1

生成日期：2026-11-02。数据文件是 [`records.jsonl`](records.jsonl)，共 **685 条**，来自 9 个公开仓库，已统一为 UTF-8 JSONL。简体中文 511 条、繁体中文 140 条、英文 34 条；中文合计 651 条（95.0%）。其中 193 条为逐条筛选的 `curated_core`，492 条为规则筛选并抽查的 `screened_extension`。保留原文、来源版本、原始标签和适用的计分方式。原始大文件只用于抽样，不保存在 `datas` 中。

## 组成

| 评测轨道 `track` | 条数 | 来源 | 用法 |
| --- | ---: | --- | --- |
| `content_safety` | 188 | CHILLGuardTest 48、PATCH 140 | 分来源测安全/不安全二分类。PATCH 为繁体。 |
| `broad_harmful_input` | 80 | ChangeMore | 分语言测广义有害输入二分类；不等同于提示注入检测。 |
| `external_content_safety` | 270 | ChineseHarm-Bench | 测外部短文本的六类内容风险及“无违规”误报。 |
| `pii_span` | 105 | pii-bench-zh 100、OpenPII 5 | 在含 PII 文本上测实体跨度 precision/recall/F1；缺少整篇无 PII 负例，不能测文档级误报率。 |
| `indirect_prompt_injection` | 30 | InjectAgent-CN 15 组攻击/良性网页配对 | 测工具返回文本中的间接注入。负例由原网页模板填入正常页面内容构成。 |
| `behavior_challenge` | 12 | Safety-Prompts 6、Flames 6 | 保留挑战提示；`label=null`，不参加自动二分类总分。 |

## 统一字段

每行都有 `id`、`text`、`language`、`source_type`、`track`、`label`、`metric_scope`、`category`、`entities`、`context`、`label_quality`、`cohort` 和 `source`。

- `label`：仅在当前 `track` 内解释，`1` 是该轨道的风险/实体存在，`0` 是良性，`null` 表示没有可用于输入二分类的金标。**不要跨轨道直接合并标签计算总准确率。**
- `metric_scope`：`binary` 可算 precision、recall、FPR；`binary_paired` 还应按 `context.pair_id` 对照；`span_positive_only` 在含 PII 文本上比较实体位置；`qualitative` 不进入自动计分。
- `source_type`：`user_message`、`external_text`、`tool_result` 或 `text_unspecified`。`text_unspecified` 不强行假设原文来自用户还是工具。
- `entities`：PII 的 `{start,end,text,type}`，采用 Python 字符索引的半开区间 `[start,end)`；其他轨道为空数组。
- `context`：Agent 网页案例保留用户任务、工具名、URL、攻击目标工具与配对 ID；其他轨道为 `null`。
- `source`：含仓库名、固定 revision、原始文件 URL、原始行 ID/split/标签和许可证。`label_quality` 区分发布方标签、子集级标签、场景派生配对与仅有挑战类别的记录。
- `cohort`：`curated_core` 为原有精选样本中许可清楚的 193 条；`screened_extension` 为新增 492 条。建议分别报告两组指标，尤其不要把扩充组的发布方标签视作独立人工金标。

## 选样与边界

1. 来源文件先拉到临时目录，再统一字段；生成脚本为 [`../build_agent_input_zh_v1.py`](../build_agent_input_zh_v1.py)。核心组使用固定原始 ID，逐条检查可读性和标签显著冲突；扩充组按语言、长度、类别、实体跨度及文本指纹等规则筛选，再抽查高噪声来源并排除发现的明显误标。**没有对全部 685 条重新做人类专家金标**。保留发布方标签便于追溯。
2. CHILLGuardTest 的标签由模型投票/裁决形成；ChangeMore 的 `label=1` 是广义“有害”而非专门的提示注入；PATCH 只取可读的 `safe` 和 `unsafe_gpt` 测试样本，未纳入噪声较多的 `unsafe_rt`；ChineseHarm-Bench、pii-bench-zh 原页面只有 `train`，本集是固定抽样，不能假装原仓库已有独立测试切分。
3. Safety-Prompts、Flames 主要评模型行为，不能从所属类别推断每一条提示都应被输入护栏拦截，因此 `label=null`。先前本地版本中的 4 条 CSEI-SafetyBench 样本未纳入公开发布版：其页面许可为 `other`，`LICENSE` 文件为空，无法确认再分发许可。可从[原仓库](https://huggingface.co/datasets/BAAI/CSEI-SafetyBench)单独获取并按发布方条件使用。
4. InjectAgent-CN 的 15 条攻击 HTML 来自原场景模板；15 条良性配对使用同一模板和本项目写入的页面正常内容。它们适合检测器离线测试；完整 Agent 是否执行了攻击动作，需要另跑原仓库的端到端框架。
5. OpenPII 只收录 5 条验证集多语言噪声样本，不代表自然中文；PII 主集是 pii-bench-zh 的 100 条合成样本。两个来源的实体类型体系不同，应分别汇报跨度指标。
6. ChineseHarm-Bench 的“无违规”样本是从原生良性类别抽出的短文本，与风险类别的分布不同；其 FPR 只代表这个来源上的结果。扩充组仍可能有边界标签和模板相似性，适合能力画像与回归，不应作为唯一的生产验收标准。不同来源的风险政策、文本分布及许可不同。

## 许可证与来源

逐条许可证和固定版本见 [`manifest.json`](manifest.json) 与每条记录的 `source`。本集合包含 CC BY-NC 4.0、CC BY-NC-SA、CC BY 4.0、Apache-2.0、MIT 等来源；PATCH 页面同时注明“research use only”。各来源的使用条件仍分别适用；整包不声明新的统一开源许可，也不作为商用数据发布。

主要来源：[CHILLGuardTest](https://huggingface.co/datasets/cswbyu/CHILLGuardTest)、[ChangeMore](https://huggingface.co/datasets/CTCT-CT2/ChangeMore-prompt-injection-eval)、[PATCH](https://huggingface.co/datasets/Raymond102103028/PATCH)、[ChineseHarm-Bench](https://huggingface.co/datasets/zjunlp/ChineseHarm-bench)、[pii-bench-zh](https://huggingface.co/datasets/wan9yu/pii-bench-zh)、[OpenPII](https://huggingface.co/datasets/ai4privacy/pii-masking-openpii-1.5m)、[Safety-Prompts](https://github.com/thu-coai/Safety-Prompts)、[Flames](https://github.com/AI45Lab/Flames)、[InjectAgent-CN](https://github.com/Binye234/InjectAgent-CN)。

## 使用

```python
import json

with open("datas/agent_input_zh_v1/records.jsonl", encoding="utf-8") as f:
    records = [json.loads(line) for line in f]

# 例如：只评估 Agent 工具返回中的间接注入
cases = [r for r in records if r["track"] == "indirect_prompt_injection"]
for case in cases:
    print(case["id"], case["context"]["pair_id"], case["label"])
```

重新构建（需联网，源文件约数十 MB；下载缓存被 `.gitignore` 排除）：

```bash
python3 datas/build_agent_input_zh_v1.py --download
```

`manifest.json` 记录原始文件和输出 JSONL 的 SHA-256；重建时可对照验证来源未变。
