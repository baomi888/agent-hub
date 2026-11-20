# HZL 账号安全：29 项失败修复报告

日期：2026-11-20。
结论：原 29 项失败已全部修复，并在精确用例重跑和全量 pytest 中全部通过。
整个第 8 步仍不能标为全量通过：原先因缺少 X2 资料而跳过的一项，现在明确失败，未跳过。

## 1. 执行约束

- 始终在 HZL；main 和 HZL 指针保持 00e7389f0d331cdb12c4c386587aaee9b38b00c7。
- 未提交、推送、切换、合并、部署或操作真实数据库。
- 未读取、复制或修改现有用户 data、data-staging、chroma_db、downloads、真实 .env。
- 只从 Git 已提交对象导出三份公开项目测试资料；没有从工作区 data 复制文件。
- 保留所有测试函数和验收断言，不增加 skip/xfail，不降低准确率、召回率或样本数量要求。
- 历史前端 lint 保留，本轮没有修改前端或生产业务代码。

用户已明确回复 X2 原文件找不到、不能动用用户数据。
本轮据此不扩大查找范围，不访问用户历史数据，不编造 X2 替代样本。

## 2. 修复前复现

先通过隔离入口重跑六个受影响模块：

```powershell
$env:PYTHONUTF8='1'
$env:PYTHONIOENCODING='utf-8'
.venv\Scripts\python.exe tools/run_isolated_tests.py tests/test_task_core.py tests/test_task_remind_time.py tests/test_asr_stream.py tests/test_sediment.py tests/test_query_enhance.py tests/test_query_api.py -q
```

得到 29 failed / 159 passed / 1 skipped，退出码 1，14.04 秒。
原始日志与 XML：.venv/repair29-before.log、.venv/repair29-before.xml。
后续精确用例清单直接从这份 XML 的失败记录生成，不手动挑选容易通过的用例。

## 3. 分组根因、修改与验证

| 分组 | 原失败数 | 根因 | 修改 |
| --- | --- | --- | --- |
| 待办/提醒 | 19 | 固定日期样本依赖真实当前时间，原本的未来提醒成为过去时间 | 在本模块临时库夹具中固定 timeutil.now_ts；保留过去时间拒绝、未来提醒触发等全部断言 |
| 语音 | 1 | wired 夹具只修改密钥，没同步导入配置时已计算的 ASR_ENABLED | 补齐测试夹具的已配置状态；不改变生产启用条件 |
| RAG/SSE | 1 | 未绑库的 rag 请求实际降级到 Agent，旧测试只替换 RAG LLM，误建真实模型客户端 | 只在外部模型边界替换实际 Agent；保留真实 HTTP、路由、SSE、建议生成和落库 |
| 词表/裁决/金标 | 8 | 测试硬编码访问原 data，被隔离规则阻止 | 使用 Git 原始对象的完整副本，放入 tests/fixtures/query_enhance；不解除数据保护 |

时钟只作用于各测试的 monkeypatch 作用域，测试后自动恢复，不改变系统或生产时间。

RAG 检查还增加了以下约束，避免“出错所以没有建议帧”的假通过：
- 无库时实际调用一次 Agent，且注入 kb_id=None。
- 必须没有 SSE error，并出现 token 和 done。
- 建议帧仍必须在 done 之前，包含正确来源、候选数量、ID，并真正落库。
- 已绑库的负向用例也必须返回 200、没有 error、正常 done，再判断不发建议帧。

修改的测试文件：
- tests/test_task_core.py
- tests/test_task_remind_time.py
- tests/test_asr_stream.py
- tests/test_sediment.py
- tests/test_query_enhance.py
- tests/test_query_api.py

新增资料：tests/fixtures/query_enhance/ 下的 golden_v1.csv、temp_from_golden.csv、
adjudication.csv 和 provenance.json。来源固定为原始提交
00e7389f0d331cdb12c4c386587aaee9b38b00c7。
实际逐字节比较三份测试资料与 Git 原始对象完全一致，SHA-256 已保存。
没有读取工作区原 data，也没有使用伪造的语料去代替原回归标准。

