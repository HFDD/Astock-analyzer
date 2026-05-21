# 严重问题修复：API 响应速度优化

## 问题等级：🔴 严重

**现象：** `GET /api/stock/600519` 实测 **5.9秒**
**竞品参考：** 同类接口 < 1秒

## 已知的耗时分析

### 理论耗时拆解（基于本地测试）
| 步骤 | 本地测试 | FastAPI |
|------|---------|---------|
| get_stock_daily | ~0.01s | - |
| get_stock_info | ~0.19-1.09s | ? |
| analyze_stock | ~0.01-0.02s | ~0.02s |
| JSON serialize | ~0.01s | ? |
| **总计（无网络等待）** | **~1.3s** | **~6s** |

**结论：** FastAPI 比本地纯 Python 慢 ~4.7s，这个开销来自：
- FastAPI middleware
- 数据库操作（每次请求有 SQLite 读写）
- 可能的 uvicorn 同步模式瓶颈

### 可能的根因
1. **同步 uvicorn**：所有请求在一个线程里串行处理
2. **SQLite 锁**：每次请求要写分析记录，可能和读操作抢锁
3. **get_stock_info 无缓存**：每次请求都从 Sina/Tencent API 拉数据
4. **懒加载开销**：Python 首次 import 慢，但这个应该只影响第一次请求

## 目标
优化到 **单次请求 < 2秒**（合理目标，竞品<1s）

## 优化方向（按优先级）

### P0 必须做
1. **加数据缓存**（最重要）
   - `get_stock_info` 结果缓存 5 分钟
   - `get_stock_daily` 结果缓存 10 分钟
   - 已有 `data_fetcher.py` 模块级缓存，检查是否正常工作

2. **SQLite 连接优化**
   - 检查是否每次请求都重新连接
   - 考虑加连接池或全局单连接

### P1 应该有明显改善
3. **uvicorn 换成异步模式**
   - `uvicorn main:app --host 0.0.0.0 --port 8899 --loop uvloop`（需要安装uvloop）
   - 或至少 `--workers 2`

4. **后台数据预加载**
   - 每天开盘前（9:00前）预加载热门股信息

### P2 如果还不够
5. **gunicorn 多进程**
6. **静态文件 nginx 反向代理**

## 项目路径
`/Users/tangsaijun/.openclaw/workspace/stock-analyzer/`

## 验收标准
| 接口 | 目标 | 当前 |
|------|------|------|
| `GET /api/stock/600519` | < 2s | ~6s |
| `GET /api/watchlist` | < 0.5s | 未知 |
| `GET /api/daily-picks` | < 1s | 未知 |
| `GET /api/dragon-tiger` | < 1s | 未知 |

## 测试方法
```bash
TOKEN=$(curl -s -X POST http://localhost:8899/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"demo","password":"demo123456"}' \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["data"]["access_token"])')

# 连续3次取平均（去掉第一次的冷启动）
for i in 1 2 3; do
  curl -s -w "Time: %{time_total}s\n" \
    http://localhost:8899/api/stock/600519 \
    -H "Authorization: Bearer $TOKEN" -o /dev/null
done
```

## 输出要求
- 修改了哪些文件
- 优化前/后的响应时间对比
- 服务是否保持正常运行（无崩溃）
- 所有接口回归测试通过