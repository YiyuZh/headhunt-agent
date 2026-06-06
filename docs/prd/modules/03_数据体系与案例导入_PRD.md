# 模块 03：数据体系与案例导入 PRD

## 1. 模块目标

建立可持续喂数据、可重复导入、可审计复盘的数据体系。系统必须同时支持：

```text
业务数据：真实工作里的岗位、人才地图、候选人、沟通、报告。
案例数据：用户提供的脱敏案例，用于演示、测试、迭代 prompt。
审计数据：每次 Agent 运行的输入、输出、人工确认、错误和写入结果。
```

## 2. 存储策略

```text
第一版主数据库：PostgreSQL + pgvector + seed files
团队协作工作台：Discord War Room
运行时 checkpoint：PostgreSQL checkpointer
向量记忆检索：PostgreSQL + pgvector
```

Discord 是第一版工作台，PostgreSQL 是系统状态、记忆、向量索引、审计、测试和可重复执行的底座。Feishu/Bitable 仅作为 deferred adapter。第一版主路径直接启用 pgvector；Qdrant / Milvus 只保留适配接口，不作为首版依赖。SQLite 只允许作为个人临时实验，不进入第一版验收主路径。

记忆相关表第一版必须进入主 schema：

```text
memory_items：长期记忆和 RunMemory 的元数据、摘要、状态、权限范围
memory_embeddings：pgvector embedding、embedding_model、embedding_dim、content_hash
memory_retrieval_audit：每次 MemoryGateway 检索的 query、filters、命中、裁剪原因和 token 估算
memory_proposals：长期记忆写入、撤销和审批流
```

## 3. 数据目录

```text
data/
  raw/
    requisitions/
    candidates/
    interactions/
    reports/
  seed/
    requisitions.csv
    talent_map.csv
    candidates.csv
    interactions.csv
    reports.csv
  examples/
    case_001_ai_engineer.md
    case_002_sales_leader.md
  anonymized/
    candidates_demo.csv
```

## 4. 案例 Markdown 模板

```markdown
# Case: AI Infra Engineer

## Requisition
- 客户/部门：
- 岗位名称：
- 城市/工作方式：
- 薪资范围：
- JD 原文：
- 客户补充要求：
- 已知目标公司：
- 明确不要的人：
- 岗位卖点：
- 当前状态：

## Talent Mapping Notes
- 目标公司：
- title 变体：
- 技能关键词：
- 搜索语句：
- 已验证渠道：

## Candidates
### CAND-001
- 当前公司：
- 当前 title：
- 年限：
- 简历/经历摘要：
- 技能：
- 沟通状态：
- 候选人反馈：
- 风险/疑问：

## Interactions
- CAND-001 / 日期 / 沟通摘要 / 下一步：

## Expected Output
- 人才地图 / 筛选结论 / 话术 / 推荐报告 / 跟进计划
```

## 5. CSV 字段

`requisitions.csv`：

```text
requisition_id,client,owner,title,city,work_mode,salary_range,jd_text,
business_context,must_have,nice_to_have,knockout_rules,selling_points,
target_company_hypothesis,status
```

`talent_map.csv`：

```text
map_item_id,requisition_id,target_company,company_type,target_title,
title_aliases,seniority,core_skills,channel,search_query,lead_status,
priority,notes
```

`candidates.csv`：

```text
candidate_id,requisition_id,alias_name,contact_ref,current_company,
current_title,years_experience,resume_text,skills,communication_status,
next_followup_at,notes
```

## 6. 导入流程

```text
读取 raw/ 或 seed/
-> 校验字段
-> 脱敏检查
-> 生成稳定 ID
-> 写入 PostgreSQL
-> 同步到 PostgreSQL 仓储；Feishu/Bitable 同步只在 deferred adapter 启用后执行
-> 写入 import_run 审计
```

规则：

- 案例数据默认 `source="case_data"`。
- 重复 ID 默认 update，不重复则 create。
- 简历原文可以先存 ArtifactStore，Discord/War Room 只展示摘要和附件引用。
- 同一个候选人可匹配多个岗位，用 `candidate_requisition_matches` 关联。
- 第一版导入主写入 PostgreSQL repositories。启用 Feishu/Bitable deferred adapter 时，必须走 `FeishuBitableGateway`，通过 `app_token` + `table_id` registry 定位表，不允许在导入脚本或业务节点硬编码。
- deferred 多维表格写入前必须校验编辑权限；缺权限时写入 `import_run` 失败原因，不得只写 PostgreSQL 后假装同步成功。
- deferred 批量新增/更新必须按飞书接口上限分片；同一业务表使用稳定业务 ID 做 upsert，避免重复创建记录。
- deferred 多维表格部分失败必须记录成功记录、失败记录、错误码和可重试状态。

## 7. 导入命令与 API

```text
python -m app.importers.load_case data/examples/case_001_ai_engineer.md
python -m app.importers.load_seed data/seed/requisitions.csv
python -m app.importers.load_seed data/seed/candidates.csv
python -m app.importers.sync_feishu --table Requisitions
```

```text
POST /data/import/case
POST /data/import/csv
POST /data/import/json
POST /data/sync/feishu
GET /data/import-runs/{run_id}
```

## 8. 验收标准

- Markdown 单案例可导入。
- CSV 批量数据可重复导入。
- 导入时进行脱敏检查。
- 导入结果写入 PostgreSQL 审计。
- RunMemory、CaseMemory 和经审批的长期记忆可写入 `memory_items` 并生成 pgvector embedding。
- 第一版导入结果主写 PostgreSQL；Feishu/Bitable 同步为 deferred adapter，测试中可使用 fake gateway 验证调用契约。
- case_data 不与 production_data 混淆。