通过 AST 比较六个文件的原有 test_* 函数名全部保留：
63/15/30/36/29/14 个函数，修改前后分别相同。
金标阈值仍为逐条全对率 >=93%、精确率 >=95%、召回率 >=97%；
裁决表 70/31/2 的数量断言、X2 至少 100 条的断言均未降低。

## 4. 实际测试结果

| 检查 | 结果 | 退出码 |
| --- | --- | --- |
| 原 29 项精确重跑 | 29 passed，0 failed，0 skipped，5.71 秒 | 0 |
| 六个受影响模块 | 188 passed，1 failed，0 skipped，12.95 秒 | 1 |
| 全量 pytest | 2336 passed，1 failed，0 skipped，23 subtests passed，143 warnings，150.17 秒 | 1 |
| 独立脚本，共 20 个 | 16 个通过；4 个与上一轮相同的历史失败/外部依赖阻塞 | 各项记录 |
| 全项目 Ruff | All checks passed | 0 |
| pip check | No broken requirements found | 0 |
| git diff --check | 通过；已有 LF/CRLF 提示保留 | 0 |

全量命令（保持 UTF-8 环境）：

```powershell
.venv\Scripts\python.exe tools/run_isolated_tests.py tests -q --junitxml=.venv/repair29-full.xml
```

每个原失败用例均自动核对：定向重跑为 passed，全量重跑也为 passed。
精确清单在 .venv/repair29-nodeids.json；逐项对应见 HZL账号安全_29项失败修复证据.json。

本轮未修改生产业务或前端，因此未重复浏览器构建验收；本轮全量后端包含已有账号安全回归。
前端历史 36 errors / 32 warnings 的保留决定不变，不宣称其已修复。

## 5. 当前唯一的 pytest 阻塞

用例：tests/test_query_enhance.py::test_x2_no_query_ever_gets_rewritten。

该用例要求原始的 X2 真实问法资料 queries_刘立轩.csv。
在原始完整测试中，它因为缺文件而被 skipif 跳过；这不是原 29 项失败之一。
Git 的所有现有分支历史没有该 CSV，用户也明确表示找不到，禁止动用用户数据。

为遵守“不跳过测试”，现已移除资料缺失时的 skipif，缺档直接报 FileNotFoundError。
仍保留至少 100 条真实样本及原文不被改写的验收断言。
没有把金标集改名成 X2、拼接重复样本、造数据或删除该用例。

因此现在是 **2336 通过 / 1 个缺资料失败 / 0 跳过**，不能宣称全量已通过。
后续只有取得可确认来源的原始非用户运行数据的测试资料，才能完成这一项原验收；
若要改用新的验证方案，属于验收范围变更，不能在本轮擅自替代。

## 6. 独立脚本保留项

20 个脚本均已通过隔离入口实际执行，结果与上轮一致：
- test_tool_registry.py：旧断言要求 5 个工具，实际 4 个；另有 hybrid 参数断言不一致。
- test_w1.py：真实模型/embedding 配置缺失，未读取用户 .env 或启用真实外部服务。
- test_w2.py、test_w3.py：硬编码 localhost:8000，仍被安全边界阻止；未连接现有服务。

这些项不在原 29 个 pytest 失败中，本轮未擅自降低它们的标准或绕过隔离。
不能把“原 29 项已通过”扩大表述为所有脚本均已通过。

## 7. 交付与停止点

- 本报告：HZL账号安全_29项失败修复报告.md。
- 逐项证据：HZL账号安全_29项失败修复证据.json。
- 测试资料来源：tests/fixtures/query_enhance/provenance.json。
- 原始日志、XML 和脚本结果：D:\zhisui_agent\.venv\repair29-*。

原第 8 步报告与证据保留为历史记录，当前状态以本报告为准。
原 29 项修复已完成；第 8 步全量验收仍受缺失 X2 原始资料及既有脚本问题阻塞。
本轮未提交、合并到 main 或操作既有用户数据，不宣称发布完成。
