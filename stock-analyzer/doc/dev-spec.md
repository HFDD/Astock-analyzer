# 开发规范

## 代码风格
- Python: PEP 8，4空格缩进
- 前端: 2空格缩进
- 函数/变量命名: snake_case
- 类名: PascalCase

## 后端规范
- 所有API路由统一前缀 `/api/`
- 错误返回格式: `{"error": "message", "code": 400}`
- 成功返回格式: `{"data": {...}, "message": "success"}`
- JWT token 放 Header: `Authorization: Bearer <token>`
- 密码必须 bcrypt 加密，绝不存明文
- 数据库操作使用参数化查询，防SQL注入

## 前端规范
- 单页应用，所有页面在 index.html 中
- 使用 TailwindCSS CDN
- 使用 ECharts CDN
- 移动端优先响应式设计
- 中文界面，面向国内用户

## 数据库规范
- SQLite 文件: `data/stock.db`
- 所有表必须有 created_at 字段
- 外键约束启用: `PRAGMA foreign_keys = ON`
- 敏感数据（密码）只存hash

## 文件结构
```
stock-analyzer/
├── doc/
│   ├── plan.md
│   ├── dev-spec.md
│   └── lessons-learned.md
├── src/
│   ├── main.py            # FastAPI入口 + 所有路由
│   ├── models.py           # 数据库模型和操作
│   ├── auth.py             # JWT认证逻辑
│   ├── analyzer.py         # 股票分析引擎
│   ├── data_fetcher.py     # AKShare数据获取
│   ├── templates/
│   │   └── index.html      # 前端单页应用
│   └── static/             # 静态资源
├── data/                   # SQLite数据库目录
├── logs/                   # 日志目录
├── requirements.txt
└── main-log.md             # 主Agent日志
```

## 测试标准
- 后端: 每个API端点返回正确状态码和数据格式
- 前端: 页面加载正常，交互流程通畅
- 集成: 注册→登录→查询股票→查看历史 全流程通过
