## 港美股服务工具 (server_type="global_stock")

| 工具名称 | 功能说明 | 典型参数 |
|---------|---------|---------|
| `search_global_stocks` | 港美股智能选股 | `{"query": "选股条件", "market": "港股/美股"}` 如 `{"query": "汽车行业且市盈率低于50", "market": "港股"}` |
| `global_stock_profile` | 港美股基本资料与股本结构 | `{"query": "股票名称/代码+指标"}` 如 `"智谱、minimax的所属行业、上市日期与发行价"` |
| `global_stock_quotes` | 港美股行情数据与技术指标 | `{"query": "股票名称/代码+时间+指标"}` 如 `"苹果和特斯拉近10个交易日的涨跌幅、换手率"` |
| `global_stock_financial` | 港美股财务数据与估值指标 | `{"query": "股票名称/代码+指标"}` 如 `"Google和Meta在最新报告期的ROE、ROA、利润增速"` |
| `global_stock_events` | 港美股公告事件（IPO、回购、分红、ESG等） | `{"query": "股票名称/代码+事件指标"}` 如 `"minimax的IPO日期、数量、价格及保荐人"` |

### 脚本调用示例

以下示例保存于包含 `SKILL.md` 的技能根目录后执行；Python 需安装 `requests`。后续查询片段复用此处导入的函数。

```javascript
const { call } = require('./scripts/call-node.js');

async function main() {
    const result = await call("global_stock", "global_stock_quotes", {
        query: "苹果和特斯拉近10个交易日的涨跌幅、换手率"
    });
    console.log(JSON.stringify(result, null, 2));
}

main().catch(console.error);
```

```python
from scripts.call import call

result = call("global_stock", "global_stock_quotes", {"query": "苹果和特斯拉近10个交易日的涨跌幅、换手率"})
print(result)
```

### 港美股查询示例

```python
# 港美股智能选股
call("global_stock", "search_global_stocks", {"query": "汽车行业且市盈率低于50", "market": "港股"})

# 港美股基本资料
call("global_stock", "global_stock_profile", {"query": "智谱、minimax的所属行业、上市日期与发行价"})

# 港美股行情数据
call("global_stock", "global_stock_quotes", {"query": "苹果和特斯拉近10个交易日的涨跌幅、换手率"})

# 港美股财务数据
call("global_stock", "global_stock_financial", {"query": "Google和Meta在最新报告期的ROE、ROA、利润增速"})

# 港美股公告事件
call("global_stock", "global_stock_events", {"query": "minimax的IPO日期、数量、价格及保荐人"})
```

