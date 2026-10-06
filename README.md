# 青少年志愿讲解成长服务

本项目是使用 Python、FastAPI 与 SQLite 实现的服务端应用，覆盖志愿者、培训考核、服务记录、积分权益、监护关系和统计。它可在单个 Linux 应用容器内完成安装、测试、编译和接口验收，不依赖浏览器、外部数据库、缓存、消息队列或额外运行服务。

## 安装

```bash
python3 -m pip install -r requirements.txt -r requirements-dev.txt
```

## 测试

```bash
python3 -m pytest -q
```

## 编译

```bash
python3 -m compileall -q .
```

## 接口验收

```bash
python3 -c "from main import app; assert len(app.routes) > 5; print(len(app.routes))"
```

## 启动

```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

## 监护关系可追溯授权

监护关系不再是孩子档案上的一条静态手机号，而是“版本化、可限定、可撤回、可追溯”的授权。

授权版本字段：孩子（volunteer）、监护人（guardian）、操作范围 `查询 / 报名确认 / 权益代领`、生效与截止时间、状态（有效/已撤回/已过期）。每次（重新）授权产生不可变新版本，撤回只置状态、保留旧版本；撤回后新请求立即失效，撤回前已完成的操作仍可凭业务行上冻结的 `authorization_id` 与审计快照解释依据。

- 监护人入口 `/api/guardians`：`/login`、`/children/{id}/view`（查询）、`/children/{id}/enroll-confirm`（报名确认）、`/children/{id}/benefit-claims`（权益代领，携带 `idempotency_key` 防重复提交）。
- 运营管理 `/api/guardianships`：`POST /authorizations`（授予/变更，产生新版本）、`POST /authorizations/{id}/revoke`（撤回）、`GET /authorizations`、`GET /audits?at=`（审计流水，可按时间点）、`GET /reconstruct/{volunteer_id}?at=`（按时间点还原当时有效授权与“谁凭哪一版授权看过/办过什么”）。
- 所有写操作在 `BEGIN IMMEDIATE` 串行事务内完成“授权校验 + 业务落库 + 审计”，撤回与代办并发时二者只取其一，不越权、不两次生效；旧版 `/api/parents` 入口同样改为依据当时有效的授权版本放行。

测试：`python -m pytest tests/test_guardianship.py -q`（含共同监护、监护人交接、重复提交幂等、撤回/代办真实并发与按时间点还原）。

