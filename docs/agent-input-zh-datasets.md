# Agent 输入侧中文安全测试数据集

核对日期：2026-11-02。这里的“输入侧”包括用户消息，以及 Agent 读入的网页、文档、工具返回文本；PII 检测也可在进入模型前执行。以下按**数据原生标签**整理，不把“有害请求”“越狱”和“提示注入”混成同一类。样本数是发布方当前页面所列规模，实际使用时应锁定版本与抽样种子。

已从下列 9 个许可清楚的来源拉取并统一格式的本地精选测试集见 [`datas/agent_input_zh_v1/`](../datas/agent_input_zh_v1/README.md)：685 条，其中简体中文 511 条、繁体中文 140 条、英文 34 条。193 条为逐条筛选的核心组，492 条为规则筛选并抽查的扩充组；原始标签与许可证逐条保留。CSEI-SafetyBench 的许可不明确，未纳入拟公开的数据文件。

## 一、可直接做中文离线检测评测

| 数据集 | 语言与规模 | 原生标签 / 字段 | 适合评什么 | 限制与许可 |
| --- | --- | --- | --- | --- |
| [CHILLGuardTest](https://huggingface.co/datasets/cswbyu/CHILLGuardTest) | 简体中文，51,745 条；26,691 safe、25,054 unsafe | `prompt`、`safety`、31 类 `category`；单一 `test` split | 用户输入有害性二分类、细类召回、良性误报；含隐晦表达、谐音等中文变体 | 标签经多模型投票和裁决，并非全量人工金标；其政策边界须与本项目对齐。CC BY-NC 4.0。 |
| [ChangeMore-prompt-injection-eval](https://huggingface.co/datasets/CTCT-CT2/ChangeMore-prompt-injection-eval) | 中英混合，10,728 条；仅发布 `test` | `query`、`label`、`source`、`metainfo`；1=有害、0=良性 | 筛出中文后测广义对抗/有害输入和正常请求误报 | **不能直接以 `label=1` 当作“提示注入”金标**：正例还包括普通有害请求和越狱。没有公布中文子集准确规模；筛选后要复核语种和风险类。CC BY-NC-SA。 |
| [PATCH](https://huggingface.co/datasets/Raymond102103028/PATCH) | **繁体中文**，593,020 safe + 231,924 unsafe | `safe`、`unsafe_gpt`、`unsafe_rt` 配置，均有 train/validation/test；文本字段 `prompt` | 繁体输入安全性、规避式表达鲁棒性、误报 | 只取各配置的 `test`；不得把训练集混入评测。数据页标 MIT，同时卡片写有“research use only”，再分发或商用前核对。 |
| [ChineseHarm-Bench](https://huggingface.co/datasets/zjunlp/ChineseHarm-bench) / [代码](https://github.com/zjunlp/ChineseHarm-bench) | 中文，15,515 条 | `文本`、`标签`；赌博、色情、辱骂、诈骗、黑产广告、无违规六类 | 不可信工具文本、评论、网页片段的内容风险分类 | 页面仅有 `train`，若作测试需自行固定切分并核重。源文本多为社交平台短文本，不等于用户的操作指令。CC BY-NC 4.0。 |
| [pii-bench-zh](https://huggingface.co/datasets/wan9yu/pii-bench-zh) | 中文，8,000 条：正式 5,000、聊天 3,000；23,206 个 PII 实体 | `text`、`entities`，实体含 `start`/`end`/`type` | 输入侧 PII 实体跨度识别，尤其大陆号码、证件、地址及聊天噪声 | 全部为模板合成，页面只有 `train` split；若作为最终测试，先锁定并与模型训练数据核重。Apache-2.0。 |
| [OpenPII 1.5M](https://huggingface.co/datasets/ai4privacy/pii-masking-openpii-1.5m) | 30 种语言，约 163.6 万条；含中文 | `source_text`、`privacy_mask`、`language`、`region` 等 | 过滤 `language=zh`（必要时再按 `region=CN`）补充多格式 PII 边界样本 | 合成多语言数据，中文可能有混写或不自然格式；规模巨大，不必全量下载。CC BY 4.0。 |

**优先组合**：以 CHILLGuardTest 测简体中文有害输入；用 ChangeMore 的**经复核中文子集**补充对抗输入；用 ChineseHarm-Bench 测非指令式外部文本；用 pii-bench-zh 测 PII 跨度。若服务覆盖繁体，再加入 PATCH `test`。不要把这些来源的标签直接拼成同一个二分类总分。

## 二、中文攻击提示与 Agent 场景补充集

| 数据集 | 语言与规模 | 用法 | 为什么不能直接计算输入分类准确率 |
| --- | --- | --- | --- |
| [Safety-Prompts](https://github.com/thu-coai/safety-prompts) | 中文，约 10 万条 prompt-response；其中常规安全场景约 7 万、指令攻击约 3 万 | 按 `type` 取 Goal Hijacking、Prompt Leaking、Role Play 等攻击提示，做分类型召回 | 不是统一的“安全/不安全”输入金标；有生成数据噪声，缺少相同分布的良性负例。Apache-2.0。 |
| [CSEI-SafetyBench](https://huggingface.co/datasets/BAAI/CSEI-SafetyBench) | 中文，600 explicit + 400 implicit 不安全提示 | 测显式与隐式有害请求的召回、模型行为 | 仅 `prompt` 字段，没有良性负例，不能算误报率。页面许可标为 `other`。 |
| [Flames / Flames-1k-Chinese](https://github.com/AI45Lab/Flames) | 中文，1,000 条手工对抗提示 | 补充社会价值、安全、道德、法律、数据保护维度的压力测试 | 主要评模型回复/拒答；不是逐条二分类检测标注。Apache-2.0。 |
| [InjectAgent-CN](https://github.com/Binye234/InjectAgent-CN) | 中文网页浏览 Agent；15 场景 × 30 攻击工具 = 450 个测试用例 | 保留原始用户任务、网页注入点与工具约束，测间接注入和端到端攻击成功率 | 框架生成场景并评 Agent 行为，不能把 450 条直接当独立、人工标注的输入文本正负样本。项目较新，结果需自行复测。MIT。 |

**特别注意**：`prompt injection` 指低信任内容试图越过指令边界；单纯请求有害内容、角色扮演或越狱不必然是 Agent 工具结果注入。ChangeMore 的数据名包含 prompt injection，但其二分类标签是 broad harmful/benign。评估 `source_type=message` 和 `source_type=tool_result` 时应分开报告。

## 三、映射到本仓库的输入检测目标

| 目标 | 推荐来源 | 可报告指标 | 仍缺的材料 |
| --- | --- | --- | --- |
| `harmful_content` / 有害请求 | CHILLGuardTest；ChangeMore 中文子集；CSEI、Flames 和 Safety-Prompts 作正例压力集 | 前两者按复核后的映射报告 precision、recall、FPR；正例集只报 recall | 真实业务中的良性相近请求、政策边界样本 |
| `prompt_injection` | Safety-Prompts 中经复核的目标劫持、Prompt 泄漏等直接指令覆盖样本；InjectAgent-CN 的网页注入场景 | 分别报直接覆盖攻击召回、网页注入场景的端到端 ASR 和正常任务完成率 | 有人工核验的**中文工具结果注入正负配对**。Safety-Prompts 其他指令攻击类别未必属于 PI；现列来源均不足以单独给出可靠的 PI precision/FPR |
| `pii` | pii-bench-zh；OpenPII 的中文子集 | 实体跨度 precision、recall、F1；按 PII 类型分组 | 无 PII 的相似数字、示例占位符、脱敏文本等硬负例 |
| `dangerous_content` / 危险工具参数 | 现有项目夹具可做功能冒烟 | 类别召回与误报，按 `role=tool`、工具名、参数风险拆分 | 公开中文 Agent 工具参数金标；需另建独立的本地评测集 |
| `data_exfiltration` / 密钥外传 | InjectAgent-CN 场景、项目自造假凭据 canary | 攻击阻断率、误报率、端到端外传成功率 | 公开中文凭据外传正负集；只能使用**假** Token/密钥作本地夹具 |

## 四、现成的仓库内中文冒烟样本

[`tools/fixtures/safety_eval/input/`](../tools/fixtures/safety_eval/input/) 已有 1,000 条输入样本，其中 `language=zh` **550 条**，覆盖五个类别：`prompt_injection`、`data_exfiltration`、`pii`、`dangerous_content`、`harmful_content`。每类中文正例 55 条、负例 55 条。字段含 `id`、`phase`、`language`、`category`、`label`、`text`、`source`、`license`，来源为项目确定性合成，许可见 [`license.json`](../tools/fixtures/safety_eval/license.json)。

这些样本适合接线、冒烟和回归，**不适合作为当前模型的独立最终成绩**：文本是模板变体，还带“评估样本”编号；部分原始样本出现在 [`safety_distill/distilled_v0/train.jsonl`](../tools/fixtures/safety_distill/distilled_v0/train.jsonl)。要测泛化，应把上述公开集的固定测试切分和新标注的真实中文误报/漏报样本作为独立留出集，并做文本及近重复去重。

## 五、落地建议与记录格式

1. 固定每个来源的仓库 revision、原始 split、抽样种子和下载日期。保留原始许可证；不同许可的数据不要不加区分地重新发布成一个文件。
2. 统一评测记录可采用 `id`、`text`、`language`（`zh-Hans`/`zh-Hant`）、`source_type`（`message`/`tool_result`/`tool_args`）、`risk_type`、`label`、`source_dataset`、`source_id`、`split`、`license`、`annotation_status`。PII 另存 `entities`；Agent 场景另存任务和目标动作。
3. CHILLGuardTest、PATCH 用各自原生安全标签；ChangeMore 的 `label` 仅映射成广义有害输入，若要用于 PI 需人工重标。ChineseHarm-Bench 先保留六类，再依本地策略映射风险/良性。
4. 报告**各来源和各风险类别**的指标，不用跨来源微平均掩盖类别失衡。只有正例的集合只给召回；PII 用跨度指标；Agent 端到端用 ASR 与正常任务成功率。
