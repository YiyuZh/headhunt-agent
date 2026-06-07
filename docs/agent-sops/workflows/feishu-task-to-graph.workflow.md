# Feishu Task To Graph Workflow

Version: 0.1.0

```text
/feishu/events or /feishu/card-actions
-> Feishu signature + tenant/chat allowlist + idempotency
-> ACK/toast
-> TaskIntakeParser
-> CanonicalTaskBrief with field_source and confidence
-> Feishu double check card
-> approve: freeze version and enqueue graph_dispatch
-> edit: create new version and repeat double check
-> reject/expired: stop task
```

Graph dispatch receives only the frozen `CanonicalTaskBrief` and allowed refs.
