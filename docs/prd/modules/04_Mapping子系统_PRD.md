# 模块 04：Mapping 子系统 PRD

## 1. 模块目标

Mapping 子系统是 AI 猎头作战系统的核心引擎。它把岗位作战单转成：

```text
标准技能
title 变体
目标公司
搜索策略
候选人匹配证据
TalentMap 表行
```

## 2. 采用与预留组件

| 组件 | 用法 |
|---|---|
| Nesta Skills Extractor | 技能短语抽取，映射到 ESCO / Lightcast Open Skills |
| ESCO / ISCO | 预留职业和技能标准化 |
| CV-Matcher | 借鉴 JD/简历解析、关键词抽取、Qdrant 向量相似度 |
| TalentMatch | 借鉴 JD 分析、候选人 ranking、面试题生成 API |
| acenji/ats | 借鉴 soft match confidence、missing keywords、gap analysis |

## 3. 核心接口

```python
class SkillTaxonomyAdapter:
    def extract_and_map(self, text: str) -> list[dict]: ...

class TargetCompanyMapper:
    def build(self, requisition: dict) -> list[dict]: ...

class TitleAliasMapper:
    def expand(self, title: str, seniority: str | None, domain: str | None) -> list[str]: ...

class CandidateImportAdapter:
    def import_from_feishu_or_csv(self, source: dict) -> list[dict]: ...

class CandidateMatcher:
    def match(self, requisition: dict, candidate_profile: dict) -> dict: ...

class TalentMapWriter:
    def write_to_feishu(self, map_items: list[dict]) -> dict: ...
```

## 4. 技能归一

先做内置轻量 taxonomy，再接外部 taxonomy。

技能和关键词抽取以当前 JD / 简历 artifact 为主。长期记忆只能提供 taxonomy、项目规则、用户修正、历史 title 变体和 SOP，不得覆盖当前 JD / 简历中的事实。

```json
{
  "LLM": ["大模型", "Large Language Model", "GenAI", "生成式 AI"],
  "RAG": ["检索增强生成", "Retrieval Augmented Generation"],
  "Prompt Engineering": ["提示词工程", "Prompt 设计"],
  "Vector Database": ["向量数据库", "Milvus", "Qdrant", "Pinecone"],
  "Agent": ["AI Agent", "智能体", "Multi-Agent"]
}
```

## 5. 目标公司 Mapping

来源：

```text
客户指定
直接竞品
上下游公司
同业务模式公司
同技术栈公司
同岗位密集公司
候选人过往公司反推
```

每个目标公司必须包含：

```text
company
company_type
reason
likely_titles
core_skills
priority
source
source_refs
confidence
```

公司当前事实边界：

```text
融资、裁员、组织变化、当前业务线、当前招聘状态、候选人当前在职状态等高时效事实必须由 SearchGateway source_refs 支撑。
长期记忆可以提供历史目标公司经验和分类 taxonomy，但不得替代实时搜索。
无 source_refs 的公司事实只能进入 hypothesis 或 assumptions，不得作为已确认事实。
```

## 6. Title 变体

必须覆盖：

```text
中文 title
英文 title
职级变体
平台常见写法
业务别名
技术别名
```

示例：

```text
AI Infra Engineer
LLM Engineer
RAG Engineer
AI Platform Engineer
大模型应用工程师
AI 平台工程师
```

## 7. 候选人匹配

结合三类信号：

```text
规则匹配：硬条件、年限、行业、地点、语言、职级
taxonomy 匹配：技能同义词、标准技能、技能簇
semantic soft match：语义相似、项目经验相似、职责相似
```

输出：

```json
{
  "score": 82,
  "decision": "待复核",
  "evidence": [
    {
      "requirement": "RAG 项目经验",
      "candidate_evidence": "简历第 3 段提到基于向量数据库搭建企业知识库",
      "match_type": "semantic",
      "confidence": 0.86
    }
  ],
  "gaps": ["没有明确写明线上用户规模"],
  "risks": ["最近一段经历起止时间不完整"],
  "questions_to_ask": ["RAG 项目的数据规模和上线效果是什么？"]
}
```

## 8. 验收标准

- JD 能抽取技能并归一。
- `LLM / 大模型 / GenAI` 能映射到同一技能。
- title 能生成中英文和职级变体。
- TalentMap 输出有目标公司、理由、搜索语句和优先级。
- 候选人匹配必须有 evidence、gaps、risks、questions_to_ask。
- 无证据不得输出“推荐”。
