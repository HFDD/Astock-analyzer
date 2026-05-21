# CONTEXT

## 项目一句话

这是一个 A 股股票分析系统：用户登录后输入股票代码，系统拉取行情和历史 K 线，计算技术指标和综合评分，并给出买卖建议。

## 以当前代码为准

- 后端：Python 3 + FastAPI
- 认证：JWT + bcrypt
- 数据库：SQLite，文件在 `data/stock.db`
- 数据源：AKShare 和外部行情接口
- 前端：`index.html` 单页应用，使用 TailwindCSS 和 ECharts
- 入口：`src/main.py`

## 主要模块

- `src/auth.py`：注册、登录、当前用户
- `src/models.py`：数据库初始化、自选股、分析历史、credits 扣减
- `src/data_fetcher.py`：股票日线和基础行情数据
- `src/analyzer.py`：指标计算和买卖建议
- `src/main.py`：FastAPI 路由和聚合逻辑

## 核心概念

- `user`：登录账户
- `credits`：分析额度，每次调用 `/api/stock/{code}` 扣 1
- `watchlist`：自选股列表
- `analysis history`：分析记录
- `stock code`：A 股代码，例如 `600519`
- `kline_data`：前端画图用的 K 线序列
- `analysis result`：包含评分、建议、指标摘要和 K 线数据的响应对象

## 主要路由

- `POST /api/auth/register`
- `POST /api/auth/login`
- `GET /api/auth/me`
- `GET /api/stock/{code}`
- `GET /api/history`
- `GET /api/watchlist`
- `POST /api/watchlist`
- `DELETE /api/watchlist/{code}`

## 规则和约束

- 所有 API 路由都以 `/api/` 开头
- 成功响应格式为 `{"data": ..., "message": "success"}`
- 错误响应格式为 `{"error": "...", "code": N}`
- JWT 放在 `Authorization: Bearer <token>`
- 密码必须使用 bcrypt 哈希
- 数据库访问要参数化，避免 SQL 注入
- `analyze_stock_api` 在 credits 不足时返回 `403`
- 当前真实实现是 FastAPI / Python，不以旧文档里出现的 Node.js / Express 描述为准

## 用词

以后写 issue、测试名、重构提案时，优先使用这里的中文术语和模块名。新增概念前先确认是不是项目里的真实术语，而不是同义词。

## 文档布局

- 根目录 `CONTEXT.md` 是唯一的全局上下文
- `docs/adr/` 用来记录持久性的架构决策
- 本仓库没有 `CONTEXT-MAP.md`，也没有多上下文拆分
