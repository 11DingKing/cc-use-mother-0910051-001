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

## 监护授权版本化（可追溯授权）

监护关系不再是一条静态手机号绑定，而是只追加、不改写的授权版本链。

**核心规则**

- 授权可限定孩子、操作范围（`view` 查询 / `enroll` 报名确认 / `benefit` 权益代领）与有效期；到期自动失效。
- 监护人发起的查询、报名确认、权益代领，均依据**当时有效的授权版本**执行，并在审计流水中固化版本号、范围、有效期快照。
- 撤回后新请求立即失效（登录也不再出现该孩子）；撤回前已完成的操作仍挂在原版本上，可解释依据。
- 共同监护：对同一孩子发起多条独立授权链，撤回其中一方不影响另一方。
- 监护人交接：在同一事务内撤回原授权并向新监护人授权，原子完成。
- 重复提交：客户端可传 `idempotency_key`（数据库唯一约束兜底）；无键时报名也按业务唯一性去重，不两次生效。
- 撤回与代办并发：所有写操作在 SQLite `BEGIN IMMEDIATE` 串行写事务中执行并带锁冲突重试，二者只有一方生效，无越权、无重复。

**接口**

| 用途 | 接口 |
| --- | --- |
| 监护人登录（仅列有效授权的孩子） | `POST /api/guardian/login` |
| 凭授权查看孩子全景/服务/培训 | `GET /api/guardian/children/{id}/view`（及 `/service-records`、`/training-records`，均需 `?phone=`） |
| 报名确认（代办，支持幂等键） | `POST /api/guardian/children/{id}/enrollments/confirm?phone=` |
| 权益代领（代办，支持幂等键） | `POST /api/guardian/children/{id}/benefits/claim?phone=` |
| 发起授权 / 出新版本 / 撤回 | `POST /api/admin/guardianship/authorizations`、`/authorizations/{id}/versions`、`/authorizations/{id}/revoke` |
| 按孩子撤回某监护人 | `POST /api/admin/guardianship/volunteers/{id}/guardians/revoke` |
| 监护人交接 | `POST /api/admin/guardianship/volunteers/{id}/handover` |
| 授权版本与操作审计查询 | `GET /api/admin/guardianship/authorizations`、`/operations` |
| 按时间点还原谁凭哪版授权看过/办过什么 | `GET /api/admin/guardianship/as-of?ts=YYYY-MM-DDTHH:MM:SS` |

遗留 `POST/GET /api/parents/*` 入口已统一接入同一授权引擎：撤回/过期同样立即生效，查看同样留痕。

